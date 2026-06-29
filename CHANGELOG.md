# Changelog

<!-- this_file: CHANGELOG.md -->

All notable changes to url22md are documented here.

## Unreleased (2026-06-29)

### Changed
- Simplified TOOLS registry from 9 slots to 6 distinct engines: trafilatura (1), crawl4ai (2), playwright (3), firecrawl (4), jina (5), readability (6). Fallback chain now runs 1→2→3→4→5→6.
- `process_urls()` signature: added `output_dir` parameter; writes individual `.md` files and JSONL records inline; returns summary dict `{total, processed, skipped, succeeded, failed}` instead of a list.
- `run_conversion()` now returns the same summary dict (was `list[dict]`).
- Default JSONL report filename changed from `_url2md.jsonl` to `_url22md.jsonl`.
- `convert_single_url()` no longer passes `minify` to tool functions; tools use their own defaults.

### Fixed
- `assess_quality()`: single-sentence texts (< 30 prose words) now score non-zero (0.05 base) instead of always 0.0.
- `assess_quality()`: repetitive-short-lines penalty requires at least 3 lines before activating.
- All 48 unit tests pass with zero network calls (full mock coverage).

### Added
- CI workflow (`.github/workflows/ci.yml`): test matrix Python 3.12/3.13, ruff lint, mypy type check.
- `docs/` Jekyll site explaining the web-to-Markdown extraction pipeline, tool reference, and Python API.
- `[tool.ruff.lint]` and `[tool.mypy]` sections in `pyproject.toml`.
- hatch test matrix extended to Python 3.13.

## 1.0.0 (2026-03-26)

### Initial Release

#### Core Features
- Multi-tool extraction pipeline: trafilatura → crawl4ai → playwright → firecrawl → jina → readability
- Quality-based tool selection with configurable threshold (default: 0.3)
- Cascading fallback: skips to next tool if quality insufficient
- CLI via Fire with comprehensive argument support

#### Processing
- Async concurrent URL handling (default: 5 concurrent)
- Per-tool timeout configuration (default: 30s)
- Crash-resilient JSONL reporting with immediate flush
- Already-processed URL skipping for resumable batches
- Progress bar with real-time statistics

#### Configuration
- Webshare proxy support (via WEBSHARE_* env vars)
- Cloud API support (FIRECRAWL_API_KEY, JINA_API_KEY)
- Filesystem-safe URL-to-filename conversion
- Batch cleanup options (--clean, --clean_all)

#### Quality Scoring
- Content-length evaluation (0-0.4 points)
- Markdown structure scoring (0-0.4 points)
- Links and images bonus (0.1 points)
- HTML tag penalty for residual markup
- Scale: 0.0 (empty/failed) to 1.0 (excellent extraction)

#### Testing
- Unit test suite for core utilities and tool functions
- Test fixtures for mock extraction results
- Quality assessment validation tests
