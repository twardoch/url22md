"""Extraction tool implementations for converting URLs to Markdown.

Nine tool slots (7 distinct engines) with directed fallback chains:
1. trafilatura           → fallback to 5
2. trafilatura (strict)  → no fallback
3. readability + md      → fallback to 5
4. readability (strict)  → no fallback
5. playwright + md       → fallback to 6
6. firecrawl (cloud)     → fallback to 7
7. Jina Reader (cloud)   → fallback to 2
8. crawl4ai (anti-bot)   → fallback to 6
9. crawl4ai (fit md)     → fallback to 5
"""
# this_file: src/url22md/tools.py

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass

from loguru import logger


def _readability_to_markdown(html_content: str) -> str:
    """Pass HTML through readability-lxml for article extraction, then markdownify."""
    from markdownify import markdownify as html2md
    from readability import Document

    doc = Document(html_content)
    title = doc.title() or ""
    cleaned = doc.summary()
    md = html2md(cleaned, heading_style="ATX", strip=["script", "style"])
    if title and md and not md.strip().startswith("#"):
        md = f"# {title}\n\n{md}"
    return md


@dataclass
class ToolResult:
    """Result returned by every extraction tool."""

    markdown: str
    tool_name: str
    success: bool
    error: str | None = None
    quality_score: float = 0.0  # 0.0 to 1.0


def assess_quality(markdown: str) -> float:
    """Score markdown quality from 0.0 to 1.0 based on prose content, not boilerplate.

    Detects and penalises CSS, JS config, and other non-prose content that tools
    sometimes return instead of actual article text.
    """
    if not markdown or not markdown.strip():
        return 0.0
    text = markdown.strip()
    score = 0.0

    # --- Prose word count (words outside code fences and obvious code lines) ---
    # Strip fenced code blocks
    stripped = re.sub(r"```[\s\S]*?```", "", text)
    # Strip lines that look like code/config (braces, @-rules, semicolons, etc.)
    prose_lines = [
        line for line in stripped.split("\n")
        if not re.match(r"^\s*[{}@]|^\s*\S+\s*[{};:]|^\s*//|^\s*/\*|^\s*\*", line)
    ]
    prose_text = " ".join(prose_lines)
    word_count = len(re.findall(r"[a-zA-Z]{2,}", prose_text))

    # Prose word count is the primary quality signal
    if word_count >= 200:
        score += 0.4
    elif word_count >= 100:
        score += 0.3
    elif word_count >= 30:
        score += 0.15
    else:
        # Very few prose words — almost certainly garbage
        return 0.0

    # --- Sentence detection (periods, question marks, exclamation marks) ---
    sentences = len(re.findall(r"[.!?]\s", prose_text))
    if sentences >= 3:
        score += 0.15
    elif sentences >= 1:
        score += 0.05

    # Has headings
    if any(line.startswith("#") for line in text.split("\n")):
        score += 0.15

    # Has paragraphs (multiple blank-line-separated blocks)
    if text.count("\n\n") >= 2:
        score += 0.1

    # Has markdown links
    if "[" in text and "](" in text:
        score += 0.1

    # --- Penalties ---

    # Residual HTML tags
    html_tags = len(re.findall(r"<[^>]+>", text))
    if html_tags > 20:
        score -= 0.3
    elif html_tags > 10:
        score -= 0.15

    # CSS/JS boilerplate patterns
    code_patterns = len(re.findall(
        r"@tailwind|@layer|@media|@keyframes|@import|tailwind\.config"
        r"|\.classList\.|document\.|window\.|console\.|function\s*\("
        r"|module\.exports|require\(|import\s+\{",
        text,
    ))
    if code_patterns >= 3:
        score -= 0.3
    elif code_patterns >= 1:
        score -= 0.1

    # Excessive braces (JSON/JS/CSS)
    brace_count = text.count("{") + text.count("}")
    if brace_count > 20:
        score -= 0.2
    elif brace_count > 10:
        score -= 0.1

    # Repetitive short lines (nav menus, link lists, layout fragments)
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    short_lines = sum(1 for line in lines if len(line) < 30)
    if lines and short_lines / len(lines) > 0.7:
        score -= 0.2

    # Low prose-to-total ratio (layout-heavy extraction)
    total_chars = len(text)
    prose_chars = len(prose_text.strip())
    if total_chars > 200 and prose_chars / total_chars < 0.3:
        score -= 0.2

    # Framework/CMS class-name noise (Webflow, WordPress, etc.)
    class_noise = len(re.findall(
        r"w-\w+|wp-\w+|wf-\w+|webflow|elementor|shopify"
        r"|data-\w+|aria-\w+|class=",
        text, re.IGNORECASE,
    ))
    if class_noise >= 5:
        score -= 0.2

    return max(0.0, min(1.0, score))


