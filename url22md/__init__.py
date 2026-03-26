"""url22md - Convert HTTP(S) URLs to Markdown."""

from __future__ import annotations

# this_file: url22md/__init__.py
this_file = "url22md/__init__.py"

from importlib.metadata import version

try:
    __version__ = version("url22md")
except Exception:
    __version__ = "0.0.0"

from url22md.converter import run_conversion
from url22md.tools import QUALITY_THRESHOLD, TOOLS, ToolResult, assess_quality
from url22md.utils import url2filename

__all__ = [
    "QUALITY_THRESHOLD",
    "TOOLS",
    "ToolResult",
    "assess_quality",
    "run_conversion",
    "url2filename",
]
