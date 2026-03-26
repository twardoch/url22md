"""CLI entry point for url22md."""
# this_file: src/url22md/__main__.py

import fire
from url22md.utils import read_urls_input, setup_logging
from url22md.converter import run_conversion


def cli(
    url: str | None = None,
    urls_path: str | None = None,
    output_dir: str = ".",
    jsonl: str | None = None,
    tool: int | None = None,
    proxy: bool = False,
    clean: bool = False,
    clean_all: bool = False,
    concurrency: int = 5,
    timeout: int = 30,
    verbose: bool = False,
) -> None:
    """Convert HTTP(S) URLs to Markdown files.

    Args:
        url: Single URL to convert.
        urls_path: Path to file with one URL per line.
        output_dir: Directory for output .md files (default: current dir).
        jsonl: Path for JSONL progress report (default: output_dir/_url2md.jsonl).
        tool: Force specific tool (1=trafilatura, 2=crawl4ai, 3=playwright, 4=firecrawl, 5=jina, 6=readability).
        proxy: Use Webshare proxy (requires WEBSHARE_* env vars).
        clean: Delete existing report file before running.
        clean_all: Delete existing report and output files before running.
        concurrency: Max concurrent URL processing (default: 5).
        timeout: Timeout per URL in seconds (default: 30).
        verbose: Enable debug logging.
    """
    setup_logging(verbose)

    urls = read_urls_input(url, urls_path)
    if not urls:
        from rich.console import Console
        Console().print("[red]No URLs provided. Use --url, --urls_path, or pipe via stdin.[/red]")
        raise SystemExit(1)

    run_conversion(
        urls=urls,
        output_dir=output_dir,
        jsonl_path=jsonl,
        proxy=proxy,
        tool=tool,
        clean=clean,
        clean_all=clean_all,
        concurrency=concurrency,
        timeout=timeout,
        verbose=verbose,
    )


def main():
    fire.Fire(cli)


if __name__ == "__main__":
    main()
