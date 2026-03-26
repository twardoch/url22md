# url22md TODO

<!-- this_file: TODO.md -->

## Completed

- [x] Core package structure with __init__, __main__, converter, tools, utils modules
- [x] Six extraction tools: trafilatura, crawl4ai, playwright+markdownify, firecrawl, jina, readability
- [x] Tool cascading with quality scoring (0.0-1.0 scale)
- [x] Quality threshold enforcement (0.3 minimum)
- [x] CLI via Fire with all argument support
- [x] Async concurrent URL processing with semaphore
- [x] Crash-resilient JSONL report tracking
- [x] Already-processed URL skipping
- [x] Webshare proxy support
- [x] --clean and --clean_all flags
- [x] Progress bar with statistics
- [x] Unit test structure

## Future

- [ ] Integration tests with live URLs
- [ ] Custom quality threshold CLI flag
- [ ] HTML output format option
- [ ] Rate limiting per domain
- [ ] Cookie/auth support for protected pages
- [ ] Plugin system for custom extractors
- [ ] Batch analytics and failure categorization
- [ ] Retry strategy with exponential backoff
- [ ] Test coverage expansion to 80%+