# ---------------------------------------------------------------------------
# Tool: trafilatura (used by tools 1 and 2)
# ---------------------------------------------------------------------------


async def extract_with_trafilatura(
    url: str, proxy_url: str | None = None, timeout: int = 30, minify: bool = False,
) -> ToolResult:
    """Extract markdown using trafilatura (fast, no JS rendering).

    When *minify* is True, trafilatura fetches the HTML and then readability-lxml
    + markdownify handle the conversion (article-only extraction).
    """
    tool = "trafilatura"
    try:

        def _fetch_and_extract() -> str:
            import trafilatura
            import trafilatura.downloads

            old_proxy = trafilatura.downloads.PROXY_URL
            if proxy_url:
                trafilatura.downloads.PROXY_URL = proxy_url
                trafilatura.downloads.HTTP_POOL = None
                trafilatura.downloads.NO_CERT_POOL = None
                trafilatura.downloads.RETRY_STRATEGY = None

            try:
                downloaded = trafilatura.fetch_url(url)
                if not downloaded:
                    raise RuntimeError(f"trafilatura.fetch_url returned None for {url}")

                if minify:
                    return _readability_to_markdown(downloaded)

                result = trafilatura.extract(
                    downloaded,
                    output_format="markdown",
                    include_links=True,
                    include_images=True,
                    include_tables=True,
                )
                if not result:
                    raise RuntimeError("trafilatura.extract returned empty result")
                return result
            finally:
                if proxy_url:
                    trafilatura.downloads.PROXY_URL = old_proxy
                    trafilatura.downloads.HTTP_POOL = None
                    trafilatura.downloads.NO_CERT_POOL = None
                    trafilatura.downloads.RETRY_STRATEGY = None

        logger.debug("trafilatura: fetching {} (minify={})", url, minify)
        md = await asyncio.to_thread(_fetch_and_extract)
        quality = assess_quality(md)
        logger.debug("trafilatura: got {} chars, quality={:.2f}", len(md), quality)
        return ToolResult(markdown=md, tool_name=tool, success=True, quality_score=quality)
    except Exception as e:
        logger.warning("trafilatura failed for {}: {}", url, e)
        return ToolResult(markdown="", tool_name=tool, success=False, error=str(e))


# ---------------------------------------------------------------------------
# Tool: readability-lxml + markdownify (used by tools 3 and 4)
# ---------------------------------------------------------------------------


async def extract_with_readability(
    url: str, proxy_url: str | None = None, timeout: int = 30, **kwargs,
) -> ToolResult:
    """Extract markdown using readability-lxml + markdownify (article-focused)."""
    tool = "readability"
    try:
        import httpx

        logger.debug("readability: fetching {}", url)
        async with httpx.AsyncClient(proxy=proxy_url, timeout=timeout) as client:
            response = await client.get(url, follow_redirects=True)
            response.raise_for_status()
            html = response.text

        md = await asyncio.to_thread(_readability_to_markdown, html)

        if not md or not md.strip():
            raise RuntimeError("readability + markdownify produced empty output")

        quality = assess_quality(md)
        logger.debug("readability: got {} chars, quality={:.2f}", len(md), quality)
        return ToolResult(markdown=md, tool_name=tool, success=True, quality_score=quality)
    except Exception as e:
        logger.warning("readability failed for {}: {}", url, e)
        return ToolResult(markdown="", tool_name=tool, success=False, error=str(e))


# ---------------------------------------------------------------------------
# Tool: playwright + markdownify (tool 5)
# ---------------------------------------------------------------------------


async def extract_with_playwright(
    url: str, proxy_url: str | None = None, timeout: int = 30, minify: bool = False,
) -> ToolResult:
    """Extract markdown using Playwright (real browser) + markdownify.

    When *minify* is True, the HTML is passed through readability-lxml for
    article extraction before markdownify conversion.
    """
    tool = "playwright"
    try:
        from playwright.async_api import async_playwright

        launch_args: dict = {"headless": True}
        if proxy_url:
            launch_args["proxy"] = {"server": proxy_url}

        logger.debug("playwright: navigating to {} (minify={})", url, minify)
        async with async_playwright() as p:
            browser = await p.chromium.launch(**launch_args)
            try:
                page = await browser.new_page()
                await page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
                html = await page.content()
            finally:
                await browser.close()

        if minify:
            md = await asyncio.to_thread(_readability_to_markdown, html)
        else:
            from markdownify import markdownify as html2md
            md = html2md(html, heading_style="ATX", strip=["script", "style"])
        if not md or not md.strip():
            raise RuntimeError("markdownify produced empty output")

        quality = assess_quality(md)
        logger.debug("playwright: got {} chars, quality={:.2f}", len(md), quality)
        return ToolResult(markdown=md, tool_name=tool, success=True, quality_score=quality)
    except Exception as e:
        logger.warning("playwright failed for {}: {}", url, e)
        return ToolResult(markdown="", tool_name=tool, success=False, error=str(e))


