"""Utility functions for the url22md package."""
# this_file: src/url22md/utils.py

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from loguru import logger
from pathvalidate import sanitize_filename
from slugify import slugify


def url2filename(url: str) -> str:
    """Generate a filesystem-safe filename from a URL."""
    urlparsed = urlparse(url)
    clean = ""
    for part in [urlparsed.netloc, urlparsed.path, urlparsed.params, urlparsed.query, urlparsed.fragment]:
        if part:
            clean = f"{clean}-{part}"
    return sanitize_filename(slugify(clean), replacement_text="-")


def build_proxy_url(use_proxy: bool = False) -> str | None:
    """Build a Webshare proxy URL from environment variables.

    Returns http://{user}:{password}@{host}:{port} if use_proxy is True
    and all required env vars are present, otherwise None.
    """
    if not use_proxy:
        return None

    user = os.environ.get("WEBSHARE_PROXY_USER")
    password = os.environ.get("WEBSHARE_PROXY_PASS")
    host = os.environ.get("WEBSHARE_DOMAIN_NAME")
    port = os.environ.get("WEBSHARE_PROXY_PORT")

    if not all([user, password, host, port]):
        logger.warning("use_proxy=True but one or more WEBSHARE_* env vars are missing; skipping proxy.")
        return None

    return f"http://{user}:{password}@{host}:{port}"


def setup_logging(verbose: bool = False) -> None:
    """Configure loguru logging.

    Sets DEBUG level when verbose=True, WARNING otherwise.
    """
    logger.remove()
    level = "DEBUG" if verbose else "WARNING"
    logger.add(sys.stderr, level=level)


def read_jsonl_report(path: Path) -> dict[str, dict]:
    """Read an existing JSONL report file, returning a dict keyed by URL.

    Lines that are not valid JSON or lack a 'url' key are skipped silently.
    Returns an empty dict if the file does not exist.
    """
    records: dict[str, dict] = {}
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                logger.debug(f"Skipping invalid JSON line in {path}")
                continue
            url = record.get("url")
            if url:
                records[url] = record
    return records


def append_jsonl_record(path: Path, record: dict) -> None:
    """Append a single JSON record as a line to a JSONL file, flushing immediately."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        fh.flush()


def read_urls_input(url: str | None, urls_path: str | None) -> list[str]:
    """Read URLs from --url, --urls_path, or stdin.

    Deduplicates and strips whitespace. Filters to http/https only.
    """
    raw: list[str] = []

    if url:
        raw.append(url)

    if urls_path:
        p = Path(urls_path)
        if p.exists():
            raw.extend(p.read_text(encoding="utf-8").splitlines())
        else:
            logger.warning(f"urls_path does not exist: {urls_path}")

    if not url and not urls_path and not sys.stdin.isatty():
        raw.extend(sys.stdin.read().splitlines())

    seen: set[str] = set()
    result: list[str] = []
    for u in raw:
        u = u.strip()
        if not u:
            continue
        parsed = urlparse(u)
        if parsed.scheme not in ("http", "https"):
            logger.debug(f"Skipping non-http/https URL: {u}")
            continue
        if u not in seen:
            seen.add(u)
            result.append(u)

    return result
