# this_file: tests/test_converter.py
"""Tests for url22md converter orchestrator (mocked, no network calls)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from url22md.converter import convert_single_url, process_urls, run_conversion
from url22md.tools import ToolResult


def run(coro):
    """Run a coroutine synchronously without requiring pytest-asyncio."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GOOD_MARKDOWN = (
    "# Test Page\n\n"
    "This is a paragraph with enough content.\n\n"
    "Another paragraph here.\n\n"
    + "X" * 700
)

_GOOD_QUALITY = 0.8  # Above QUALITY_THRESHOLD


def _make_good_result(tool_name: str = "trafilatura") -> ToolResult:
    return ToolResult(
        markdown=_GOOD_MARKDOWN,
        tool_name=tool_name,
        success=True,
        quality_score=_GOOD_QUALITY,
    )


def _make_bad_result(tool_name: str = "trafilatura") -> ToolResult:
    return ToolResult(
        markdown="",
        tool_name=tool_name,
        success=False,
        error="simulated failure",
        quality_score=0.0,
    )


# ---------------------------------------------------------------------------
# convert_single_url
# ---------------------------------------------------------------------------


def test_convert_single_url_uses_specified_tool():
    """When tool=1 is given, only tool 1 (trafilatura) should be called."""
    good = _make_good_result("trafilatura")
    mock_fn = AsyncMock(return_value=good)

    with patch("url22md.converter.TOOLS", {1: ("trafilatura", mock_fn)}):
        result = run(convert_single_url("https://example.com", proxy_url=None, tool=1))

    mock_fn.assert_called_once_with(url="https://example.com", proxy_url=None, timeout=30)
    assert result.success is True
    assert result.tool_name == "trafilatura"


def test_convert_single_url_cascade_stops_on_first_good():
    """Cascade should stop as soon as a tool returns quality >= QUALITY_THRESHOLD."""
    good = _make_good_result("trafilatura")
    tool1_mock = AsyncMock(return_value=good)
    tool2_mock = AsyncMock(return_value=_make_good_result("crawl4ai"))

    with patch(
        "url22md.converter.TOOLS",
        {
            1: ("trafilatura", tool1_mock),
            2: ("crawl4ai", tool2_mock),
        },
    ):
        result = run(convert_single_url("https://example.com", proxy_url=None))

    tool1_mock.assert_called_once()
    tool2_mock.assert_not_called()
    assert result.tool_name == "trafilatura"


def test_convert_single_url_falls_back_on_low_quality():
    """If tool 1 returns low quality, tool 2 should be tried."""
    low = ToolResult(markdown="tiny", tool_name="trafilatura", success=True, quality_score=0.0)
    good = _make_good_result("crawl4ai")

    tool1_mock = AsyncMock(return_value=low)
    tool2_mock = AsyncMock(return_value=good)

    with patch(
        "url22md.converter.TOOLS",
        {
            1: ("trafilatura", tool1_mock),
            2: ("crawl4ai", tool2_mock),
        },
    ):
        result = run(convert_single_url("https://example.com", proxy_url=None))

    tool1_mock.assert_called_once()
    tool2_mock.assert_called_once()
    assert result.tool_name == "crawl4ai"
    assert result.success is True


def test_convert_single_url_returns_best_when_all_fail():
    """When all tools fail, the result with the highest quality_score is returned."""
    r1 = ToolResult(markdown="a", tool_name="t1", success=False, quality_score=0.1)
    r2 = ToolResult(markdown="bb", tool_name="t2", success=False, quality_score=0.05)

    with patch(
        "url22md.converter.TOOLS",
        {
            1: ("t1", AsyncMock(return_value=r1)),
            2: ("t2", AsyncMock(return_value=r2)),
        },
    ):
        result = run(convert_single_url("https://example.com", proxy_url=None))

    assert result.tool_name == "t1"
    assert result.quality_score == pytest.approx(0.1)


def test_convert_single_url_handles_exception_from_tool():
    """If a tool raises an exception, it should be caught and cascading continues."""
    crashing = AsyncMock(side_effect=RuntimeError("boom"))
    good = _make_good_result("crawl4ai")
    good_mock = AsyncMock(return_value=good)

    with patch(
        "url22md.converter.TOOLS",
        {
            1: ("trafilatura", crashing),
            2: ("crawl4ai", good_mock),
        },
    ):
        result = run(convert_single_url("https://example.com", proxy_url=None))

    assert result.success is True
    assert result.tool_name == "crawl4ai"


# ---------------------------------------------------------------------------
# process_urls
# ---------------------------------------------------------------------------


