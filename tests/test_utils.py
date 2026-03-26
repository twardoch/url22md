# this_file: tests/test_utils.py
"""Tests for url22md utility functions."""

import json

from url22md.utils import (
    append_jsonl_record,
    build_proxy_url,
    read_jsonl_report,
    read_urls_input,
    setup_logging,
    url2filename,
)


# ---------------------------------------------------------------------------
# url2filename
# ---------------------------------------------------------------------------


def test_url2filename_basic():
    result = url2filename("https://example.com/page")
    assert isinstance(result, str)
    assert len(result) > 0


def test_url2filename_complex_url():
    result = url2filename("https://example.com/path?foo=bar&baz=1#section")
    assert isinstance(result, str)
    assert len(result) > 0
    # Should not contain raw query/fragment chars that are unsafe for filenames
    assert "?" not in result
    assert "#" not in result
    assert "&" not in result


def test_url2filename_preserves_domain():
    result = url2filename("https://example.com/some/path")
    # slugify normalises to lowercase; domain should appear somewhere
    assert "example" in result.lower()


def test_url2filename_empty_path():
    result = url2filename("https://example.com")
    assert isinstance(result, str)
    assert len(result) > 0
    assert "example" in result.lower()


# ---------------------------------------------------------------------------
# build_proxy_url
# ---------------------------------------------------------------------------


def test_build_proxy_url_disabled():
    result = build_proxy_url(use_proxy=False)
    assert result is None


def test_build_proxy_url_enabled_with_env(monkeypatch):
    monkeypatch.setenv("WEBSHARE_PROXY_USER", "user1")
    monkeypatch.setenv("WEBSHARE_PROXY_PASS", "pass1")
    monkeypatch.setenv("WEBSHARE_DOMAIN_NAME", "proxy.example.com")
    monkeypatch.setenv("WEBSHARE_PROXY_PORT", "8080")

    result = build_proxy_url(use_proxy=True)
    assert result == "http://user1:pass1@proxy.example.com:8080"


def test_build_proxy_url_missing_env(monkeypatch):
    # Remove all Webshare env vars so they are definitely absent
    for var in ("WEBSHARE_PROXY_USER", "WEBSHARE_PROXY_PASS", "WEBSHARE_DOMAIN_NAME", "WEBSHARE_PROXY_PORT"):
        monkeypatch.delenv(var, raising=False)

    result = build_proxy_url(use_proxy=True)
    assert result is None


# ---------------------------------------------------------------------------
# setup_logging
# ---------------------------------------------------------------------------


def test_setup_logging_verbose():
    # Should not raise
    setup_logging(verbose=True)


def test_setup_logging_quiet():
    # Should not raise
    setup_logging(verbose=False)


# ---------------------------------------------------------------------------
# read_jsonl_report
# ---------------------------------------------------------------------------


def test_read_jsonl_report_nonexistent(tmp_path):
    missing = tmp_path / "does_not_exist.jsonl"
    result = read_jsonl_report(missing)
    assert result == {}


def test_read_jsonl_report_valid(tmp_path):
    jsonl_file = tmp_path / "report.jsonl"
    records = [
        {"url": "https://a.com", "tool": "trafilatura", "success": True},
        {"url": "https://b.com", "tool": "jina", "success": False},
    ]
    jsonl_file.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    result = read_jsonl_report(jsonl_file)
    assert len(result) == 2
    assert "https://a.com" in result
    assert result["https://a.com"]["tool"] == "trafilatura"
    assert "https://b.com" in result


def test_read_jsonl_report_invalid_lines(tmp_path):
    jsonl_file = tmp_path / "report.jsonl"
    lines = [
        "not valid json{{",
        json.dumps({"url": "https://good.com", "success": True}),
        "{broken",
        json.dumps({"no_url_key": "value"}),  # valid JSON but no 'url' key — should be skipped
    ]
    jsonl_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = read_jsonl_report(jsonl_file)
    # Only the record with a 'url' key should appear
    assert len(result) == 1
    assert "https://good.com" in result


# ---------------------------------------------------------------------------
# append_jsonl_record
# ---------------------------------------------------------------------------


def test_append_jsonl_record(tmp_path):
    jsonl_file = tmp_path / "out.jsonl"
    record = {"url": "https://example.com", "success": True}

    append_jsonl_record(jsonl_file, record)

    lines = jsonl_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["url"] == "https://example.com"
    assert parsed["success"] is True


def test_append_jsonl_record_creates_parents(tmp_path):
    nested = tmp_path / "a" / "b" / "c" / "report.jsonl"
    assert not nested.parent.exists()

    append_jsonl_record(nested, {"url": "https://x.com"})

    assert nested.exists()
    parsed = json.loads(nested.read_text(encoding="utf-8").strip())
    assert parsed["url"] == "https://x.com"


# ---------------------------------------------------------------------------
# read_urls_input
# ---------------------------------------------------------------------------


def test_read_urls_input_single_url():
    result = read_urls_input(url="https://example.com", urls_path=None)
    assert result == ["https://example.com"]


def test_read_urls_input_file(tmp_path):
    url_file = tmp_path / "urls.txt"
    url_file.write_text("https://first.com\nhttps://second.com\n", encoding="utf-8")

    result = read_urls_input(url=None, urls_path=str(url_file))
    assert result == ["https://first.com", "https://second.com"]


def test_read_urls_input_dedup():
    # Provide the same URL via --url; combine with a file containing duplicates
    import os
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        fh.write("https://dup.com\nhttps://dup.com\nhttps://other.com\n")
        path = fh.name

    try:
        result = read_urls_input(url=None, urls_path=path)
        # Duplicates must be collapsed
        assert result.count("https://dup.com") == 1
        assert "https://other.com" in result
    finally:
        os.unlink(path)


def test_read_urls_input_filters_non_http(tmp_path):
    url_file = tmp_path / "mixed.txt"
    url_file.write_text(
        "https://valid.com\nftp://invalid.com\nfile:///local\nhttps://also-valid.com\n",
        encoding="utf-8",
    )

    result = read_urls_input(url=None, urls_path=str(url_file))
    assert "https://valid.com" in result
    assert "https://also-valid.com" in result
    assert "ftp://invalid.com" not in result
    assert "file:///local" not in result
