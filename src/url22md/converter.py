"""URL-to-Markdown converter orchestrator with cascading fallback and concurrent processing."""
# this_file: src/url22md/converter.py

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn

from url22md.tools import QUALITY_THRESHOLD, TOOLS, ToolResult
from url22md.utils import append_jsonl_record, build_proxy_url, read_jsonl_report, setup_logging, url2filename


async def convert_single_url(
    url: str,
    proxy_url: str | None,
    tool: int | None = None,
    timeout: int = 30,
) -> ToolResult:
    """Convert a single URL to Markdown using cascading tool fallback.

    If *tool* is specified (1-6), only that tool is attempted. Otherwise tools
    are tried in order 1 -> 2 -> 3 -> 4 -> 5 -> 6 and the first result whose
    quality_score meets QUALITY_THRESHOLD is returned. When no result meets the
    threshold, the best result seen so far is returned.

    Args:
        url: The HTTP(S) URL to convert.
        proxy_url: Optional proxy URL for tools that support it.
        tool: If given, restrict to this single tool number.
        timeout: Per-tool timeout in seconds.

    Returns:
        The best ToolResult obtained.
    """
    tool_ids: list[int] = [tool] if tool is not None else list(TOOLS.keys())

    best: ToolResult | None = None

    for tid in tool_ids:
        entry = TOOLS.get(tid)
        if entry is None:
            logger.warning("Unknown tool id {}, skipping", tid)
            continue

        tool_name, tool_fn = entry

        logger.debug("Trying tool {} ({}) for {}", tid, tool_name, url)
        try:
            result = await tool_fn(url=url, proxy_url=proxy_url, timeout=timeout)
        except Exception as exc:
            logger.debug("Tool {} ({}) raised {}: {}", tid, tool_name, type(exc).__name__, exc)
            result = ToolResult(
                success=False,
                markdown="",
                tool_name=tool_name,
                quality_score=0.0,
                error=f"{type(exc).__name__}: {exc}",
            )

        # Track the best result we have seen so far.
        if best is None or result.quality_score > best.quality_score:
            best = result

        if result.success and result.quality_score >= QUALITY_THRESHOLD:
            logger.debug(
                "Tool {} ({}) succeeded for {} with quality {:.2f}",
                tid,
                tool_name,
                url,
                result.quality_score,
            )
            return result

        logger.debug(
            "Tool {} ({}) quality {:.2f} below threshold {:.2f} for {}",
            tid,
            tool_name,
            result.quality_score,
            QUALITY_THRESHOLD,
            url,
        )

    # Return whatever we have, even if below threshold.
    if best is not None:
        return best

    # Should not happen (TOOLS is non-empty), but guard against it.
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
    jsonl_path: Path,
    proxy_url: str | None = None,
    tool: int | None = None,
    concurrency: int = 5,
    timeout: int = 30,
) -> dict:
    """Process a list of URLs concurrently, writing Markdown files and a JSONL report.

    Already-processed URLs (present in *jsonl_path*) are skipped. Results are
    appended to the JSONL report immediately after each URL is processed so that
    a crash does not invalidate earlier work.

    Args:
        urls: URLs to convert.
        output_dir: Directory where .md files are written.
        jsonl_path: Path to the JSONL report file.
        proxy_url: Optional proxy URL.
        tool: Restrict to a single tool number, or None for cascade.
        concurrency: Maximum number of concurrent conversions.
        timeout: Per-tool timeout in seconds.

    Returns:
        Summary dict with keys: total, processed, skipped, succeeded, failed.
    """
    existing = read_jsonl_report(jsonl_path)
    to_process = [u for u in urls if u not in existing]
    skipped = len(urls) - len(to_process)

    if skipped:
        logger.info("Skipping {} already-processed URLs", skipped)

    succeeded = 0
    failed = 0
    sem = asyncio.Semaphore(concurrency)

    async def _handle(url: str, idx: int, total: int, progress: Progress, task_id) -> None:
        nonlocal succeeded, failed
        async with sem:
            result = await convert_single_url(url, proxy_url, tool=tool, timeout=timeout)

            filename = url2filename(url) + ".md"
            filepath = output_dir / filename

            # Write the markdown file (even on partial success, for inspection).
            if result.markdown:
                try:
                    filepath.write_text(result.markdown, encoding="utf-8")
                except OSError as exc:
                    logger.error("Failed to write {}: {}", filepath, exc)

            # Build and append JSONL record immediately.
            record = {
                "url": url,
                "filename": filename,
                "tool": result.tool_name,
                "success": result.success,
                "quality": result.quality_score,
                "error": result.error,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            append_jsonl_record(jsonl_path, record)

            if result.success:
                succeeded += 1
            else:
                failed += 1

            progress.update(task_id, advance=1)  # type: ignore[reportArgumentType]
            logger.info(
                "Processed {}/{}: {} ({}, quality {:.2f})",
                idx + 1,
                total,
                url,
                result.tool_name,
                result.quality_score,
            )

    total = len(to_process)
    if total == 0:
        logger.info("Nothing to process (all {} URLs already done)", len(urls))
        return {
            "total": len(urls),
            "processed": 0,
            "skipped": skipped,
            "succeeded": 0,
            "failed": 0,
        }

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
    ) as progress:
        task_id = progress.add_task("Converting URLs", total=total)
        tasks = [
            _handle(url, idx, total, progress, task_id)
            for idx, url in enumerate(to_process)
        ]
        await asyncio.gather(*tasks)

    return {
        "total": len(urls),
        "processed": total,
        "skipped": skipped,
        "succeeded": succeeded,
        "failed": failed,
    }


