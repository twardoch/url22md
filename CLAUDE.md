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
uvx hatch test

# Lint
uvx ruff check url22md/ tests/
uvx ruff format --check url22md/ tests/

# Build
uvx hatch build

# Publish
uv publish

# Version: derived from git tags via hatch-vcs (setuptools-scm)
# Tag with v1.2.3 → version 1.2.3. Dirty/untagged → X.Y.Z.dev0

# Run CLI
python -m url22md --url "https://example.com"
python -m url22md --urls_path urls.txt --output_dir ./out
python -m url22md --url "https://example.com" --tool 1 --verbose
```

## Architecture: Directed Fallback Chains

The core design uses directed fallback chains (not a simple linear cascade). Each tool has a specific next-tool on failure or low quality. `--tool N` starts from tool N and follows its chain.

| Tool | Engine | JS | Fallback | Notes |
|------|--------|----|----------|-------|
| 1 | `trafilatura` | No | → 5 | Fastest. Default start. Native Markdown output |
| 2 | `trafilatura` (strict) | No | none | Same engine, no fallback |
| 3 | `readability-lxml` + `markdownify` | No | → 5 | Article-focused extraction |
| 4 | `readability-lxml` + `markdownify` (strict) | No | none | Same engine, no fallback |
| 5 | `playwright` + `markdownify` | Yes | → 6 | Local headless Chromium rendering |
| 6 | `firecrawl-py` | Yes | → 7 | Cloud API. Requires `FIRECRAWL_API_KEY` |
| 7 | Jina Reader API | Yes | → 2 | Cloud API. Requires `JINA_API_KEY` |
| 8 | `crawl4ai` | Yes | → 6 | Stealth + anti-bot (magic, user sim). Requires `crawl4ai-setup` |
| 9 | `crawl4ai` (fit) | Yes | → 5 | PruningContentFilter for fit_markdown. Requires `crawl4ai-setup` |

Default chain: 1 → 5 → 6 → 7 → 2 (stop).

## CLI Interface (Fire-based)

Entry point: `url22md/__main__.py` using `fire` CLI framework.

Key arguments:
- `--url URL` — single URL
- `--urls_path FILE` — file with one URL per line (also accepts stdin)
- `--output_dir PATH` — output directory (default: current dir)
- `--jsonl PATH` — JSONL report path (default: `_url2md.jsonl` in output dir)
- `--tool N` — start from tool N (1-9), follows fallback chain
- `--force` — re-process URLs even if already in the JSONL report
- `--minify` — article-only extraction: readability for tools 1-4, minify for tool 5
- `--proxy` — enable Webshare proxy (requires `WEBSHARE_*` env vars)
- `--clean` — delete pre-existing report file before running
- `--clean_all` — delete report and all pre-existing output files

## Module Structure

| Module | Role |
|--------|------|
| `url22md/__init__.py` | Package exports: `run_conversion`, `ToolResult`, `TOOLS`, `FALLBACKS`, `url2filename` |
| `url22md/__main__.py` | Fire CLI entry point (`cli()` → `run_conversion()`) |
| `url22md/tools.py` | 7 async extraction tools (9 registry entries) + `ToolResult` dataclass + `assess_quality()` scorer + `TOOLS` registry + `FALLBACKS` chain |
| `url22md/converter.py` | Fallback chain orchestrator (`convert_single_url`), batch processor (`process_urls`), sync entry (`run_conversion`) |
| `url22md/utils.py` | `url2filename`, `build_proxy_url`, JSONL read/append, `read_urls_input`, logging setup |

## Key Design Decisions

- **Crash resilience**: JSONL report appended after each URL (not batched). Crash preserves prior work.
- **Skip already scraped**: URLs in existing JSONL report are skipped automatically.
- **Quality scoring**: `assess_quality()` scores 0.0–1.0 based on prose word count, sentence detection, headings, paragraphs, links. Penalises CSS/JS boilerplate, excessive braces, short-line repetition, framework class noise. Fallback continues if below `QUALITY_THRESHOLD` (0.5).
- **Filename generation**: `url2filename()` uses `slugify` + `pathvalidate` for filesystem-safe names.
- **Proxy**: Optional Webshare proxy via `--proxy` flag + `WEBSHARE_PROXY_USER`, `WEBSHARE_PROXY_PASS`, `WEBSHARE_DOMAIN_NAME`, `WEBSHARE_PROXY_PORT` env vars.
- **Concurrency**: `asyncio.Semaphore`-bounded concurrent processing (default 5, configurable via `--concurrency`).
- **Lazy imports**: Heavy libraries imported inside tool functions for graceful degradation if not installed.

## Reference Materials

- `issues/101.md` — full requirements specification
- `_private/repos/` — reference implementations of extraction tools
- `_private/tools1.md`, `_private/tools2.md` — tool comparison and recommendations
- `_private/typedrawers.py` — reference Webshare proxy pattern
