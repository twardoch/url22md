# url22md Project Plan

<!-- this_file: PLAN.md -->

## Project Goals

Create a resilient, production-ready Python package that converts URLs to Markdown with intelligent tool selection, crash-safe batch processing, and concurrent execution.

## Architecture

### Multi-Tool Cascade

Six extraction backends with quality-based fallback:
- **Tier 1 (Fast, no JS)**: trafilatura
- **Tier 2 (JS-capable headless)**: crawl4ai, playwright
- **Tier 3 (Cloud APIs)**: firecrawl, jina
- **Tier 4 (Article-focused fallback)**: readability

Quality threshold of 0.3 determines acceptance; best result returned if none qualify.

### Crash-Resilient JSONL Reporting

- Append-only JSONL format with immediate flush
- Each URL processed = one appended line
- Crash tolerance: resume from last complete record
- Already-processed URLs automatically skipped on re-run

### Async Concurrency

- Semaphore-gated concurrent processing (default: 5 concurrent)
- Per-tool timeout (default: 30 seconds)
- Progress bar with real-time statistics

### Proxy Support

- Webshare proxy integration via environment variables
- Per-tool proxy parameter handling
- Graceful fallback if proxy vars missing

## Module Structure

| Module | Purpose |
|--------|---------|
| `__init__.py` | Package metadata and version |
| `__main__.py` | Fire CLI with full argument parsing |
| `converter.py` | Orchestration: cascading tools, concurrent processing, report writing |
| `tools.py` | Six extraction tool implementations with quality scoring |
| `utils.py` | Utilities: URL→filename mapping, proxy config, JSONL I/O, logging |

## Future Enhancements

- Custom quality threshold CLI flag `--quality-threshold`
- Additional tools (HTMLSession, Selenium-based extraction)
- Output formats (JSON, CSV, HTML)
- Per-domain rate limiting
- Cookie/auth support for protected pages
- Plugin system for custom extractors
- Batch resume with failure analysis
