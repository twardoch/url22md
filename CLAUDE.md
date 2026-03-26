# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**url22md** is a Python package that converts HTTP(S) URLs to Markdown files. It uses a multi-tool fallback cascade for resilient web content extraction, handles bulk URLs with smart concurrency, and produces a crash-safe JSONL progress report.

License: Apache 2.0. Repository: `https://github.com/twardoch/url22md`

## Build & Development Commands

```bash
# Install (editable)
uv pip install -e .
playwright install chromium
crawl4ai-setup

# Run tests (48 unit tests, no network needed)
python -m pytest tests/ -v

# Lint
uvx ruff check url22md/ tests/
uvx ruff format --check url22md/ tests/

# Full lint+format+test cycle
./test.sh

# Run CLI
python -m url22md --url "https://example.com"
python -m url22md --urls_path urls.txt --output_dir ./out
python -m url22md --url "https://example.com" --tool 1 --verbose
```

## Architecture: Multi-Tool Fallback Cascade

The core design is a cascading extraction pipeline. Each tool is tried in order; on failure or low-quality results, the next tool is attempted. Users can force a specific tool with `--tool N`.

| Tool | Engine | JS Support | Notes |
|------|--------|------------|-------|
| 1 | `trafilatura` | No | Fastest. Default first attempt. Native Markdown output |
| 2 | `crawl4ai` | Yes | Browser-based crawler with integrated Markdown |
| 3 | `playwright` + `markdownify` | Yes (real browser) | Local headless Chromium rendering |
| 4 | `firecrawl-py` | Yes (anti-bot) | Cloud API. Requires `FIRECRAWL_API_KEY` |
| 5 | Jina Reader API | Yes | Cloud API. Requires `JINA_API_KEY` |
| 6 (fallback) | `readability-lxml` + `markdownify` | No | Lightweight article-only extraction |

## CLI Interface (Fire-based)

Entry point: `url22md/__main__.py` using `fire` CLI framework.

Key arguments:
- `--url URL` — single URL
- `--urls_path FILE` — file with one URL per line (also accepts stdin)
- `--output_dir PATH` — output directory (default: current dir)
- `--jsonl PATH` — JSONL report path (default: `_url2md.jsonl` in output dir)
- `--tool N` — force specific tool (1-6)
- `--proxy` — enable Webshare proxy (requires `WEBSHARE_*` env vars)
- `--clean` — delete pre-existing report file before running
- `--clean_all` — delete report and all pre-existing output files

## Module Structure

| Module | Role |
|--------|------|
| `url22md/__init__.py` | Package exports: `run_conversion`, `ToolResult`, `TOOLS`, `url2filename` |
| `url22md/__main__.py` | Fire CLI entry point (`cli()` → `run_conversion()`) |
| `url22md/tools.py` | 6 async extraction tools + `ToolResult` dataclass + `assess_quality()` scorer |
| `url22md/converter.py` | Cascade orchestrator (`convert_single_url`), batch processor (`process_urls`), sync entry (`run_conversion`) |
| `url22md/utils.py` | `url2filename`, `build_proxy_url`, JSONL read/append, `read_urls_input`, logging setup |

## Key Design Decisions

- **Crash resilience**: JSONL report appended after each URL (not batched). Crash preserves prior work.
- **Skip already scraped**: URLs in existing JSONL report are skipped automatically.
- **Quality scoring**: `assess_quality()` scores 0.0–1.0 based on length, headings, paragraphs, links, HTML residue. Cascade continues if below `QUALITY_THRESHOLD` (0.3).
- **Filename generation**: `url2filename()` uses `slugify` + `pathvalidate` for filesystem-safe names.
- **Proxy**: Optional Webshare proxy via `--proxy` flag + `WEBSHARE_PROXY_USER`, `WEBSHARE_PROXY_PASS`, `WEBSHARE_DOMAIN_NAME`, `WEBSHARE_PROXY_PORT` env vars.
- **Concurrency**: `asyncio.Semaphore`-bounded concurrent processing (default 5, configurable via `--concurrency`).
- **Lazy imports**: Heavy libraries imported inside tool functions for graceful degradation if not installed.

## Reference Materials

- `issues/101.md` — full requirements specification
- `_private/repos/` — reference implementations of extraction tools
- `_private/tools1.md`, `_private/tools2.md` — tool comparison and recommendations
- `_private/typedrawers.py` — reference Webshare proxy pattern
