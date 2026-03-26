# Changelog

<!-- this_file: CHANGELOG.md -->

All notable changes to url22md are documented here.

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
