# url22md

Convert HTTP(S) URLs to clean Markdown files. Tries six extraction tools in cascade order, scores quality, and keeps the best result. Handles hundreds of URLs concurrently, tracks progress in a crash-safe JSONL report, and skips already-processed URLs on restart.

## Install

```bash
pip install url22md
```

For full functionality (JS-rendered pages), also run:

```bash
playwright install chromium
crawl4ai-setup
```

## Quick start

Single URL:

```bash
url22md --url "https://example.com/article"
```

Batch from file:

```bash
url22md --urls_path urls.txt --output_dir ./output
```

Pipe from stdin:

```bash
cat urls.txt | url22md --output_dir ./output
```

Force a specific tool:

```bash
url22md --url "https://spa-heavy-site.com" --tool 3
```

## How it works

url22md tries up to six extraction tools in order. After each attempt it scores the Markdown output on length, headings, paragraph structure, links, and residual HTML. The first result scoring above the quality threshold (0.3) is accepted. If nothing meets the threshold, the best result is kept anyway.

| # | Tool | JS rendering | Speed | Needs |
|---|------|-------------|-------|-------|
| 1 | [trafilatura](https://trafilatura.readthedocs.io/) | No | Fast | nothing |
| 2 | [crawl4ai](https://docs.crawl4ai.com/) | Yes (headless browser) | Good | `crawl4ai-setup` |
| 3 | [playwright](https://playwright.dev/python/) + [markdownify](https://github.com/matthewwithanm/python-markdownify) | Yes (real Chromium) | Good | `playwright install chromium` |
| 4 | [firecrawl](https://firecrawl.dev/) | Yes (cloud, anti-bot) | Good | `FIRECRAWL_API_KEY` |
| 5 | [Jina Reader](https://jina.ai/reader/) | Yes (cloud) | Fast | `JINA_API_KEY` |
| 6 | [readability-lxml](https://github.com/buriy/python-readability) + markdownify | No | Fast | nothing |

Tools 1, 2, 3, and 6 run locally. Tools 4 and 5 call cloud APIs and require API keys.

## CLI reference

```
url22md [flags]
```

**Input** (at least one required):

| Flag | Description |
|------|-------------|
| `--url URL` | Single URL to convert |
| `--urls_path FILE` | Text file with one URL per line |
| *(stdin)* | Pipe URLs, one per line |

**Output**:

| Flag | Default | Description |
|------|---------|-------------|
| `--output_dir DIR` | `.` | Directory for `.md` files |
| `--jsonl PATH` | `DIR/_url2md.jsonl` | JSONL progress report path |

**Extraction control**:

| Flag | Default | Description |
|------|---------|-------------|
| `--tool N` | cascade 1-6 | Force a specific tool (1-6) |
| `--proxy` | off | Route through Webshare proxy |
| `--concurrency N` | 5 | Max parallel URL conversions |
| `--timeout N` | 30 | Per-tool timeout in seconds |

**Housekeeping**:

| Flag | Description |
|------|-------------|
| `--clean` | Delete existing JSONL report before starting |
| `--clean_all` | Delete report and all `.md` files listed in it |
| `--verbose` | Debug-level logging to stderr |

## JSONL report

Each processed URL appends one JSON line to the report immediately (crash-safe):

```json
{"url": "https://example.com", "filename": "example-com.md", "tool": "trafilatura", "success": true, "quality": 0.7, "error": null, "timestamp": "2026-03-26T22:15:00+00:00"}
```

On the next run, URLs already in the report are skipped. Use `--clean` to start fresh.

## Python API

```python
from url22md import run_conversion

summary = run_conversion(
    urls=["https://example.com", "https://docs.python.org/3/"],
    output_dir="./output",
    concurrency=10,
    tool=1,          # optional: force trafilatura only
    verbose=True,
)
print(summary)
# {"total": 2, "processed": 2, "skipped": 0, "succeeded": 2, "failed": 0}
```

Lower-level access:

```python
import asyncio
from url22md.tools import extract_with_trafilatura, extract_with_playwright

async def main():
    result = await extract_with_trafilatura("https://example.com")
    if not result.success:
        result = await extract_with_playwright("https://example.com")
    print(result.markdown)

asyncio.run(main())
```

Quality scoring:

```python
from url22md import assess_quality

score = assess_quality("# Title\n\nA paragraph.\n\nAnother paragraph.")
print(score)  # 0.6
```

## Proxy support

url22md supports [Webshare](https://www.webshare.io/) proxies. Set these environment variables:

```bash
export WEBSHARE_PROXY_USER="your_user"
export WEBSHARE_PROXY_PASS="your_pass"
export WEBSHARE_DOMAIN_NAME="proxy.webshare.io"
export WEBSHARE_PROXY_PORT="80"
```

Then pass `--proxy`:

```bash
url22md --url "https://geo-restricted-site.com" --proxy
```

## Cloud API keys

For tools 4 and 5, set the corresponding environment variable:

```bash
export FIRECRAWL_API_KEY="fc-..."    # tool 4
export JINA_API_KEY="jina_..."       # tool 5
```

These tools are only attempted when their API key is present. Without them, the cascade skips to the next tool.

## Output structure

```
output_dir/
  example-com.md                     # Markdown content
  docs-python-org-3.md               # Markdown content
  _url2md.jsonl                      # Progress report
```

Filenames are generated from URLs using `slugify` + `pathvalidate`, producing filesystem-safe names like `example-com-article-id-42.md`.

## Development

```bash
git clone https://github.com/twardoch/url22md
cd url22md
uv pip install --system -e .
playwright install chromium

# Tests (48 unit tests, no network required)
uvx hatch test

# Lint
uvx ruff check src/url22md/ tests/

# Build
uvx hatch build

# Publish
uv publish
```

Versioning is derived from git tags via [hatch-vcs](https://github.com/ofek/hatch-vcs). Tag `v1.2.3` produces version `1.2.3`.

## License

[Apache 2.0](LICENSE)
