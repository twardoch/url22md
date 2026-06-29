---
layout: home
title: url22md
nav_order: 1
---

# url22md

Convert HTTP(S) URLs to clean Markdown. Six extraction engines, directed fallback chains, and a quality scorer keep the best result automatically.

---

## How web-to-Markdown extraction works

Fetching a web page and turning it into clean Markdown is harder than it sounds. Raw HTML is full of navigation menus, cookie banners, sidebars, ads, CSS class noise, and JavaScript payloads. Naive conversion produces thousands of lines of garbage around a 400-word article.

url22md solves this in three layers:

### Layer 1 — Fetch

Six engines fetch the raw page content:

- **No-JS engines** (trafilatura, readability+markdownify) issue a plain HTTP request. Fast, no dependencies, work on 80 % of the web.
- **JS-rendering engines** (crawl4ai, playwright) boot a headless Chromium browser, execute the page JavaScript, wait for network idle, then grab the rendered DOM. Needed for single-page apps.
- **Cloud APIs** (Firecrawl, Jina Reader) delegate fetching and cleaning to a hosted service. No local browser required; API key needed.

### Layer 2 — Extract and convert

After fetching:

- **trafilatura** uses a statistical model to identify the main text block, discarding boilerplate regions. It outputs Markdown natively.
- **readability-lxml** ports Mozilla's Readability algorithm to Python: it computes a content score for every `<div>` and keeps only the highest-scoring subtree. The HTML fragment is then converted to Markdown by **markdownify**.
- **Playwright + markdownify** grabs the fully rendered HTML and converts the whole DOM, relying on the quality scorer (layer 3) to flag low-signal output.
- **crawl4ai** adds stealth browsing, magic overlay removal, consent-popup dismissal, and user simulation before handing the DOM to its own Markdown generator.

### Layer 3 — Quality scoring

Every extracted Markdown string is scored 0.0–1.0 by `assess_quality()`:

| Signal | Points |
|--------|--------|
| Prose word count ≥ 200 | +0.40 |
| Prose word count 100–199 | +0.30 |
| Prose word count 30–99 | +0.15 |
| Sentence detection (≥ 3) | +0.15 |
| Heading present | +0.15 |
| Multiple paragraphs | +0.10 |
| Markdown links | +0.10 |
| HTML tags > 20 | −0.30 |
| CSS/JS boilerplate patterns | −0.10 to −0.30 |
| Brace-heavy content (JSON/CSS) | −0.10 to −0.20 |
| Repetitive short lines (nav menus) | −0.20 |
| Framework/CMS class noise | −0.20 |

If the score is below `QUALITY_THRESHOLD` (0.5), the cascade follows the configured fallback to the next engine and tries again. The first result meeting the threshold is returned; if nothing crosses it, the highest-scoring result is kept.

### Directed fallback chains

Each tool has a specific next-tool on failure — not a simple ordered list:

```
1 (trafilatura)
  → 2 (crawl4ai, anti-bot)
    → 3 (playwright, local browser)
      → 4 (firecrawl, cloud API)
        → 5 (jina, cloud API)
          → 6 (readability, article-focused)
            → stop
```

Use `--tool N` to start from any engine and follow its chain.

---

## Installation

```bash
pip install url22md
```

For JS-rendered pages, also install browser backends:

```bash
playwright install chromium
crawl4ai-setup
```

---

## Quick start

```bash
# Single URL → article.md
url22md --url "https://example.com/article"

# Batch from file
url22md --urls_path urls.txt --output_dir ./output

# Pipe
cat urls.txt | url22md --output_dir ./output

# Force a specific starting tool
url22md --url "https://spa.example.com" --tool 3
```

---

## Tool reference

| # | Engine | JS | Fallback | Requires |
|---|--------|----|----------|---------|
| 1 | trafilatura | No | → 2 | nothing |
| 2 | crawl4ai | Yes | → 3 | `crawl4ai-setup` |
| 3 | playwright | Yes | → 4 | `playwright install chromium` |
| 4 | firecrawl | Yes (cloud) | → 5 | `FIRECRAWL_API_KEY` |
| 5 | jina | Yes (cloud) | → 6 | `JINA_API_KEY` |
| 6 | readability | No | — | nothing |

---

## Python API

```python
from url22md import convert_single_url, run_conversion
import asyncio

# Single URL (async)
async def main():
    result = await convert_single_url("https://example.com", proxy_url=None)
    print(result.tool_name, result.quality_score)
    print(result.markdown[:200])

asyncio.run(main())

# Batch (sync, writes .md files)
summary = run_conversion(
    urls=["https://example.com", "https://docs.python.org/3/"],
    output_dir="./output",
    concurrency=5,
)
print(summary)
# {'total': 2, 'processed': 2, 'skipped': 0, 'succeeded': 2, 'failed': 0}
```

---

## JSONL report

Every run appends one record per URL to a crash-safe JSONL file:

```json
{"url": "https://example.com", "filename": "example-com.md", "tool": "trafilatura",
 "success": true, "quality": 0.72, "error": null, "timestamp": "2026-03-26T22:15:00+00:00"}
```

URLs already in the report are skipped on the next run. Use `--clean` to start fresh or `--Force` to re-process.

---

[GitHub](https://github.com/twardoch/url22md) · [PyPI](https://pypi.org/project/url22md/) · Apache 2.0
