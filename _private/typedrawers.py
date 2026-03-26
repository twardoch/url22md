#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["fire", "rich", "httpx", "tenacity"]
# ///
# this_file: typedrawers.py

"""
TypeDrawers (typedrawers.com) Vanilla Forum API v2 scraper.

Usage:
    ./typedrawers.py users          # Scrape user list pages (cached)
    ./typedrawers.py user_details   # Scrape individual user profiles (cached)
    ./typedrawers.py contacts       # Build contact JSON from scraped data
    ./typedrawers.py categories     # Scrape category listing
    ./typedrawers.py discussions    # Scrape discussion lists by category (cached)
    ./typedrawers.py comments       # Scrape comments per discussion (cached)
    ./typedrawers.py enrich         # Find email addresses via askpoe
    ./typedrawers.py all            # Run everything in sequence (cached)

    ./typedrawers.py new_users      # Fetch only NEW users since last run
    ./typedrawers.py new_all        # New users + new discussions + new comments
    ./typedrawers.py update_users   # Re-fetch ALL user profiles (overwrite)
    ./typedrawers.py update_texts   # Re-fetch categories + discussions + comments (no users)
    ./typedrawers.py update_all     # Re-fetch everything (full overwrite)
"""

import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import fire
import httpx
from rich.console import Console
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://typedrawers.com"
API_BASE = f"{BASE_URL}/api/v2"
BASE_DIR = Path(__file__).parent

RAWDATA_USERS_DIR = BASE_DIR / "rawdata" / "users"
RAWDATA_DISCUSSIONS_DIR = BASE_DIR / "rawdata" / "discussions"
RAWDATA_COMMENTS_DIR = BASE_DIR / "rawdata" / "comments"
RAWDATA_DIR = BASE_DIR / "rawdata"
DATA_DIR = BASE_DIR / "data"
STATE_PATH = RAWDATA_DIR / "state.json"
FAILED_PROFILES_PATH = RAWDATA_DIR / "failed_profiles.json"

PAGE_LIMIT = 50  # Vanilla Forum max items per page

BANDWIDTH_WARN_BYTES = 8 * 1024**3       # 8 GB
BANDWIDTH_STOP_BYTES = 9.5 * 1024**3     # 9.5 GB

console = Console()


# ---------------------------------------------------------------------------
# .env loader (minimal, no extra dependency)
# ---------------------------------------------------------------------------

def _load_dotenv() -> None:
    """Load .env file from script directory into os.environ if present."""
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


# ---------------------------------------------------------------------------
# Proxy helpers
# ---------------------------------------------------------------------------

def _build_proxy_url() -> str | None:
    """Build Webshare proxy URL from env vars, or None if not configured."""
    user = os.environ.get("WEBSHARE_PROXY_USER")
    password = os.environ.get("WEBSHARE_PROXY_PASS")
    host = os.environ.get("WEBSHARE_DOMAIN_NAME")
    port = os.environ.get("WEBSHARE_PROXY_PORT")
    if all([user, password, host, port]):
        url = f"http://{user}:{password}@{host}:{port}"
        console.print(f"[dim]Proxy configured: {host}:{port}[/dim]")
        return url
    console.print("[dim]No proxy configured — using direct connection.[/dim]")
    return None


# ---------------------------------------------------------------------------
# Retry predicate
# ---------------------------------------------------------------------------

class RateLimitError(Exception):
    """Raised on HTTP 429 so tenacity can retry."""


# ---------------------------------------------------------------------------
# Social link extraction helper
# ---------------------------------------------------------------------------

def _extract_social(text: str, patterns: tuple[str, ...]) -> str | None:
    """Very lightweight extractor: find a URL/handle in bio text."""
    if not text:
        return None
    lower = text.lower()
    for pat in patterns:
        idx = lower.find(pat)
        if idx == -1:
            continue
        start = idx + len(pat)
        end = start
        while end < len(text) and not text[end].isspace() and text[end] not in (",", ")", "]", '"', "'"):
            end += 1
        handle = text[start:end].strip().lstrip("@/")
        if handle:
            return handle
    return None


def _extract_urls(text: str) -> list[str]:
    """Extract all URLs from text."""
    if not text:
        return []
    return re.findall(r'https?://[^\s<>"\')\]]+', text)


# ---------------------------------------------------------------------------
# Scraper class
# ---------------------------------------------------------------------------