# ---------------------------------------------------------------------------
# Tool: crawl4ai (tools 8 and 9)
# ---------------------------------------------------------------------------


async def extract_with_crawl4ai(
    url: str, proxy_url: str | None = None, timeout: int = 30, **kwargs,
) -> ToolResult:
    """Extract markdown using crawl4ai with anti-bot features.

    Uses stealth mode, magic overlay removal, user simulation, and consent
    popup removal for resilient extraction.
    """
    tool = "crawl4ai"
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig

        browser_kwargs: dict = {
            "headless": True,
            "enable_stealth": True,
            "verbose": False,
        }
        if proxy_url:
            browser_kwargs["proxy_config"] = proxy_url
        browser_config = BrowserConfig(**browser_kwargs)

        run_config = CrawlerRunConfig(
            page_timeout=timeout * 1000,
            magic=True,
            simulate_user=True,
            override_navigator=True,
            remove_overlay_elements=True,
            remove_consent_popups=True,
            verbose=False,
        )

        logger.debug("crawl4ai: crawling {}", url)
        async with AsyncWebCrawler(config=browser_config) as crawler:
            result = await crawler.arun(url=url, config=run_config)

        if not result.success:
            err = result.error_message or "crawl4ai returned success=False"
            raise RuntimeError(err)

        md = str(result.markdown or "")
        if not md.strip():
            raise RuntimeError("crawl4ai returned empty markdown")

        quality = assess_quality(md)
        logger.debug("crawl4ai: got {} chars, quality={:.2f}", len(md), quality)
        return ToolResult(markdown=md, tool_name=tool, success=True, quality_score=quality)
    except Exception as e:
        logger.warning("crawl4ai failed for {}: {}", url, e)
        return ToolResult(markdown="", tool_name=tool, success=False, error=str(e))


async def extract_with_crawl4ai_fit(
    url: str, proxy_url: str | None = None, timeout: int = 30, **kwargs,
) -> ToolResult:
    """Extract fit_markdown using crawl4ai with PruningContentFilter.

    Like extract_with_crawl4ai but applies content filtering to strip
    boilerplate, returning the cleaned fit_markdown.
    """
    tool = "crawl4ai-fit"
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
        from crawl4ai.content_filter_strategy import PruningContentFilter
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

        browser_kwargs: dict = {
            "headless": True,
            "enable_stealth": True,
            "verbose": False,
        }
        if proxy_url:
            browser_kwargs["proxy_config"] = proxy_url
        browser_config = BrowserConfig(**browser_kwargs)

        md_generator = DefaultMarkdownGenerator(
            content_filter=PruningContentFilter(threshold=0.4, threshold_type="fixed"),
        )

        run_config = CrawlerRunConfig(
            page_timeout=timeout * 1000,
            markdown_generator=md_generator,
            excluded_tags=["nav", "footer", "header", "aside"],
            exclude_external_links=True,
            word_count_threshold=10,
            magic=True,
            simulate_user=True,
            override_navigator=True,
            remove_overlay_elements=True,
            remove_consent_popups=True,
            verbose=False,
        )

        logger.debug("crawl4ai-fit: crawling {}", url)
        async with AsyncWebCrawler(config=browser_config) as crawler:
            result = await crawler.arun(url=url, config=run_config)

        if not result.success:
            err = result.error_message or "crawl4ai returned success=False"
            raise RuntimeError(err)

        md_result = result.markdown
        md = str(getattr(md_result, "fit_markdown", "") or getattr(md_result, "raw_markdown", "") or "")
        if not md.strip():
            raise RuntimeError("crawl4ai-fit returned empty markdown")

        quality = assess_quality(md)
        logger.debug("crawl4ai-fit: got {} chars, quality={:.2f}", len(md), quality)
        return ToolResult(markdown=md, tool_name=tool, success=True, quality_score=quality)
    except Exception as e:
        logger.warning("crawl4ai-fit failed for {}: {}", url, e)
        return ToolResult(markdown="", tool_name=tool, success=False, error=str(e))


