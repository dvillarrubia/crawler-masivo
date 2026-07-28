# Tests

Unit tests for the crawler's pure extraction and validation logic.

## Running

```bash
pip install -r tests/requirements.txt
pytest            # from the repo root
```

`tests/conftest.py` puts the repo root and `crawler/` on `sys.path`, so the
tests import `seo_crawler.extractors` and `analysis.sd_validation` directly —
no full crawler install (scrapy, playwright, extruct, trafilatura) needed, as
those are imported lazily by the modules under test.

## Coverage

| File | Covers |
|------|--------|
| `test_extractors.py` | URL utilities, `extract_meta` (canonical/og resolution to absolute), `effective_base_url` (`<base href>`), `extract_links` (no per-page dedup, follow/nofollow, link-type, internal), `_detect_link_position` (nearest-ancestor wins), `extract_headings` (skips template/noscript/svg), `extract_hreflang`, `extract_resources` (mixed content, srcset), meta-refresh, security headers, `compute_indexability_status`, text ratio, pixel widths |
| `test_sd_validation.py` | Structured-data validation: missing `@type` (error), missing required property for known rich-result types (warning), unknown types / odd shapes (ok, no false positives), `@graph` and bare-list handling |

The `test_*.png` / `*.mjs` files in this directory are older Playwright UI
inspection scripts, unrelated to the pytest suite.
