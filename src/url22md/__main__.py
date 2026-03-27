"""CLI entry point for url22md."""
# this_file: src/url22md/__main__.py

import fire
from url22md.utils import read_urls_input, setup_logging
from url22md.converter import run_conversion
from pathlib import Path


def cli(
    url: str | None = None,
    Urls_path: str | None = None,
    format: str = "md",
    output_dir: str | Path = ".",
    jsonl: str | Path | None = None,
    tool: int | None = None,
    proxy: bool = False,
    Force: bool = False,
    minify: bool = False,
    clean: bool = False,
    Clean_all: bool = False,
    Jobs: int = 5,
    Timeout: int = 30,
    verbose: bool = False,
) -> None:
    """Convert HTTP(S) URLs to Markdown files.

    Args:
        url: Single URL to convert.
        Urls_path: Path to file with one URL per line.
        format: Output format: "md"=one .md per URL, "all"=single combined .md,
                "json"=JSONL with markdown content, "-"=JSONL to stdout.
        output_dir: Directory for output files (default: current dir).
        jsonl: Path for JSONL progress report (default: output_dir/_url2md.jsonl).
        tool: Start from tool (1=trafilatura, 2=trafilatura-strict, 3=readability,
              4=readability-strict, 5=playwright, 6=firecrawl, 7=jina, 8=crawl4ai,
              9=crawl4ai-fit).
        proxy: Use Webshare proxy (requires WEBSHARE_* env vars).
        Force: Re-process URLs even if already in the JSONL report.
        minify: Article-only extraction: readability for tools 1-4, pruning for crawl4ai.
        clean: Delete existing report file before running.
        Clean_all: Delete existing report and output files before running.
        Jobs: Max concurrent URL processing (default: 5).
        Timeout: Timeout per URL in seconds (default: 30).
        verbose: Enable debug logging.
    """
    setup_logging(verbose)

    urls = read_urls_input(url, Urls_path)
    if not urls:
        from rich.console import Console
        Console().print("[red]No URLs provided. Use --url, --Urls_path, or pipe via stdin.[/red]")
        raise SystemExit(1)

    run_conversion(
        urls=urls,
        output_dir=Path(output_dir),
        jsonl_path=Path(jsonl) if jsonl else None,
        format=format,
        proxy=proxy,
        tool=tool,
        force=Force,
        minify=minify,
        clean=clean,
        clean_all=Clean_all,
        concurrency=Jobs,
        timeout=Timeout,
        verbose=verbose,
    )


def main():
    fire.Fire(cli)


if __name__ == "__main__":
    main()