# ---------------------------------------------------------------------------
# Tool: firecrawl (tool 6)
# ---------------------------------------------------------------------------


async def extract_with_firecrawl(
    url: str, proxy_url: str | None = None, timeout: int = 30, **kwargs,
) -> ToolResult:
    """Extract markdown using the Firecrawl cloud API (requires FIRECRAWL_API_KEY)."""
    tool = "firecrawl"
    try:
        import os

        api_key = os.environ.get("FIRECRAWL_API_KEY")
        if not api_key:
            raise RuntimeError("FIRECRAWL_API_KEY environment variable is not set")

        _ = proxy_url, timeout  # firecrawl manages its own proxy/timeout

        def _scrape() -> str:
            from firecrawl import Firecrawl
            app = Firecrawl(api_key=api_key)
            doc = app.scrape(url, formats=["markdown"])  # type: ignore[reportAttributeAccessIssue]
            md = doc.markdown if hasattr(doc, 'markdown') else ""
            if not md:
                if isinstance(doc, dict):
                    md = doc.get("markdown", "")
            if not md:
                raise RuntimeError("firecrawl returned empty markdown")
            return md

        logger.debug("firecrawl: scraping {}", url)
        md = await asyncio.to_thread(_scrape)
        quality = assess_quality(md)
        logger.debug("firecrawl: got {} chars, quality={:.2f}", len(md), quality)
        return ToolResult(markdown=md, tool_name=tool, success=True, quality_score=quality)
    except Exception as e:
        logger.warning("firecrawl failed for {}: {}", url, e)
        return ToolResult(markdown="", tool_name=tool, success=False, error=str(e))


# ---------------------------------------------------------------------------
# Tool: Jina Reader API (tool 7)
# ---------------------------------------------------------------------------


async def extract_with_jina(
    url: str, proxy_url: str | None = None, timeout: int = 30, **kwargs,
) -> ToolResult:
    """Extract markdown using the Jina Reader API (requires JINA_API_KEY)."""
    tool = "jina"
    try:
        import os

        import httpx

        api_key = os.environ.get("JINA_API_KEY")
        if not api_key:
            raise RuntimeError("JINA_API_KEY environment variable is not set")

        jina_url = f"https://r.jina.ai/{url}"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "text/markdown",
            "X-Return-Format": "markdown",
        }

        logger.debug("jina: requesting {}", jina_url)
        async with httpx.AsyncClient(proxy=proxy_url, timeout=timeout) as client:
            response = await client.get(jina_url, headers=headers, follow_redirects=True)
            response.raise_for_status()
            md = response.text

        if not md or not md.strip():
            raise RuntimeError("Jina Reader returned empty response")

        quality = assess_quality(md)
        logger.debug("jina: got {} chars, quality={:.2f}", len(md), quality)
        return ToolResult(markdown=md, tool_name=tool, success=True, quality_score=quality)
    except Exception as e:
        logger.warning("jina failed for {}: {}", url, e)
        return ToolResult(markdown="", tool_name=tool, success=False, error=str(e))


# ---------------------------------------------------------------------------
# Registry and fallback chain
# ---------------------------------------------------------------------------

TOOLS: dict[int, tuple[str, Callable]] = {
    1: ("trafilatura", extract_with_trafilatura),
    2: ("trafilatura", extract_with_trafilatura),
    3: ("readability", extract_with_readability),
    4: ("readability", extract_with_readability),
    5: ("playwright", extract_with_playwright),
    6: ("firecrawl", extract_with_firecrawl),
    7: ("jina", extract_with_jina),
    8: ("crawl4ai", extract_with_crawl4ai),
    9: ("crawl4ai-fit", extract_with_crawl4ai_fit),
}

# Directed fallback: tool_id -> next tool_id on failure/low quality (None = stop)
FALLBACKS: dict[int, int | None] = {
    1: 5,     # trafilatura → playwright
    2: None,  # trafilatura strict (no fallback)
    3: 5,     # readability → playwright
    4: None,  # readability strict (no fallback)
    5: 6,     # playwright → firecrawl
    6: 7,     # firecrawl → jina
    7: 2,     # jina → trafilatura strict (terminal)
    8: 6,     # crawl4ai → firecrawl
    9: 5,     # crawl4ai-fit → playwright
}

QUALITY_THRESHOLD = 0.5  # Minimum quality to accept result without fallback
