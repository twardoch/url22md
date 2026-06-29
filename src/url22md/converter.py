"""URL-to-Markdown converter orchestrator with directed fallback chains and concurrent processing."""
# this_file: src/url22md/converter.py

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn

from url22md.tools import FALLBACKS, QUALITY_THRESHOLD, TOOLS, ToolResult
from url22md.utils import append_jsonl_record, build_proxy_url, read_jsonl_report, setup_logging, url2filename


async def convert_single_url(
    url: str,
    proxy_url: str | None,
    tool: int | None = None,
    timeout: int = 30,
    minify: bool = False,
) -> ToolResult:
    """Convert a single URL to Markdown following directed fallback chains.

    Starts with a primary tool and follows a specific fallback path if extraction fails 
    or returns low quality results (score < QUALITY_THRESHOLD).

    Args:
        url: The target HTTP/HTTPS URL.
        proxy_url: Optional proxy string for tools that support it.
        tool: Starting tool ID (1-9). Defaults to 1 (trafilatura).
        timeout: Time limit in seconds per tool attempt.
        minify: If True, uses article-only extraction filters to strip menus and boilerplate.

    Returns:
        A ToolResult object containing the best extracted markdown and metadata.
    """
    current_tid: int | None = tool if tool is not None else 1
    visited: set[int] = set()
    best: ToolResult | None = None

    while current_tid is not None and current_tid not in visited:
        visited.add(current_tid)

        entry = TOOLS.get(current_tid)
        if entry is None:
            logger.warning("Unknown tool id {}, stopping", current_tid)
            break

        tool_name, tool_fn = entry

        logger.debug("Trying tool {} ({}) for {}", current_tid, tool_name, url)
        try:
            result = await tool_fn(url=url, proxy_url=proxy_url, timeout=timeout)
        except Exception as exc:
            logger.debug("Tool {} ({}) raised {}: {}", current_tid, tool_name, type(exc).__name__, exc)
            result = ToolResult(
                success=False,
                markdown="",
                tool_name=tool_name,
                quality_score=0.0,
                error=f"{type(exc).__name__}: {exc}",
            )

        if best is None or result.quality_score > best.quality_score:
            best = result

        if result.success and result.quality_score >= QUALITY_THRESHOLD:
            logger.debug(
                "Tool {} ({}) succeeded for {} with quality {:.2f}",
                current_tid,
                tool_name,
                url,
                result.quality_score,
            )
            return result

        logger.debug(
            "Tool {} ({}) quality {:.2f} below threshold {:.2f} for {}, following fallback",
            current_tid,
            tool_name,
            result.quality_score,
            QUALITY_THRESHOLD,
            url,
        )

        current_tid = FALLBACKS.get(current_tid)

    if best is not None:
        return best

    return ToolResult(
        success=False,
        markdown="",
        tool_name="none",
        quality_score=0.0,
        error="No tools available",
    )


