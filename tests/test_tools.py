# this_file: tests/test_tools.py
"""Tests for url22md extraction tools (unit tests, no HTTP calls)."""

import pytest

from url22md.tools import (
    QUALITY_THRESHOLD,
    TOOLS,
    ToolResult,
    assess_quality,
)


# ---------------------------------------------------------------------------
# assess_quality
# ---------------------------------------------------------------------------


def test_assess_quality_empty():
    assert assess_quality("") == 0.0


def test_assess_quality_whitespace_only():
    assert assess_quality("   \n\t  ") == 0.0


def test_assess_quality_short():
    # Fewer than 100 chars → lowest length band (0.1), no structural bonuses
    score = assess_quality("Short text.")
    assert 0.0 < score <= 0.2


def test_assess_quality_good_markdown():
    # Well-structured markdown: long, has heading, multiple paragraphs, a link
    md = (
        "# My Article\n\n"
        "This is the first paragraph with enough content to be meaningful.\n\n"
        "This is the second paragraph which adds more substance to the article.\n\n"
        "Here is a [link to somewhere](https://example.com) for reference.\n\n"
        + "A" * 600  # push well past 500-char threshold
    )
    score = assess_quality(md)
    assert score > 0.5, f"Expected >0.5, got {score}"


def test_assess_quality_html_penalty():
    # Text loaded with HTML tags should receive a penalty relative to clean text
    clean = "A" * 600 + "\n\n" + "B" * 600
    html_heavy = clean + " " + " ".join(f"<div{i}>" for i in range(15))
    score_clean = assess_quality(clean)
    score_html = assess_quality(html_heavy)
    assert score_html < score_clean, "HTML-heavy text should score lower than clean text"


def test_assess_quality_with_heading_bonus():
    text = "# Heading\n\n" + "X" * 200
    score_with = assess_quality(text)
    text_no_heading = "X" * 200
    score_without = assess_quality(text_no_heading)
    assert score_with > score_without


def test_assess_quality_with_paragraphs_bonus():
    # Two blank-line-separated blocks give the paragraph bonus
    text = "Para one.\n\nPara two.\n\nPara three.\n" + "X" * 200
    score = assess_quality(text)
    assert score > assess_quality("X" * 200)


def test_assess_quality_clamped_max():
    # Even a perfect document should not exceed 1.0
    md = (
        "# Title\n\n"
        + "A" * 1100
        + "\n\nParagraph two.\n\nParagraph three.\n\n"
        + "[link](https://x.com)"
    )
    score = assess_quality(md)
    assert score <= 1.0


def test_assess_quality_clamped_min():
    # Even with an HTML penalty the score must not go below 0.0
    many_tags = " ".join(f"<tag{i}>" for i in range(30))
    score = assess_quality(many_tags)
    assert score >= 0.0


# ---------------------------------------------------------------------------
# ToolResult dataclass
# ---------------------------------------------------------------------------


def test_tool_result_dataclass_required_fields():
    r = ToolResult(markdown="# Hello", tool_name="trafilatura", success=True)
    assert r.markdown == "# Hello"
    assert r.tool_name == "trafilatura"
    assert r.success is True
    assert r.error is None
    assert r.quality_score == 0.0


def test_tool_result_dataclass_all_fields():
    r = ToolResult(
        markdown="content",
        tool_name="jina",
        success=False,
        error="timeout",
        quality_score=0.42,
    )
    assert r.error == "timeout"
    assert r.quality_score == pytest.approx(0.42)


def test_tool_result_dataclass_is_mutable():
    r = ToolResult(markdown="", tool_name="readability", success=False)
    r.quality_score = 0.9
    assert r.quality_score == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# TOOLS registry
# ---------------------------------------------------------------------------


def test_tools_registry_size():
    assert len(TOOLS) == 6


def test_tools_registry_keys():
    assert set(TOOLS.keys()) == {1, 2, 3, 4, 5, 6}


def test_tools_registry_names():
    expected_names = {
        1: "trafilatura",
        2: "crawl4ai",
        3: "playwright",
        4: "firecrawl",
        5: "jina",
        6: "readability",
    }
    for tid, (name, _fn) in TOOLS.items():
        assert name == expected_names[tid], f"Tool {tid}: expected '{expected_names[tid]}', got '{name}'"


def test_tools_registry_callables():
    import inspect

    for tid, (name, fn) in TOOLS.items():
        assert callable(fn), f"Tool {tid} ({name}) is not callable"
        # Each entry should be a coroutine function (async def)
        assert inspect.iscoroutinefunction(fn), f"Tool {tid} ({name}) should be async"


# ---------------------------------------------------------------------------
# QUALITY_THRESHOLD
# ---------------------------------------------------------------------------


def test_quality_threshold_range():
    assert 0.0 < QUALITY_THRESHOLD < 1.0


def test_quality_threshold_type():
    assert isinstance(QUALITY_THRESHOLD, float)
