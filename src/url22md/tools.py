"""Extraction tool implementations for converting URLs to Markdown.

Six async tools, each wrapping a different extraction backend:
1. trafilatura  - fast, no JS rendering
2. crawl4ai     - async-native, JS-capable via headless browser
3. playwright   - real browser + markdownify conversion
4. firecrawl    - cloud API with anti-bot capabilities
5. jina         - Jina Reader cloud API
6. readability  - readability-lxml + markdownify fallback
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass

from loguru import logger

# this_file: src/url22md/tools.py
this_file = "src/url22md/tools.py"


@dataclass
class ToolResult:
    """Result returned by every extraction tool."""

    markdown: str
    tool_name: str
    success: bool
    error: str | None = None
    quality_score: float = 0.0  # 0.0 to 1.0


def assess_quality(markdown: str) -> float:
    """Score markdown quality from 0.0 to 1.0 based on content length, structure, etc."""
    if not markdown or not markdown.strip():
        return 0.0
    text = markdown.strip()
    score = 0.0

    # Length scoring
    if len(text) > 1000:
        score += 0.4
    elif len(text) > 500:
        score += 0.3
    elif len(text) > 100:
        score += 0.2
    else:
        score += 0.1

    # Has headings
    if any(line.startswith("#") for line in text.split("\n")):
        score += 0.2

    # Has paragraphs (multiple blank-line-separated blocks)
    if text.count("\n\n") >= 2:
        score += 0.2

    # Has markdown links
    if "[" in text and "](" in text:
        score += 0.1

    # Penalty for excessive residual HTML tags
    html_tags = len(re.findall(r"<[^>]+>", text))
    if html_tags > 10:
        score -= 0.2

    return max(0.0, min(1.0, score))


# ---------------------------------------------------------------------------
# Tool 1: trafilatura
# ---------------------------------------------------------------------------


async def extract_with_trafilatura(
    url: str, proxy_url: str | None = None, timeout: int = 30
) -> ToolResult:
    """Extract markdown using trafilatura (fast, no JS rendering)."""
    tool = "trafilatura"
    try:

        def _fetch_and_extract() -> str:
            import trafilatura
            import trafilatura.downloads

            # trafilatura reads proxy from module global, not config
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
                # Restore original proxy state
                if proxy_url:
                    trafilatura.downloads.PROXY_URL = old_proxy
                    trafilatura.downloads.HTTP_POOL = None
                    trafilatura.downloads.NO_CERT_POOL = None
                    trafilatura.downloads.RETRY_STRATEGY = None

        logger.debug("trafilatura: fetching {}", url)
        md = await asyncio.to_thread(_fetch_and_extract)
        quality = assess_quality(md)
        logger.debug("trafilatura: got {} chars, quality={:.2f}", len(md), quality)
        return ToolResult(markdown=md, tool_name=tool, success=True, quality_score=quality)
    except Exception as e:
        logger.warning("trafilatura failed for {}: {}", url, e)
        return ToolResult(markdown="", tool_name=tool, success=False, error=str(e))


# ---------------------------------------------------------------------------
# Tool 2: crawl4ai
# ---------------------------------------------------------------------------


async def extract_with_crawl4ai(
    url: str, proxy_url: str | None = None, timeout: int = 30
) -> ToolResult:
    """Extract markdown using crawl4ai (async, JS-capable headless browser)."""
    tool = "crawl4ai"
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig

        browser_config = BrowserConfig(headless=True)

        run_kwargs: dict = {"page_timeout": timeout * 1000}
        if proxy_url:
            run_kwargs["proxy_config"] = proxy_url  # accepts plain URL string
        run_config = CrawlerRunConfig(**run_kwargs)

        logger.debug("crawl4ai: crawling {}", url)
        async with AsyncWebCrawler(config=browser_config) as crawler:
            result = await crawler.arun(url=url, config=run_config)  # type: ignore[assignment]

        if not result.success:  # type: ignore[reportAttributeAccessIssue]
            err = result.error_message or "crawl4ai returned success=False"  # type: ignore[reportAttributeAccessIssue]
            raise RuntimeError(err)

        md = str(result.markdown or "")  # type: ignore[reportAttributeAccessIssue]
        if not md.strip():
            raise RuntimeError("crawl4ai returned empty markdown")

        quality = assess_quality(md)
        logger.debug("crawl4ai: got {} chars, quality={:.2f}", len(md), quality)
        return ToolResult(markdown=md, tool_name=tool, success=True, quality_score=quality)
    except Exception as e:
        logger.warning("crawl4ai failed for {}: {}", url, e)
        return ToolResult(markdown="", tool_name=tool, success=False, error=str(e))


# ---------------------------------------------------------------------------
# Tool 3: playwright + markdownify
# ---------------------------------------------------------------------------


async def extract_with_playwright(
    url: str, proxy_url: str | None = None, timeout: int = 30
) -> ToolResult:
    """Extract markdown using Playwright (real browser) + markdownify."""
    tool = "playwright"
    try:
        from markdownify import markdownify as html2md
        from playwright.async_api import async_playwright

        launch_args: dict = {"headless": True}
        if proxy_url:
            launch_args["proxy"] = {"server": proxy_url}

        logger.debug("playwright: navigating to {}", url)
        async with async_playwright() as p:
            browser = await p.chromium.launch(**launch_args)
            try:
                page = await browser.new_page()
                await page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
                html = await page.content()
            finally:
                await browser.close()

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
# Tool 4: firecrawl
# ---------------------------------------------------------------------------


async def extract_with_firecrawl(
    url: str, proxy_url: str | None = None, timeout: int = 30
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
                # Fallback: try dict-style access for older SDK versions
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
# Tool 5: Jina Reader API
# ---------------------------------------------------------------------------


async def extract_with_jina(
    url: str, proxy_url: str | None = None, timeout: int = 30
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
# Tool 6: readability + markdownify
# ---------------------------------------------------------------------------


async def extract_with_readability(
    url: str, proxy_url: str | None = None, timeout: int = 30
) -> ToolResult:
    """Extract markdown using readability-lxml + markdownify (article-focused fallback)."""
    tool = "readability"
    try:
        import httpx

        logger.debug("readability: fetching {}", url)
        async with httpx.AsyncClient(proxy=proxy_url, timeout=timeout) as client:
            response = await client.get(url, follow_redirects=True)
            response.raise_for_status()
            html = response.text

        def _extract(html_content: str) -> str:
            from markdownify import markdownify as html2md
            from readability import Document

            doc = Document(html_content)
            title = doc.title() or ""
            cleaned = doc.summary()
            md = html2md(cleaned, heading_style="ATX", strip=["script", "style"])
            # Prepend title as heading if readability extracted one
            if title and md and not md.strip().startswith("#"):
                md = f"# {title}\n\n{md}"
            return md

        md = await asyncio.to_thread(_extract, html)

        if not md or not md.strip():
            raise RuntimeError("readability + markdownify produced empty output")

        quality = assess_quality(md)
        logger.debug("readability: got {} chars, quality={:.2f}", len(md), quality)
        return ToolResult(markdown=md, tool_name=tool, success=True, quality_score=quality)
    except Exception as e:
        logger.warning("readability failed for {}: {}", url, e)
        return ToolResult(markdown="", tool_name=tool, success=False, error=str(e))


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

TOOLS: dict[int, tuple[str, Callable]] = {
    1: ("trafilatura", extract_with_trafilatura),
    2: ("crawl4ai", extract_with_crawl4ai),
    3: ("playwright", extract_with_playwright),
    4: ("firecrawl", extract_with_firecrawl),
    5: ("jina", extract_with_jina),
    6: ("readability", extract_with_readability),
}

QUALITY_THRESHOLD = 0.3  # Minimum quality to accept result without fallback