async def process_urls(
    urls: list[str],
    output_dir: Path,
    jsonl_path: Path | None = None,
    proxy_url: str | None = None,
    tool: int | None = None,
    force: bool = False,
    concurrency: int = 5,
    timeout: int = 30,
) -> dict[str, int]:
    """Process multiple URLs concurrently, writing MD files and a JSONL report.

    Reads existing JSONL reports to skip previously processed URLs unless forced.
    Writes one .md file per successful extraction and appends one JSONL record per URL.

    Args:
        urls: List of URLs to convert.
        output_dir: Directory to write individual .md files.
        jsonl_path: Path to the JSONL report file for skip detection and logging.
        proxy_url: Optional proxy string.
        tool: Starting tool ID or None for the default cascade.
        force: If True, ignores existing records and re-processes all URLs.
        concurrency: Maximum number of simultaneous URL processing tasks.
        timeout: Time limit in seconds per tool attempt.

    Returns:
        Summary dict with keys: total, processed, skipped, succeeded, failed.
    """
    if not urls:
        return {"total": 0, "processed": 0, "skipped": 0, "succeeded": 0, "failed": 0}

    existing = {} if force or jsonl_path is None else read_jsonl_report(jsonl_path)
    to_process = [u for u in urls if u not in existing]
    skipped_count = len(urls) - len(to_process)

    if skipped_count:
        logger.info("Skipping {} already-processed URLs", skipped_count)

    processed = 0
    succeeded = 0
    failed = 0
    sem = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()

    async def _handle(url: str) -> None:
        nonlocal processed, succeeded, failed
        async with sem:
            result = await convert_single_url(url, proxy_url, tool=tool, timeout=timeout)
            record = {
                "url": url,
                "filename": url2filename(url) + ".md",
                "tool": result.tool_name,
                "success": result.success,
                "quality": result.quality_score,
                "error": result.error,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            # Write individual MD file for successful extractions
            if result.success and result.markdown:
                filepath = output_dir / record["filename"]
                try:
                    filepath.write_text(result.markdown, encoding="utf-8")
                except OSError as exc:
                    logger.error("Failed to write {}: {}", filepath, exc)
            # Append JSONL record (without heavy markdown payload)
            if jsonl_path is not None:
                append_jsonl_record(jsonl_path, record)
            async with lock:
                processed += 1
                if result.success:
                    succeeded += 1
                else:
                    failed += 1
            logger.info("Processed: {} ({}, quality {:.2f})", url, result.tool_name, result.quality_score)

    await asyncio.gather(*[_handle(url) for url in to_process])

    return {
        "total": len(urls),
        "processed": processed,
        "skipped": skipped_count,
        "succeeded": succeeded,
        "failed": failed,
    }


def _write_md_files(records: list[dict], output_dir: Path) -> None:
    """Write individual Markdown files for successful extractions.
    
    Args:
        records: List of result records containing the extracted 'markdown'.
        output_dir: Destination directory.
    """
    for rec in records:
        if rec.get("markdown"):
            filepath = output_dir / rec["filename"]
            try:
                filepath.write_text(rec["markdown"], encoding="utf-8")
            except OSError as exc:
                logger.error("Failed to write {}: {}", filepath, exc)


def _write_combined_md(records: list[dict], output_dir: Path) -> None:
    """Merge all extracted Markdown content into a single file.
    
    Creates 'combined.md' in the output directory, separating articles with a horizontal rule and the URL.
    
    Args:
        records: List of result records.
        output_dir: Destination directory.
    """
    parts: list[str] = []
    for rec in records:
        md = rec.get("markdown", "")
        if md:
            parts.append(f"---\n\n# {rec['url']}\n\n{md}")
    if parts:
        combined = "\n\n".join(parts)
        filepath = output_dir / "combined.md"
        try:
            filepath.write_text(combined, encoding="utf-8")
        except OSError as exc:
            logger.error("Failed to write {}: {}", filepath, exc)


def _write_jsonl_report(records: list[dict], jsonl_path: Path, include_markdown: bool = False) -> None:
    """Save the operation summary to a JSONL file.
    
    Args:
        records: List of result records.
        jsonl_path: Path to the target JSONL file.
        include_markdown: If False, strips the heavy 'markdown' payload before saving to save space.
    """
    for rec in records:
        out = dict(rec)
        if not include_markdown:
            out.pop("markdown", None)
        append_jsonl_record(jsonl_path, out)


def _emit_jsonl_stdout(records: list[dict]) -> None:
    """Print the complete records (including markdown content) to standard output.
    
    Useful for piping the results to other CLI tools.
    """
    for rec in records:
        sys.stdout.write(json.dumps(rec, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def run_conversion(
    urls: list[str],
    output_dir: str | Path = ".",
    jsonl_path: str | Path | None = None,
    format: str | None = None,
    proxy: bool = False,
    tool: int | None = None,
    force: bool = False,
    minify: bool = False,
    clean: bool = False,
    clean_all: bool = False,
    concurrency: int = 5,
    timeout: int = 30,
    verbose: bool = False,
) -> dict[str, int]:
    """Execute the full conversion workflow.

    Main entry point for both the CLI and external Python scripts.
    Manages the workspace (cleaning old files if requested), runs concurrent
    extraction via process_urls, and returns a summary dict.

    Args:
        urls: List of HTTP(S) URLs to process.
        output_dir: Target directory for saved files. Created if missing.
        jsonl_path: Path to the JSONL report file. Defaults to ``output_dir/_url22md.jsonl``.
        format: Optional output modifier.
            ``"all"`` additionally creates a single ``combined.md``.
            ``"-"`` dumps JSONL to stdout instead of writing files.
            Other values are accepted but have no additional effect beyond the
            per-URL .md files and JSONL record that process_urls always writes.
        proxy: Enable Webshare proxy (needs WEBSHARE_* env vars).
        tool: Starting tool ID (1-6) or None for default cascade.
        force: Process URLs even if they exist in the report.
        minify: Kept for CLI compatibility; passed through but not used in cascade.
        clean: Delete the existing JSONL report before running.
        clean_all: Delete the existing report AND any ``.md`` files listed inside it.
        concurrency: Max simultaneous extractions.
        timeout: Seconds to wait per tool attempt.
        verbose: Set log level to DEBUG instead of WARNING.

    Returns:
        Summary dict with keys: total, processed, skipped, succeeded, failed.
    """
    setup_logging(verbose)
    console = Console(stderr=True)

    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    report = Path(jsonl_path).resolve() if jsonl_path else out / "_url22md.jsonl"

    # --clean_all: remove report and all .md files referenced in it.
    if clean_all:
        if report.exists():
            existing = read_jsonl_report(report)
            for rec in existing.values():
                fname = rec.get("filename")
                if fname:
                    md_file = out / fname
                    if md_file.exists():
                        md_file.unlink()
                        logger.debug("Deleted {}", md_file)
            report.unlink()
            logger.info("Deleted report and {} output files", len(existing))
        else:
            logger.debug("No report file to clean at {}", report)
    elif clean:
        if report.exists():
            report.unlink()
            logger.info("Deleted report file {}", report)

    if not urls:
        return {"total": 0, "processed": 0, "skipped": 0, "succeeded": 0, "failed": 0}

    proxy_url = build_proxy_url(proxy)

    summary = asyncio.run(
        process_urls(
            urls=urls,
            output_dir=out,
            jsonl_path=report if format != "-" else None,
            proxy_url=proxy_url,
            tool=tool,
            force=force,
            concurrency=concurrency,
            timeout=timeout,
        )
    )

    # Additional output modes
    if format == "all":
        # Build combined.md from the written .md files
        parts: list[str] = []
        for url in urls:
            fname = url2filename(url) + ".md"
            md_file = out / fname
            if md_file.exists():
                parts.append(f"---\n\n# {url}\n\n{md_file.read_text(encoding='utf-8')}")
        if parts:
            combined = "\n\n".join(parts)
            try:
                (out / "combined.md").write_text(combined, encoding="utf-8")
            except OSError as exc:
                logger.error("Failed to write combined.md: {}", exc)
    elif format == "-":
        # Emit records to stdout
        if report.exists():
            for line in report.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    sys.stdout.write(line + "\n")
            sys.stdout.flush()

    # Print summary to stderr
    console.print()
    console.print("[bold]Conversion complete[/bold]")
    console.print(f"  Total URLs:  {summary['total']}")
    console.print(f"  Processed:   {summary['processed']}")
    console.print(f"  Skipped:     {summary['skipped']}")
    console.print(f"  Succeeded:   {summary['succeeded']}")
    console.print(f"  Failed:      {summary['failed']}")

    return summary
