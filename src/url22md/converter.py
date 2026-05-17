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
    """Process multiple URLs concurrently.

    Manages a queue of URLs, runs them through the extraction toolchain, and outputs progress.
    Reads existing JSONL reports to skip previously processed URLs unless forced.

    Args:
        urls: List of URLs to convert.
        jsonl_path: Path to the JSONL report file for skip detection and logging.
        proxy_url: Optional proxy string.
        tool: Starting tool ID (1-9) or None for the default cascade.
        force: If True, ignores existing records and re-processes all URLs.
        minify: If True, applies article-only extraction filters.
        concurrency: Maximum number of simultaneous URL processing tasks.
        timeout: Time limit in seconds per tool attempt.

    Returns:
        List of result dictionaries, matching the input order of URLs.
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
) -> list[dict]:
    """Execute the full conversion workflow.

    Main entry point for both the CLI and external Python scripts. 
    It parses inputs, manages the workspace (cleaning old files if requested), 
    runs the concurrent extraction, and dispatches the formatting and file writing logic.

    Args:
        urls: List of HTTP(S) URLs to process.
        output_dir: Target directory for saved files. Created if missing.
        jsonl_path: Path to the JSONL report file. Defaults to `output_dir/_url2md.jsonl`.
        format: Determines the output structure. 
            "md" saves one `.md` file per URL. 
            "all" creates a single `combined.md`. 
            "json" writes the full payload (with markdown) to the report.
            "-" dumps JSONL to stdout.
            None skips file writing entirely.
        proxy: Enable Webshare proxy (needs WEBSHARE_* env vars).
        tool: Starting tool ID (1-9) or None for default behavior.
        force: Process URLs even if they exist in the report.
        minify: Apply article-focused extraction filters.
        clean: Delete the existing JSONL report before running.
        clean_all: Delete the existing report AND any `.md` files listed inside it.
        concurrency: Max simultaneous extractions.
        timeout: Seconds to wait per tool attempt.
        verbose: Set log level to DEBUG instead of WARNING.

    Returns:
        A list of dictionaries representing the extraction results.
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
