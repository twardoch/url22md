# url22md

Convert HTTP(S) URLs to clean Markdown files. Uses seven extraction tools with directed fallback chains, scores quality, and keeps the best result. Handles hundreds of URLs concurrently, tracks progress in a crash-safe JSONL report, and skips already-processed URLs on restart.

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

url22md tries extraction tools following a directed fallback chain. Each tool has a specific next-tool on failure (not a simple linear sequence). After each attempt it scores the Markdown on prose word count, sentence structure, and penalises CSS/JS boilerplate. The first result scoring above the quality threshold (0.5) is accepted. If nothing meets the threshold, the best result is kept anyway.

| # | Tool | JS | Speed | Fallback | Needs |
|---|------|----|-------|----------|-------|
| 1 | [trafilatura](https://trafilatura.readthedocs.io/) | No | Fast | → 5 | nothing |
| 2 | trafilatura (strict) | No | Fast | none | nothing |
| 3 | [readability-lxml](https://github.com/buriy/python-readability) + [markdownify](https://github.com/matthewwithanm/python-markdownify) | No | Fast | → 5 | nothing |
| 4 | readability + markdownify (strict) | No | Fast | none | nothing |
| 5 | [playwright](https://playwright.dev/python/) + markdownify | Yes | Good | → 6 | `playwright install chromium` |
| 6 | [firecrawl](https://firecrawl.dev/) | Yes | Good | → 7 | `FIRECRAWL_API_KEY` |
| 7 | [Jina Reader](https://jina.ai/reader/) | Yes | Fast | → 2 | `JINA_API_KEY` |
| 8 | [crawl4ai](https://docs.crawl4ai.com/) | Yes | Good | → 6 | `crawl4ai-setup` |
| 9 | crawl4ai (fit) | Yes | Good | → 5 | `crawl4ai-setup` |

Tools 1-5, 8-9 run locally. Tools 6 and 7 call cloud APIs and require API keys. Tool 8 uses crawl4ai with stealth mode and anti-bot features (magic, user simulation, navigator override). Tool 9 adds a PruningContentFilter for article-only fit_markdown output. The default cascade starting from tool 1 follows: 1 → 5 → 6 → 7 → 2 (stop).

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
| `--format FMT` | `md` | Output format (see below) |
| `--output_dir DIR` | `.` | Directory for output files |
| `--jsonl PATH` | `DIR/_url2md.jsonl` | JSONL progress report path |

**Output formats**:

| Format | Behaviour |
|--------|-----------|
| `md` | One `.md` file per URL + JSONL report (default) |
| `all` | All results in a single `combined.md` (HR + H1 URL separators) + JSONL report |
| `json` | JSONL report with `markdown` content included (no `.md` files) |
| `-` | JSONL with `markdown` to stdout (no files written) |

**Extraction control**:

| Flag | Default | Description |
|------|---------|-------------|
| `--tool N` | 1 (cascade) | Start from tool N (1-9), follows fallback chain |
| `--proxy` | off | Route through Webshare proxy |
| `--Jobs N` | 5 | Max parallel URL conversions |
| `--Timeout N` | 30 | Per-tool timeout in seconds |

**Housekeeping**:

| Flag | Description |
|------|-------------|
| `--Force` | Re-process URLs even if already in the JSONL report |
| `--minify` | Article-only extraction: readability for tools 1-4, pruning for crawl4ai |
| `--clean` | Delete existing JSONL report before starting |
| `--Clean_all` | Delete report and all output files listed in it |
| `--verbose` | Debug-level logging to stderr |

## JSONL report

Each record contains:

```json
{"url": "https://example.com", "filename": "example-com.md", "tool": "trafilatura", "success": true, "quality": 0.7, "error": null, "timestamp": "2026-03-26T22:15:00+00:00"}
```

With `--format json` or `--format -`, a `"markdown"` key with the full content is included. On the next run, URLs already in the report are skipped. Use `--clean` to start fresh.

## Python API

`run_conversion()` returns a list of result records. Without `format`, no files are written:

```python
from url22md import run_conversion

records = run_conversion(
    urls=["https://example.com", "https://docs.python.org/3/"],
    concurrency=10,
)
for rec in records:
    print(rec["url"], rec["tool"], rec["quality"])
    print(rec["markdown"][:200])
```

Each record dict has: `url`, `filename`, `tool`, `success`, `quality`, `error`, `markdown`, `timestamp`.

To also write files, pass `format`:

```python
records = run_conversion(
    urls=["https://example.com"],
    output_dir="./output",
    format="md",       # writes individual .md files + JSONL report
)
```

Lower-level async access:

```python
import asyncio
from url22md import convert_single_url

async def main():
    result = await convert_single_url("https://example.com", proxy_url=None)
    print(result.tool_name, result.quality_score)
    print(result.markdown)

asyncio.run(main())
```

Individual tool functions:

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
