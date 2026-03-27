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

    Each tool has a specific fallback (see FALLBACKS in tools.py). Starting from
    *tool* (default 1), on failure or low quality the chain is followed until a
    good result is found or the chain ends (fallback is None).

    Args:
        url: The HTTP(S) URL to convert.
        proxy_url: Optional proxy URL for tools that support it.
        tool: Starting tool number (1-9, default 1).
        timeout: Per-tool timeout in seconds.
        minify: Article-only extraction via readability/pruning filter.

    Returns:
        The best ToolResult obtained.
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
            result = await tool_fn(url=url, proxy_url=proxy_url, timeout=timeout, minify=minify)
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
    jsonl_path: Path | None,
    proxy_url: str | None = None,
    tool: int | None = None,
    force: bool = False,
    minify: bool = False,
    concurrency: int = 5,
    timeout: int = 30,
) -> list[dict]:
    """Process a list of URLs concurrently and return a list of result records.

    Each record contains: url, filename, tool, success, quality, error, markdown,
    timestamp. Already-processed URLs (present in *jsonl_path*) are skipped
    unless *force* is True. Skipped URLs are included in the output with their
    existing record data.

    Args:
        urls: URLs to convert.
        jsonl_path: Path to existing JSONL report (for skip detection). None to skip nothing.
        proxy_url: Optional proxy URL.
        tool: Starting tool number, or None for default cascade.
        force: Re-process URLs even if already in the report.
        minify: Article-only extraction via readability/pruning filter.
        concurrency: Maximum number of concurrent conversions.
        timeout: Per-tool timeout in seconds.

    Returns:
        List of result record dicts (one per URL, in input order).
    """
    existing = {} if force or jsonl_path is None else read_jsonl_report(jsonl_path)
    to_process = {u for u in urls if u not in existing}
    skipped_count = len(urls) - len(to_process)

    if skipped_count:
        logger.info("Skipping {} already-processed URLs", skipped_count)

    results: dict[str, dict] = {}
    sem = asyncio.Semaphore(concurrency)

    async def _handle(url: str, progress: Progress, task_id) -> None:
        async with sem:
            result = await convert_single_url(url, proxy_url, tool=tool, timeout=timeout, minify=minify)
            record = {
                "url": url,
                "filename": url2filename(url) + ".md",
                "tool": result.tool_name,
                "success": result.success,
                "quality": result.quality_score,
                "error": result.error,
                "markdown": result.markdown,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            results[url] = record
            progress.update(task_id, advance=1)
            logger.info("Processed: {} ({}, quality {:.2f})", url, result.tool_name, result.quality_score)

    if to_process:
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
        ) as progress:
            task_id = progress.add_task("Converting URLs", total=len(to_process))
            tasks = [_handle(url, progress, task_id) for url in urls if url in to_process]
            await asyncio.gather(*tasks)

    # Build ordered output: skipped URLs get their existing record, processed get new record
    ordered: list[dict] = []
    for url in urls:
        if url in results:
            ordered.append(results[url])
        elif url in existing:
            rec = dict(existing[url])
            rec.setdefault("markdown", "")
            ordered.append(rec)
    return ordered


def _write_md_files(records: list[dict], output_dir: Path) -> None:
    """Write individual .md files for each record with markdown content."""
    for rec in records:
        if rec.get("markdown"):
            filepath = output_dir / rec["filename"]
            try:
                filepath.write_text(rec["markdown"], encoding="utf-8")
            except OSError as exc:
                logger.error("Failed to write {}: {}", filepath, exc)


def _write_combined_md(records: list[dict], output_dir: Path) -> None:
    """Write all results into a single combined.md file with HR + H1 URL separators."""
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
    """Write records to a JSONL report file."""
    for rec in records:
        out = dict(rec)
        if not include_markdown:
            out.pop("markdown", None)
        append_jsonl_record(jsonl_path, out)


def _emit_jsonl_stdout(records: list[dict]) -> None:
    """Emit records as JSONL to stdout (includes markdown)."""
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
) -> list[dict]:
    """Convert URLs to Markdown and optionally write output files.

    This is the main Python API entry point. Without *format*, it processes
    URLs and returns a list of result records without writing any files.
    With *format*, it also writes output in the specified mode.

    Args:
        urls: List of HTTP(S) URLs to convert.
        output_dir: Directory for output files (created if needed).
        jsonl_path: Path to JSONL report file. Defaults to ``output_dir/_url2md.jsonl``.
        format: Output format. None=no file output (return only), "md"=one .md per URL + JSONL,
                "all"=combined single .md + JSONL, "json"=JSONL with markdown content,
                "-"=JSONL to stdout (no files).
        proxy: Whether to use the Webshare proxy (requires WEBSHARE_* env vars).
        tool: Starting tool number (1-9), or None for default cascade.
        force: Re-process URLs even if already in the JSONL report.
        minify: Article-only extraction via readability/pruning filter.
        clean: Delete the existing JSONL report before processing.
        clean_all: Delete the JSONL report and all .md files listed in it.
        concurrency: Maximum number of concurrent URL conversions.
        timeout: Per-tool timeout in seconds.
        verbose: Enable debug-level logging.

    Returns:
        List of result record dicts. Each has: url, filename, tool, success,
        quality, error, markdown, timestamp.
    """
    setup_logging(verbose)
    console = Console(stderr=True)

    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    report = Path(jsonl_path).resolve() if jsonl_path else out / "_url2md.jsonl"

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

    proxy_url = build_proxy_url(proxy)

    if not urls:
        console.print("[yellow]No URLs to process.[/yellow]")
        return []

    records = asyncio.run(
        process_urls(
            urls=urls,
            jsonl_path=report if format != "-" else None,
            proxy_url=proxy_url,
            tool=tool,
            force=force,
            minify=minify,
            concurrency=concurrency,
            timeout=timeout,
        )
    )

    # Write output based on format
    if format == "md":
        _write_md_files(records, out)
        _write_jsonl_report(records, report, include_markdown=False)
    elif format == "all":
        _write_combined_md(records, out)
        _write_jsonl_report(records, report, include_markdown=False)
    elif format == "json":
        _write_jsonl_report(records, report, include_markdown=True)
    elif format == "-":
        _emit_jsonl_stdout(records)

    # Print summary to stderr (skip for stdout mode)
    succeeded = sum(1 for r in records if r.get("success"))
    failed = sum(1 for r in records if not r.get("success"))
    processed = sum(1 for r in records if "timestamp" in r)
    console.print()
    console.print("[bold]Conversion complete[/bold]")
    console.print(f"  Total URLs:  {len(urls)}")
    console.print(f"  Processed:   {processed}")
    console.print(f"  Succeeded:   {succeeded}")
    console.print(f"  Failed:      {failed}")

    return records
