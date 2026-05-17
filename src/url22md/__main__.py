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
    """Convert HTTP(S) URLs to Markdown.

    Args:
        url: Convert a single URL.
        Urls_path: Path to a text file containing one URL per line.
        format: Set output format: 
                "md" = individual .md files (default).
                "all" = a single combined .md file.
                "json" = JSONL file containing the markdown payload.
                "-" = emit JSONL to stdout.
        output_dir: Directory where files will be saved (default: current directory).
        jsonl: Path for the JSONL progress tracker (default: output_dir/_url2md.jsonl).
        tool: Force a specific starting extraction tool (1-9).
              1=trafilatura, 2=trafilatura-strict, 3=readability, 4=readability-strict,
              5=playwright, 6=firecrawl, 7=jina, 8=crawl4ai, 9=crawl4ai-fit.
        proxy: Route traffic through a Webshare proxy (requires WEBSHARE_* env variables).
        Force: Re-run URLs even if they are already recorded as complete in the JSONL report.
        minify: Apply article-only filters to strip navigation and boilerplate content.
        clean: Delete the existing JSONL report before running.
        Clean_all: Delete the existing report AND all associated output files before running.
        Jobs: Maximum number of URLs to process concurrently (default: 5).
        Timeout: Seconds to wait before aborting a single tool attempt (default: 30).
        verbose: Print detailed debug logging.
    """
    setup_logging(verbose)

    urls = read_urls_input(url, Urls_path)
    if not urls:
        from rich.console import Console
        Console().print("[red]No URLs provided. Provide --url, --Urls_path, or pipe text via stdin.[/red]")
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
