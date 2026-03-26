# url22md

<!-- this_file: README.md -->

Convert HTTP(S) URLs to Markdown with intelligent tool cascading and crash-resilient batch processing.

## Installation

```bash
uv pip install url22md
```

### Setup browser tools

For Playwright and Crawl4ai support:

```bash
playwright install chromium
crawl4ai-setup
```

### Configure cloud APIs (optional)

Set environment variables for cloud-based extractors:

```bash
export FIRECRAWL_API_KEY="your-api-key"
export JINA_API_KEY="your-api-key"
export WEBSHARE_PROXY_USER="your-user"
export WEBSHARE_PROXY_PASS="your-pass"
export WEBSHARE_DOMAIN_NAME="your-domain"
export WEBSHARE_PROXY_PORT="your-port"
```

## Quick Usage

### Single URL (CLI)

```bash
url22md --url "https://example.com"
```

### Batch processing

```bash
# From file (one URL per line)
url22md --urls_path urls.txt

# From stdin
cat urls.txt | url22md

# With custom output directory
url22md --urls_path urls.txt --output_dir ./markdown
```

### Python API

```python
from url22md.converter import run_conversion

summary = run_conversion(
    urls=["https://example.com", "https://another.com"],
    output_dir="./markdown",
    concurrency=5,
    timeout=30,
)
print(f"Success: {summary['succeeded']}/{summary['total']}")
```

## Tool Cascade

url22md tries up to 6 extraction tools in order, skipping to the next if quality is insufficient:

| # | Tool | Type | Speed | JS | Quality |
|---|------|------|-------|----|----|
| 1 | trafilatura | Native HTML parser | Fast | No | High |
| 2 | crawl4ai | Headless browser | Medium | Yes | Very High |
| 3 | playwright | Real Chromium | Slow | Yes | Very High |
| 4 | firecrawl | Cloud API | Fast | Yes | High |
| 5 | jina | Cloud API | Fast | No | Medium |
| 6 | readability | Article extractor | Fast | No | Medium |

Stops at first tool with quality ≥ 0.3, or returns best result if none meet threshold.

## CLI Reference

```bash
url22md [OPTIONS]
```

### Input Options

- `--url URL` : Single URL to convert
- `--urls_path PATH` : File with one URL per line

### Output Options

- `--output_dir DIR` : Output directory for .md files (default: `.`)
- `--jsonl PATH` : Report file path (default: `output_dir/_url2md.jsonl`)

### Processing Options

- `--tool N` : Force specific tool (1-6). If omitted, cascade through all.
- `--concurrency N` : Max concurrent URLs (default: 5)
- `--timeout N` : Seconds per tool attempt (default: 30)
- `--proxy` : Use Webshare proxy if WEBSHARE_* env vars set

### Control Options

- `--clean` : Delete existing report before running
- `--clean_all` : Delete report and all output files before running
- `--verbose` : Enable debug logging

## Report Format

The JSONL report (`_url2md.jsonl`) tracks all conversions:

```json
{"url": "https://example.com", "filename": "example-com.md", "tool": "trafilatura", "success": true, "quality": 0.65, "error": null, "timestamp": "2026-03-26T10:30:45+00:00"}
```

Already-processed URLs are automatically skipped on re-run, making batch operations resumable after crashes.

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `WEBSHARE_PROXY_USER` | Webshare username |
| `WEBSHARE_PROXY_PASS` | Webshare password |
| `WEBSHARE_DOMAIN_NAME` | Webshare domain |
| `WEBSHARE_PROXY_PORT` | Webshare port |
| `FIRECRAWL_API_KEY` | Firecrawl API key |
| `JINA_API_KEY` | Jina Reader API key |

## License

Apache License 2.0