def run_conversion(
    urls: list[str],
    output_dir: str = ".",
    jsonl_path: str | None = None,
    proxy: bool = False,
    tool: int | None = None,
    clean: bool = False,
    clean_all: bool = False,
    concurrency: int = 5,
    timeout: int = 30,
    verbose: bool = False,
) -> dict:
    """Synchronous entry point for URL-to-Markdown conversion.

    This is the function called by the CLI. It sets up logging, resolves paths,
    handles --clean / --clean_all flags, builds proxy configuration, and runs
    the async processing pipeline.

    Args:
        urls: List of HTTP(S) URLs to convert.
        output_dir: Directory for output .md files (created if needed).
        jsonl_path: Path to JSONL report file. Defaults to ``output_dir/_url2md.jsonl``.
        proxy: Whether to use the Webshare proxy (requires WEBSHARE_* env vars).
        tool: Restrict to a single tool number (1-6), or None for cascade.
        clean: Delete the existing JSONL report before processing.
        clean_all: Delete the JSONL report and all .md files listed in it.
        concurrency: Maximum number of concurrent URL conversions.
        timeout: Per-tool timeout in seconds.
        verbose: Enable debug-level logging.

    Returns:
        Summary dict with keys: total, processed, skipped, succeeded, failed.
    """
    setup_logging(verbose)
    console = Console()

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
        return {"total": 0, "processed": 0, "skipped": 0, "succeeded": 0, "failed": 0}

    summary = asyncio.run(
        process_urls(
            urls=urls,
            output_dir=out,
            jsonl_path=report,
            proxy_url=proxy_url,
            tool=tool,
            concurrency=concurrency,
            timeout=timeout,
        )
    )

    # Print summary.
    console.print()
    console.print("[bold]Conversion complete[/bold]")
    console.print(f"  Total URLs:  {summary['total']}")
    console.print(f"  Skipped:     {summary['skipped']}")
    console.print(f"  Processed:   {summary['processed']}")
    console.print(f"  Succeeded:   {summary['succeeded']}")
    console.print(f"  Failed:      {summary['failed']}")

    return summary