class TypeDrawersScraper:
    """Vanilla Forum API v2 scraper for typedrawers.com."""

    def __init__(self) -> None:
        _load_dotenv()
        proxy_url = _build_proxy_url()

        self._client = httpx.Client(
            proxy=proxy_url,
            timeout=30.0,
            headers={
                "User-Agent": "TypeDrawersScraper/1.0 (+research)",
                "Accept": "application/json",
            },
            follow_redirects=True,
        )
        self._bandwidth_bytes: int = 0
        self._ensure_dirs()

    # ------------------------------------------------------------------
    # Directory setup
    # ------------------------------------------------------------------

    def _ensure_dirs(self) -> None:
        for d in [RAWDATA_USERS_DIR, RAWDATA_DISCUSSIONS_DIR, RAWDATA_COMMENTS_DIR, DATA_DIR]:
            d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException, RateLimitError)),
        wait=wait_exponential(multiplier=2, min=4, max=60),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _get(self, url: str, **params: Any) -> Any:
        """GET a JSON endpoint, tracking bandwidth. Returns parsed JSON (dict or list)."""
        response = self._client.get(url, params=params if params else None)

        # Track bandwidth
        content_length = len(response.content)
        self._bandwidth_bytes += content_length
        gb_used = self._bandwidth_bytes / 1024**3
        console.print(
            f"[dim]  {url.split('?')[0]} → {response.status_code} "
            f"({content_length:,} bytes, total {gb_used:.3f} GB)[/dim]"
        )

        if self._bandwidth_bytes >= BANDWIDTH_STOP_BYTES:
            console.print(
                f"[bold red]BANDWIDTH LIMIT REACHED ({gb_used:.2f} GB). Stopping.[/bold red]"
            )
            raise SystemExit(1)
        if self._bandwidth_bytes >= BANDWIDTH_WARN_BYTES:
            console.print(
                f"[yellow]WARNING: Bandwidth approaching limit ({gb_used:.2f} GB / 10 GB)[/yellow]"
            )

        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", "10"))
            console.print(f"[yellow]Rate limited. Waiting {retry_after}s...[/yellow]")
            time.sleep(retry_after)
            raise RateLimitError("HTTP 429")

        if response.status_code in (403, 404, 410):
            return None  # Caller handles None as "not found / forbidden"

        response.raise_for_status()
        return response.json()

    def _save_json(self, path: Path, data: Any) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    def _load_json(self, path: Path) -> Any:
        return json.loads(path.read_text())

    def _check_bandwidth(self) -> bool:
        """Return False if we should stop due to bandwidth."""
        if self._bandwidth_bytes >= BANDWIDTH_STOP_BYTES:
            console.print("[bold red]Bandwidth limit reached. Aborting.[/bold red]")
            return False
        return True

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    def _load_state(self) -> dict[str, Any]:
        """Load incremental state from rawdata/state.json."""
        if STATE_PATH.exists():
            return self._load_json(STATE_PATH)
        return {
            "max_user_page": 0,
            "known_user_ids": [],
            "known_discussion_ids": [],
            "last_run": {},
        }

    def _save_state(self, state: dict[str, Any]) -> None:
        """Save incremental state to rawdata/state.json."""
        self._save_json(STATE_PATH, state)

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _flatten_categories(cats: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Flatten nested category tree into a list of leaf categories.

        The Vanilla Forum API returns categories with nested `children` arrays.
        Categories with `displayAs: "heading"` are containers — their children
        hold the actual discussions. This method recursively collects all
        categories that can contain discussions (leaf nodes + any node with
        countDiscussions > 0).
        """
        result: list[dict[str, Any]] = []

        def _walk(node_list: list[dict[str, Any]]) -> None:
            for cat in node_list:
                children = cat.get("children", [])
                if children:
                    # If this heading itself has discussions, include it
                    if cat.get("countDiscussions", 0) > 0:
                        result.append(cat)
                    _walk(children)
                else:
                    # Leaf category — always include
                    result.append(cat)

        _walk(cats)
        return result

    # ------------------------------------------------------------------
    # 1. users — paginate /api/v2/users
    # ------------------------------------------------------------------

    def users(self, force: bool = False) -> list[int]:
        """Scrape user list pages from /api/v2/users.

        Args:
            force: If True, re-fetch pages even if cached.

        Returns list of userIDs found across all pages.
        """
        console.print("[bold cyan]== Scraping user list ==[/bold cyan]")
        all_user_ids: list[int] = []
        page = 1  # Vanilla API pages are 1-indexed

        while True:
            if not self._check_bandwidth():
                break

            out_path = RAWDATA_USERS_DIR / f"users_page_{page}.json"
            if out_path.exists() and not force:
                console.print(f"[dim]  Page {page}: already cached, loading.[/dim]")
                data = self._load_json(out_path)
            else:
                console.print(f"[cyan]  Fetching user list page {page}...[/cyan]")
                data = self._get(f"{API_BASE}/users", limit=PAGE_LIMIT, page=page)

                if data is None:
                    console.print(f"[yellow]  Page {page}: 404, stopping.[/yellow]")
                    break

                self._save_json(out_path, data)

            # Vanilla API v2 returns a JSON array; empty array = no more pages
            if not isinstance(data, list) or len(data) == 0:
                console.print(f"[dim]  No users on page {page}. Done.[/dim]")
                break

            page_ids = [u["userID"] for u in data if "userID" in u]
            all_user_ids.extend(page_ids)
            console.print(
                f"  Page {page}: {len(page_ids)} users "
                f"(total so far: {len(all_user_ids)})"
            )

            # If we got fewer than PAGE_LIMIT, this is the last page
            if len(data) < PAGE_LIMIT:
                console.print(f"[dim]  Partial page ({len(data)} < {PAGE_LIMIT}). Done.[/dim]")
                break

            page += 1
            time.sleep(0.5)

        # Update state
        state = self._load_state()
        state["max_user_page"] = page
        existing_ids = set(state.get("known_user_ids", []))
        existing_ids.update(all_user_ids)
        state["known_user_ids"] = sorted(existing_ids)
        state["last_run"]["users"] = self._now_iso()
        self._save_state(state)

        console.print(f"[green]User list done. {len(all_user_ids)} userIDs collected.[/green]")
        return all_user_ids

    # ------------------------------------------------------------------
    # 2. user_details — fetch /profile/{id}/{Name}.json for each user
    # ------------------------------------------------------------------

    def user_details(self, force: bool = False) -> None:
        """Fetch individual user profiles for all users found in list pages.

        Args:
            force: If True, re-fetch profiles even if cached.
        """
        console.print("[bold cyan]== Scraping user profiles ==[/bold cyan]")

        # Collect users from cached list pages
        users_by_id: dict[int, dict[str, Any]] = {}
        for page_file in sorted(RAWDATA_USERS_DIR.glob("users_page_*.json")):
            data = self._load_json(page_file)
            if isinstance(data, list):
                for u in data:
                    uid = u.get("userID")
                    if uid:
                        users_by_id[uid] = u

        console.print(f"  Found {len(users_by_id)} unique users to fetch profiles for.")

        # Load previously failed profiles so we can append
        failed: list[dict[str, Any]] = []
        if FAILED_PROFILES_PATH.exists():
            failed = self._load_json(FAILED_PROFILES_PATH)

        failed_ids = {e["userID"] for e in failed}

        for idx, (uid, user_info) in enumerate(sorted(users_by_id.items()), 1):
            if not self._check_bandwidth():
                break

            out_path = RAWDATA_USERS_DIR / f"{uid}.json"
            if out_path.exists() and not force:
                console.print(f"[dim]  [{idx}/{len(users_by_id)}] User {uid}: cached.[/dim]")
                continue

            name = user_info.get("name", "")
            encoded_name = quote(str(name), safe="")
            url = f"{BASE_URL}/profile/{uid}/{encoded_name}.json"

            console.print(f"  [{idx}/{len(users_by_id)}] Fetching profile for {name} (ID {uid})...")
            try:
                data = self._get(url)
            except httpx.HTTPStatusError as exc:
                console.print(
                    f"[yellow]  [{idx}/{len(users_by_id)}] User {uid} ({name}): "
                    f"HTTP {exc.response.status_code}, skipping.[/yellow]"
                )
                if uid not in failed_ids:
                    failed.append({"userID": uid, "name": name, "error": exc.response.status_code})
                    failed_ids.add(uid)
                time.sleep(1.0)
                continue

            if data is None:
                console.print(
                    f"[yellow]  [{idx}/{len(users_by_id)}] User {uid} ({name}): "
                    f"not found or forbidden, skipping.[/yellow]"
                )
                if uid not in failed_ids:
                    failed.append({"userID": uid, "name": name, "error": "not_found_or_forbidden"})
                    failed_ids.add(uid)
            else:
                self._save_json(out_path, data)
                # Remove from failed list if a retry succeeded
                if uid in failed_ids:
                    failed = [e for e in failed if e["userID"] != uid]
                    failed_ids.discard(uid)

            time.sleep(1.0)

        # Persist failed profiles list
        self._save_json(FAILED_PROFILES_PATH, failed)
        if failed:
            console.print(
                f"[yellow]{len(failed)} profiles failed — saved to {FAILED_PROFILES_PATH}[/yellow]"
            )
        console.print("[green]User profiles done.[/green]")

    # ------------------------------------------------------------------
    # 3. contacts — merge user list + profiles into data/contacts.json
    # ------------------------------------------------------------------

    def contacts(self) -> None:
        """Read user list stats + profile details and produce data/contacts.json."""
        console.print("[bold cyan]== Building contacts JSON ==[/bold cyan]")

        # 1. Build stats lookup from user list pages
        list_stats: dict[int, dict[str, Any]] = {}
        for page_file in sorted(RAWDATA_USERS_DIR.glob("users_page_*.json")):
            data = self._load_json(page_file)
            if isinstance(data, list):
                for u in data:
                    uid = u.get("userID")
                    if uid:
                        list_stats[uid] = {
                            "userID": uid,
                            "name": u.get("name", ""),
                            "email": u.get("email"),
                            "photoUrl": u.get("photoUrl"),
                            "points": u.get("points", 0),
                            "dateInserted": u.get("dateInserted"),
                            "dateLastActive": u.get("dateLastActive"),
                            "countDiscussions": u.get("countDiscussions", 0),
                            "countComments": u.get("countComments", 0),
                            "countVisits": u.get("countVisits", 0),
                            "countPosts": u.get("countPosts", 0),
                            "banned": u.get("banned", 0),
                            "private": u.get("private", False),
                        }
        console.print(f"  Loaded list stats for {len(list_stats)} users.")

        # 2. Read profile files and merge
        contact_list: list[dict[str, Any]] = []
        profiled_ids: set[int] = set()

        profile_files = sorted(RAWDATA_USERS_DIR.glob("[0-9]*.json"))
        console.print(f"  Processing {len(profile_files)} user profile files...")

        for profile_file in profile_files:
            try:
                uid = int(profile_file.stem)
            except ValueError:
                continue

            data = self._load_json(profile_file)
            if not data:
                continue

            # Profile JSON may have a "Profile" wrapper or be flat
            profile = data.get("Profile", data) if isinstance(data, dict) else data

            profiled_ids.add(uid)

            # Get corresponding list data
            ls = list_stats.get(uid, {})

            # Extract bio/about text
            about = profile.get("About", "") or profile.get("about", "") or ""

            # Extract social links from About
            twitter = _extract_social(about, ("twitter.com/", "x.com/", "@"))
            github = _extract_social(about, ("github.com/",))
            instagram = _extract_social(about, ("instagram.com/",))

            # Extract website from About URLs if not in profile
            website = profile.get("Website") or profile.get("website") or ""
            if not website:
                urls = _extract_urls(about)
                # Pick first URL that isn't a social platform
                for u in urls:
                    lower_u = u.lower()
                    if not any(s in lower_u for s in ["twitter.com", "x.com", "github.com", "instagram.com", "facebook.com"]):
                        website = u
                        break

            username = profile.get("Name") or profile.get("name") or ls.get("name", "")

            contact = {
                "userID": uid,
                "username": username,
                "title": profile.get("Title") or profile.get("title"),
                "website": website or None,
                "location": profile.get("Location") or profile.get("location"),
                "about": about[:500] if about else None,
                "photoUrl": profile.get("Photo") or profile.get("photoUrl") or ls.get("photoUrl"),
                # Activity stats — prefer list data (more reliable), fall back to profile
                "countPosts": ls.get("countPosts") or profile.get("CountPosts") or profile.get("countPosts", 0),
                "countComments": ls.get("countComments") or profile.get("CountComments") or profile.get("countComments", 0),
                "countDiscussions": ls.get("countDiscussions") or profile.get("CountDiscussions") or profile.get("countDiscussions", 0),
                "countVisits": ls.get("countVisits") or profile.get("CountVisits") or profile.get("countVisits", 0),
                "points": ls.get("points") or profile.get("Points") or profile.get("points", 0),
                # Dates
                "dateInserted": ls.get("dateInserted") or profile.get("DateFirstVisit") or profile.get("dateInserted"),
                "dateLastActive": ls.get("dateLastActive") or profile.get("DateLastActive") or profile.get("dateLastActive"),
                # Social
                "twitter": twitter,
                "github": github,
                "instagram": instagram,
                # Email from list if present
                "email": ls.get("email"),
                # Roles from profile
                "roles": [r.get("Name", r.get("name", "")) for r in (profile.get("UserRoles") or profile.get("roles") or [])],
            }
            contact_list.append(contact)

        # 3. Add users from list that have no profile file
        for uid, ls in list_stats.items():
            if uid not in profiled_ids:
                contact_list.append({
                    "userID": uid,
                    "username": ls.get("name", ""),
                    "title": None,
                    "website": None,
                    "location": None,
                    "about": None,
                    "photoUrl": ls.get("photoUrl"),
                    "countPosts": ls.get("countPosts", 0),
                    "countComments": ls.get("countComments", 0),
                    "countDiscussions": ls.get("countDiscussions", 0),
                    "countVisits": ls.get("countVisits", 0),
                    "points": ls.get("points", 0),
                    "dateInserted": ls.get("dateInserted"),
                    "dateLastActive": ls.get("dateLastActive"),
                    "twitter": None,
                    "github": None,
                    "instagram": None,
                    "email": ls.get("email"),
                    "roles": [],
                })

        # Sort by countPosts descending
        contact_list.sort(key=lambda c: c.get("countPosts") or 0, reverse=True)

        out_path = DATA_DIR / "contacts.json"
        out_path.write_text(json.dumps(contact_list, ensure_ascii=False, indent=2))
        console.print(f"[green]Contacts done. {len(contact_list)} contacts saved to {out_path}.[/green]")

    # ------------------------------------------------------------------
    # 4. categories — fetch /api/v2/categories
    # ------------------------------------------------------------------

    def categories(self, force: bool = False) -> list[dict[str, Any]]:
        """Fetch all categories and save to rawdata/categories.json.

        Args:
            force: If True, re-fetch even if cached.

        Returns list of category dicts.
        """
        console.print("[bold cyan]== Fetching categories ==[/bold cyan]")

        out_path = RAWDATA_DIR / "categories.json"
        if out_path.exists() and not force:
            console.print("[dim]  Categories: cached, loading.[/dim]")
            data = self._load_json(out_path)
        else:
            data = self._get(f"{API_BASE}/categories")
            if data is None:
                console.print("[red]  Failed to fetch categories.[/red]")
                return []
            self._save_json(out_path, data)

        cats = data if isinstance(data, list) else []
        console.print(f"[green]Categories done. {len(cats)} categories.[/green]")
        return cats

    # ------------------------------------------------------------------
    # 5. discussions — paginate discussions per category
    # ------------------------------------------------------------------

    def discussions(self, force: bool = False) -> None:
        """Scrape discussion listings for all categories.

        Args:
            force: If True, re-fetch pages even if cached.
        """
        console.print("[bold cyan]== Scraping discussion lists ==[/bold cyan]")

        # Ensure categories are fetched
        cats_path = RAWDATA_DIR / "categories.json"
        if not cats_path.exists():
            self.categories()
        raw_cats = self._load_json(cats_path)
        if not isinstance(raw_cats, list):
            console.print("[red]  No categories found. Run `categories` first.[/red]")
            return

        cats = self._flatten_categories(raw_cats)
        console.print(f"  {len(cats)} categories to process (flattened from tree).")

        discussion_index: list[dict[str, Any]] = []
        all_disc_ids: set[int] = set()

        for cat in cats:
            if not self._check_bandwidth():
                break

            cat_id = cat.get("categoryID")
            cat_name = cat.get("name", "")
            console.print(f"  Category: [{cat_id}] {cat_name}")

            page = 1
            while True:
                if not self._check_bandwidth():
                    break

                out_path = RAWDATA_DISCUSSIONS_DIR / f"category_{cat_id}_page_{page}.json"
                if out_path.exists() and not force:
                    console.print(f"[dim]    Page {page}: cached.[/dim]")
                    data = self._load_json(out_path)
                else:
                    console.print(f"    Fetching page {page}...")
                    data = self._get(
                        f"{API_BASE}/discussions",
                        categoryID=cat_id,
                        limit=PAGE_LIMIT,
                        page=page,
                        sort="-dateInserted",
                    )
                    if data is None:
                        console.print(f"[yellow]    404 for category {cat_id} page {page}.[/yellow]")
                        break
                    self._save_json(out_path, data)

                if not isinstance(data, list) or len(data) == 0:
                    console.print(f"[dim]    No discussions on page {page}. Done with category.[/dim]")
                    break

                for d in data:
                    disc_id = d.get("discussionID")
                    if disc_id and disc_id not in all_disc_ids:
                        all_disc_ids.add(disc_id)
                        discussion_index.append({
                            "discussionID": disc_id,
                            "name": d.get("name"),
                            "categoryID": cat_id,
                            "categoryName": cat_name,
                            "dateInserted": d.get("dateInserted"),
                            "dateLastComment": d.get("dateLastComment"),
                            "countComments": d.get("countComments", 0),
                            "countViews": d.get("countViews", 0),
                            "score": d.get("score", 0),
                            "closed": d.get("closed", False),
                            "insertUserID": d.get("insertUserID"),
                        })

                console.print(
                    f"    Page {page}: {len(data)} discussions "
                    f"(index total: {len(discussion_index)})"
                )

                if len(data) < PAGE_LIMIT:
                    break
                page += 1
                time.sleep(0.5)

        # Save index
        index_path = RAWDATA_DISCUSSIONS_DIR / "index.json"
        self._save_json(index_path, {"discussions": discussion_index})

        # Update state
        state = self._load_state()
        state["known_discussion_ids"] = sorted(all_disc_ids)
        state["last_run"]["discussions"] = self._now_iso()
        self._save_state(state)

        console.print(
            f"[green]Discussions done. {len(discussion_index)} discussions indexed. "
            f"Saved to {index_path}.[/green]"
        )

    # ------------------------------------------------------------------
    # 6. comments — fetch comments per discussion
    # ------------------------------------------------------------------

    def comments(self, force: bool = False) -> None:
        """Fetch all comments for each discussion in the index.

        Args:
            force: If True, re-fetch comments even if cached.
        """
        console.print("[bold cyan]== Scraping comments ==[/bold cyan]")

        index_path = RAWDATA_DISCUSSIONS_DIR / "index.json"
        if not index_path.exists():
            console.print("[red]No discussion index found. Run `discussions` first.[/red]")
            return

        index_data = self._load_json(index_path)
        disc_list = index_data.get("discussions", [])
        console.print(f"  {len(disc_list)} discussions to fetch comments for.")

        for idx, disc in enumerate(disc_list, 1):
            if not self._check_bandwidth():
                break

            disc_id = disc.get("discussionID")
            if not disc_id:
                continue

            out_path = RAWDATA_COMMENTS_DIR / f"{disc_id}.json"
            if out_path.exists() and not force:
                console.print(f"[dim]  [{idx}/{len(disc_list)}] Discussion {disc_id}: cached.[/dim]")
                continue

            disc_name = (disc.get("name") or "")[:60]
            console.print(f"  [{idx}/{len(disc_list)}] Fetching comments for {disc_id}: {disc_name}...")

            all_comments: list[dict[str, Any]] = []
            page = 1

            while True:
                if not self._check_bandwidth():
                    break

                data = self._get(
                    f"{API_BASE}/comments",
                    discussionID=disc_id,
                    limit=PAGE_LIMIT,
                    page=page,
                )

                if data is None or not isinstance(data, list) or len(data) == 0:
                    break

                all_comments.extend(data)

                if len(data) < PAGE_LIMIT:
                    break
                page += 1
                time.sleep(0.5)

            self._save_json(out_path, all_comments)
            console.print(
                f"    → {len(all_comments)} comments saved."
            )
            time.sleep(1.0)

        console.print("[green]Comments done.[/green]")

    # ------------------------------------------------------------------
    # 7. enrich — email lookup via askpoe
    # ------------------------------------------------------------------

    def enrich(self, limit: int = 100, model: str = "web-search") -> None:
        """Find email addresses for top contacts using askpoe web search.

        Args:
            limit: Max number of contacts to enrich (default 100, most active first).
            model: askpoe model to use (default 'web-search', cheapest at 0 points).
        """
        console.print("[bold cyan]== Enriching contacts with email lookup ==[/bold cyan]")

        contacts_path = DATA_DIR / "contacts.json"
        if not contacts_path.exists():
            console.print("[red]No contacts.json found. Run `contacts` first.[/red]")
            return

        all_contacts = json.loads(contacts_path.read_text())
        enriched_dir = DATA_DIR / "enriched"
        enriched_dir.mkdir(parents=True, exist_ok=True)

        # Focus on contacts that have identifying info but no email
        candidates = [
            c for c in all_contacts
            if (c.get("countPosts") or 0) > 0
            and not c.get("email")
            and (c.get("username") or c.get("website") or c.get("twitter") or c.get("github"))
        ][:limit]

        console.print(f"  {len(candidates)} contacts to enrich (top {limit} by activity).")

        results: list[dict[str, Any]] = []
        for idx, contact in enumerate(candidates, 1):
            username = contact.get("username", "")
            cache_path = enriched_dir / f"{username}.json"

            if cache_path.exists():
                console.print(f"[dim]  [{idx}/{len(candidates)}] {username}: cached.[/dim]")
                cached = json.loads(cache_path.read_text())
                results.append(cached)
                continue

            # Build search query from available info
            parts = []
            if contact.get("username"):
                parts.append(contact["username"])
            if contact.get("website"):
                parts.append(contact["website"])
            if contact.get("twitter"):
                parts.append(f"twitter @{contact['twitter']}")
            if contact.get("github"):
                parts.append(f"github {contact['github']}")
            if contact.get("location"):
                parts.append(contact["location"])
            parts.append("type designer font")

            query = " ".join(parts)
            prompt = (
                f"Find the email address for this person: {query}. "
                f"They are a font/type designer active on TypeDrawers as '{username}'. "
                f"Return ONLY the email address if found, or 'NOT FOUND' if not."
            )

            console.print(
                f"  [{idx}/{len(candidates)}] Searching for {username} "
                f"({contact.get('title', '')})..."
            )

            try:
                result = subprocess.run(
                    ["askpoe", prompt, "-m", model],
                    capture_output=True, text=True, timeout=30,
                )
                answer = result.stdout.strip()
            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                console.print(f"[yellow]  askpoe error: {e}[/yellow]")
                answer = "ERROR"

            enriched = {**contact, "email_lookup": answer}
            cache_path.write_text(json.dumps(enriched, ensure_ascii=False, indent=2))
            results.append(enriched)

            time.sleep(0.5)

        # Update contacts.json with enrichment results
        email_map: dict[str, str] = {}
        for r in results:
            lookup = r.get("email_lookup", "")
            if lookup and "NOT FOUND" not in lookup.upper() and "ERROR" not in lookup.upper() and "@" in lookup:
                for word in lookup.split():
                    if "@" in word and "." in word:
                        email_map[r.get("username", "")] = word.strip(".,;:!?<>()\"'")
                        break

        if email_map:
            for contact in all_contacts:
                if contact.get("username") in email_map:
                    contact["email"] = email_map[contact["username"]]
            contacts_path.write_text(json.dumps(all_contacts, ensure_ascii=False, indent=2))
            console.print(f"[green]Enrichment done. Found {len(email_map)} email addresses.[/green]")
        else:
            console.print("[yellow]Enrichment done. No new emails found.[/yellow]")

    # ------------------------------------------------------------------
    # 8. all — run everything in sequence (cached)
    # ------------------------------------------------------------------

    def all(self) -> None:
        """Run all scraping steps using cache: users → user_details → contacts → categories → discussions → comments."""
        self._run_pipeline(force_listings=False, force_details=False)

    # ------------------------------------------------------------------
    # 9. new_users — incremental user fetch
    # ------------------------------------------------------------------

    def new_users(self) -> None:
        """Fetch only NEW users since last run, then rebuild contacts."""
        console.print("[bold green]== Fetching new users ==[/bold green]")

        state = self._load_state()
        start_page = max(1, state.get("max_user_page", 1))
        known_ids = set(state.get("known_user_ids", []))

        console.print(
            f"  Resuming from page {start_page}, "
            f"{len(known_ids)} known user IDs."
        )

        new_user_ids: list[int] = []
        page = start_page

        while True:
            if not self._check_bandwidth():
                break

            out_path = RAWDATA_USERS_DIR / f"users_page_{page}.json"

            # Always re-fetch start_page (may have been partial) and subsequent
            console.print(f"[cyan]  Fetching user list page {page}...[/cyan]")
            data = self._get(f"{API_BASE}/users", limit=PAGE_LIMIT, page=page)

            if data is None:
                console.print(f"[yellow]  Page {page}: 404, stopping.[/yellow]")
                break

            self._save_json(out_path, data)

            if not isinstance(data, list) or len(data) == 0:
                console.print(f"[dim]  No users on page {page}. Done.[/dim]")
                break

            page_new = [u["userID"] for u in data if u.get("userID") and u["userID"] not in known_ids]
            new_user_ids.extend(page_new)

            console.print(
                f"  Page {page}: {len(data)} users, {len(page_new)} new "
                f"(total new: {len(new_user_ids)})"
            )

            if len(data) < PAGE_LIMIT:
                break
            page += 1
            time.sleep(0.5)

        # Fetch profiles for new users only
        if new_user_ids:
            console.print(f"  Fetching profiles for {len(new_user_ids)} new users...")
            # We need the name for each new user to build the profile URL
            # Re-read the pages we just saved to get name mappings
            name_map: dict[int, str] = {}
            for page_file in sorted(RAWDATA_USERS_DIR.glob("users_page_*.json")):
                pdata = self._load_json(page_file)
                if isinstance(pdata, list):
                    for u in pdata:
                        uid = u.get("userID")
                        if uid in new_user_ids:
                            name_map[uid] = u.get("name", "")

            for idx, uid in enumerate(new_user_ids, 1):
                if not self._check_bandwidth():
                    break

                out_path = RAWDATA_USERS_DIR / f"{uid}.json"
                if out_path.exists():
                    continue

                name = name_map.get(uid, "")
                encoded_name = quote(str(name), safe="")
                url = f"{BASE_URL}/profile/{uid}/{encoded_name}.json"

                console.print(f"  [{idx}/{len(new_user_ids)}] Fetching profile for {name} (ID {uid})...")
                try:
                    pdata = self._get(url)
                except httpx.HTTPStatusError as exc:
                    console.print(
                        f"[yellow]  [{idx}/{len(new_user_ids)}] User {uid} ({name}): "
                        f"HTTP {exc.response.status_code}, skipping.[/yellow]"
                    )
                    time.sleep(1.0)
                    continue
                if pdata is not None:
                    self._save_json(out_path, pdata)
                else:
                    console.print(
                        f"[yellow]  [{idx}/{len(new_user_ids)}] User {uid} ({name}): "
                        f"not found or forbidden, skipping.[/yellow]"
                    )
                time.sleep(1.0)
        else:
            console.print("  No new users found.")

        # Update state
        state["max_user_page"] = page
        known_ids.update(new_user_ids)
        state["known_user_ids"] = sorted(known_ids)
        state["last_run"]["users"] = self._now_iso()
        self._save_state(state)

        # Rebuild contacts
        self.contacts()

        console.print(f"[green]New users done. {len(new_user_ids)} new users added.[/green]")

    # ------------------------------------------------------------------
    # 10. new_all — incremental everything
    # ------------------------------------------------------------------

    def new_all(self) -> None:
        """Fetch new users + new discussions + comments for new discussions."""
        console.print("[bold green]== Fetching all new content ==[/bold green]")

        # Step 1: New users
        self.new_users()

        # Step 2: New discussions — fetch newest first, stop at known IDs
        state = self._load_state()
        known_disc_ids = set(state.get("known_discussion_ids", []))

        console.print(f"  {len(known_disc_ids)} known discussion IDs.")

        # Ensure categories exist
        cats_path = RAWDATA_DIR / "categories.json"
        if not cats_path.exists():
            self.categories()
        raw_cats = self._load_json(cats_path)
        if not isinstance(raw_cats, list):
            raw_cats = []

        cats = self._flatten_categories(raw_cats)
        new_disc_ids: list[int] = []
        new_discussion_index: list[dict[str, Any]] = []

        for cat in cats:
            if not self._check_bandwidth():
                break

            cat_id = cat.get("categoryID")
            cat_name = cat.get("name", "")
            console.print(f"  Category: [{cat_id}] {cat_name}")
            hit_known = False
            page = 1

            while not hit_known:
                if not self._check_bandwidth():
                    break

                data = self._get(
                    f"{API_BASE}/discussions",
                    categoryID=cat_id,
                    limit=PAGE_LIMIT,
                    page=page,
                    sort="-dateInserted",
                )

                if data is None or not isinstance(data, list) or len(data) == 0:
                    break

                # Save the page (overwrite — it may contain new items)
                out_path = RAWDATA_DISCUSSIONS_DIR / f"category_{cat_id}_page_{page}.json"
                self._save_json(out_path, data)

                for d in data:
                    disc_id = d.get("discussionID")
                    if not disc_id:
                        continue
                    if disc_id in known_disc_ids:
                        hit_known = True
                        console.print(
                            f"    Hit known discussion {disc_id} on page {page}. "
                            f"Stopping category."
                        )
                        break
                    new_disc_ids.append(disc_id)
                    new_discussion_index.append({
                        "discussionID": disc_id,
                        "name": d.get("name"),
                        "categoryID": cat_id,
                        "categoryName": cat_name,
                        "dateInserted": d.get("dateInserted"),
                        "dateLastComment": d.get("dateLastComment"),
                        "countComments": d.get("countComments", 0),
                        "countViews": d.get("countViews", 0),
                        "score": d.get("score", 0),
                        "closed": d.get("closed", False),
                        "insertUserID": d.get("insertUserID"),
                    })

                if len(data) < PAGE_LIMIT:
                    break
                page += 1
                time.sleep(0.5)

        console.print(f"  Found {len(new_disc_ids)} new discussions.")

        # Update the discussion index — merge new into existing
        index_path = RAWDATA_DISCUSSIONS_DIR / "index.json"
        if index_path.exists():
            existing_index = self._load_json(index_path).get("discussions", [])
        else:
            existing_index = []

        existing_disc_id_set = {d["discussionID"] for d in existing_index if "discussionID" in d}
        for nd in new_discussion_index:
            if nd["discussionID"] not in existing_disc_id_set:
                existing_index.append(nd)
        self._save_json(index_path, {"discussions": existing_index})

        # Step 3: Fetch comments for new discussions only
        if new_disc_ids:
            console.print(f"  Fetching comments for {len(new_disc_ids)} new discussions...")
            for idx, disc_id in enumerate(new_disc_ids, 1):
                if not self._check_bandwidth():
                    break

                out_path = RAWDATA_COMMENTS_DIR / f"{disc_id}.json"
                if out_path.exists():
                    continue

                console.print(f"  [{idx}/{len(new_disc_ids)}] Comments for discussion {disc_id}...")
                all_comments: list[dict[str, Any]] = []
                cpage = 1
                while True:
                    cdata = self._get(
                        f"{API_BASE}/comments",
                        discussionID=disc_id,
                        limit=PAGE_LIMIT,
                        page=cpage,
                    )
                    if cdata is None or not isinstance(cdata, list) or len(cdata) == 0:
                        break
                    all_comments.extend(cdata)
                    if len(cdata) < PAGE_LIMIT:
                        break
                    cpage += 1
                    time.sleep(0.5)

                self._save_json(out_path, all_comments)
                time.sleep(1.0)

        # Update state
        known_disc_ids.update(new_disc_ids)
        state["known_discussion_ids"] = sorted(known_disc_ids)
        state["last_run"]["discussions"] = self._now_iso()
        self._save_state(state)

        console.print(f"[green]New all done. {len(new_disc_ids)} new discussions added.[/green]")

    # ------------------------------------------------------------------
    # 11. update_users — re-fetch ALL user detail profiles
    # ------------------------------------------------------------------

    def update_users(self) -> None:
        """Re-fetch ALL user detail profiles (overwrite), then rebuild contacts."""
        console.print("[bold green]== Updating all user profiles ==[/bold green]")
        self.users(force=True)
        self.user_details(force=True)
        self.contacts()

    # ------------------------------------------------------------------
    # 12. update_all — full overwrite re-fetch
    # ------------------------------------------------------------------

    def update_texts(self) -> None:
        """Re-fetch categories, discussions, and comments (skip users)."""
        console.print("[bold green]== Updating text content (no users) ==[/bold green]")

        steps: list[tuple[str, Any]] = [
            ("categories", lambda: self.categories(force=True)),
            ("discussions", lambda: self.discussions(force=True)),
            ("comments", lambda: self.comments(force=True)),
        ]

        for name, step_fn in steps:
            if not self._check_bandwidth():
                console.print(f"[red]Bandwidth limit hit before step '{name}'. Stopping.[/red]")
                break
            console.print(f"\n[bold]--- Step: {name} ---[/bold]")
            step_fn()

        gb_used = self._bandwidth_bytes / 1024**3
        console.print(
            f"\n[bold green]update_texts complete. Total bandwidth used: {gb_used:.3f} GB[/bold green]"
        )

    def update_all(self) -> None:
        """Re-fetch everything: user pages, all profiles, all discussion pages, all comments."""
        self._run_pipeline(force_listings=True, force_details=True)

    # ------------------------------------------------------------------
    # Pipeline helper
    # ------------------------------------------------------------------

    def _run_pipeline(self, force_listings: bool, force_details: bool) -> None:
        """Run full pipeline with configurable force flags."""
        console.print("[bold green]== Running full scrape pipeline ==[/bold green]")

        steps: list[tuple[str, Any]] = [
            ("users", lambda: self.users(force=force_listings)),
            ("user_details", lambda: self.user_details(force=force_details)),
            ("contacts", self.contacts),
            ("categories", lambda: self.categories(force=force_listings)),
            ("discussions", lambda: self.discussions(force=force_listings)),
            ("comments", lambda: self.comments(force=force_details)),
        ]

        for name, step_fn in steps:
            if not self._check_bandwidth():
                console.print(f"[red]Bandwidth limit hit before step '{name}'. Stopping.[/red]")
                break
            console.print(f"\n[bold]--- Step: {name} ---[/bold]")
            step_fn()

        gb_used = self._bandwidth_bytes / 1024**3
        console.print(
            f"\n[bold green]Pipeline complete. Total bandwidth used: {gb_used:.3f} GB[/bold green]"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    fire.Fire(TypeDrawersScraper)
