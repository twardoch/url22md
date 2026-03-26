# Dependencies

<!-- this_file: DEPENDENCIES.md -->

## Core Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| fire | ≥0.5 | CLI framework: auto-generates command-line interface from Python functions |
| rich | ≥13.0 | Terminal UI: progress bars, colored output, formatted logging |
| httpx | ≥0.27 | Async HTTP client: used by jina and readability tools |
| tenacity | ≥8.0 | Retry logic framework (imported but optional for custom retry strategies) |

## Extraction Tools

| Package | Version | Purpose |
|---------|---------|---------|
| trafilatura | ≥1.0 | Fast HTML-to-Markdown extraction without JS rendering |
| crawl4ai | ≥0.4 | Async headless browser crawler with native Markdown support |
| playwright | ≥1.40 | Real Chromium browser automation for complex JS-heavy sites |
| markdownify | ≥0.13 | HTML-to-Markdown conversion (used by playwright and readability) |
| firecrawl-py | ≥1.0 | Cloud-based intelligent web scraping with anti-bot features |
| readability-lxml | ≥0.8 | Article content extraction (Mozilla Readability algorithm) |
| html2text | ≥2024.2 | Alternative HTML-to-text converter (imported, used as fallback) |

## Utilities

| Package | Version | Purpose |
|---------|---------|---------|
| python-slugify[unidecode] | ≥8.0 | URL-to-filename slug generation with Unicode support |
| pathvalidate | ≥3.0 | Filesystem-safe filename sanitization |
| loguru | ≥0.7 | Structured logging with colored output and debug levels |

## Test Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| pytest | Latest | Unit test framework |
| pytest-cov | Latest | Code coverage measurement |

## Build System

- **hatchling**: Modern Python build backend (specified in pyproject.toml)