def test_process_urls_skips_existing(tmp_path):
    """URLs already in the JSONL report must not be re-processed."""
    jsonl = tmp_path / "report.jsonl"
    existing_url = "https://already-done.com"

    # Pre-populate the JSONL report with the URL
    jsonl.write_text(
        json.dumps({"url": existing_url, "success": True, "filename": "already-done.md"}) + "\n",
        encoding="utf-8",
    )

    mock_tool = AsyncMock(return_value=_make_good_result())

    with patch("url22md.converter.TOOLS", {1: ("trafilatura", mock_tool)}):
        summary = run(
            process_urls(
                urls=[existing_url],
                output_dir=tmp_path,
                jsonl_path=jsonl,
                tool=1,
            )
        )

    mock_tool.assert_not_called()
    assert summary["skipped"] == 1
    assert summary["processed"] == 0


def test_process_urls_processes_new_url(tmp_path):
    """A new URL not in the report should be processed and written to disk."""
    jsonl = tmp_path / "report.jsonl"
    url = "https://new.example.com/article"
    good = _make_good_result("trafilatura")
    mock_tool = AsyncMock(return_value=good)

    with patch("url22md.converter.TOOLS", {1: ("trafilatura", mock_tool)}):
        summary = run(
            process_urls(
                urls=[url],
                output_dir=tmp_path,
                jsonl_path=jsonl,
                tool=1,
            )
        )

    assert summary["processed"] == 1
    assert summary["succeeded"] == 1
    assert summary["failed"] == 0

    # Markdown file should have been written
    md_files = list(tmp_path.glob("*.md"))
    assert len(md_files) == 1

    # JSONL record should have been appended
    records = jsonl.read_text(encoding="utf-8").strip().splitlines()
    assert len(records) == 1
    rec = json.loads(records[0])
    assert rec["url"] == url
    assert rec["success"] is True


def test_process_urls_empty_list(tmp_path):
    """Empty URL list should return immediately with zero counts."""
    jsonl = tmp_path / "report.jsonl"
    summary = run(process_urls(urls=[], output_dir=tmp_path, jsonl_path=jsonl))
    assert summary == {"total": 0, "processed": 0, "skipped": 0, "succeeded": 0, "failed": 0}


# ---------------------------------------------------------------------------
# run_conversion
# ---------------------------------------------------------------------------


def test_run_conversion_clean_deletes_report(tmp_path):
    """--clean should delete the JSONL report before processing."""
    jsonl = tmp_path / "_url22md.jsonl"
    jsonl.write_text(
        json.dumps({"url": "https://old.com", "success": True, "filename": "old.md"}) + "\n",
        encoding="utf-8",
    )

    good = _make_good_result("trafilatura")
    mock_tool = AsyncMock(return_value=good)

    with patch("url22md.converter.TOOLS", {1: ("trafilatura", mock_tool)}):
        summary = run_conversion(
            urls=["https://new.com"],
            output_dir=str(tmp_path),
            jsonl_path=str(jsonl),
            clean=True,
            tool=1,
        )

    # The old URL was removed by --clean, so 'https://old.com' should NOT be
    # in the final report (it was not re-processed).
    final_records = {}
    for line in jsonl.read_text(encoding="utf-8").strip().splitlines():
        rec = json.loads(line)
        final_records[rec["url"]] = rec

    assert "https://old.com" not in final_records
    assert "https://new.com" in final_records
    assert summary["processed"] == 1


def test_run_conversion_clean_all_deletes_report_and_md_files(tmp_path):
    """--clean_all should delete both the JSONL report and referenced .md files."""
    md_file = tmp_path / "old-article.md"
    md_file.write_text("# Old article", encoding="utf-8")

    jsonl = tmp_path / "_url22md.jsonl"
    jsonl.write_text(
        json.dumps({"url": "https://old.com", "success": True, "filename": "old-article.md"}) + "\n",
        encoding="utf-8",
    )

    good = _make_good_result("trafilatura")
    mock_tool = AsyncMock(return_value=good)

    with patch("url22md.converter.TOOLS", {1: ("trafilatura", mock_tool)}):
        run_conversion(
            urls=["https://new.com"],
            output_dir=str(tmp_path),
            jsonl_path=str(jsonl),
            clean_all=True,
            tool=1,
        )

    # Old .md file should be gone
    assert not md_file.exists()


def test_run_conversion_no_urls(tmp_path):
    """Empty URL list should return immediately without touching the filesystem."""
    summary = run_conversion(urls=[], output_dir=str(tmp_path))
    assert summary == {"total": 0, "processed": 0, "skipped": 0, "succeeded": 0, "failed": 0}


def test_run_conversion_returns_summary_keys(tmp_path):
    """The returned dict must always contain the five expected summary keys."""
    good = _make_good_result("trafilatura")
    mock_tool = AsyncMock(return_value=good)

    with patch("url22md.converter.TOOLS", {1: ("trafilatura", mock_tool)}):
        summary = run_conversion(
            urls=["https://example.com"],
            output_dir=str(tmp_path),
            tool=1,
        )

    assert set(summary.keys()) == {"total", "processed", "skipped", "succeeded", "failed"}
