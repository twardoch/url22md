"""url22md - Convert HTTP(S) URLs to Markdown."""
# this_file: src/url22md/__init__.py

from __future__ import annotations

from url22md.converter import run_conversion
from url22md.tools import FALLBACKS, QUALITY_THRESHOLD, TOOLS, ToolResult, assess_quality
from url22md.utils import url2filename

try:
    from url22md._version import __version__
except ImportError:
    try:
        from importlib.metadata import version

        __version__ = version("url22md")
    except Exception:
        __version__ = "0.0.0"

this_file = "src/url22md/__init__.py"

__all__ = [
    "FALLBACKS",
    "QUALITY_THRESHOLD",
    "TOOLS",
    "ToolResult",
    "assess_quality",
    "run_conversion",
    "url2filename",
]
