"""Pytest path setup so tests can import the crawler and analysis packages.

Adds the repo root (for the ``analysis`` package) and the ``crawler`` dir
(for the ``seo_crawler`` package) to ``sys.path``.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CRAWLER = os.path.join(ROOT, "crawler")

for path in (ROOT, CRAWLER):
    if path not in sys.path:
        sys.path.insert(0, path)
