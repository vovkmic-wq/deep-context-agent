"""Tests for bounded multi-line CLI input."""

from __future__ import annotations

import argparse
import builtins
from io import StringIO
from pathlib import Path

import pytest

from context_agent.cli import (
    MAX_PROMPT_FILE_BYTES,
    read_chat_query,
    read_prompt_file,
    resolve_ask_query,
)


def test_chat_paste_mode_returns_one_multiline_query(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    lines = iter(["/paste", "first line", "second line", "/end"])
    monkeypatch.setattr(builtins, "input", lambda _: next(lines))

    assert read_chat_query() == "first line\nsecond line"


def test_chat_inline_paste_keeps_the_first_line(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    lines = iter(["/paste first line", "second line", "/end"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(lines))

    assert read_chat_query() == "first line\nsecond line"
    assert "Paste mode" in capsys.readouterr().out


def test_chat_paste_mode_can_be_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines = iter(["/paste", "do not send", "/cancel"])
    monkeypatch.setattr(builtins, "input", lambda _: next(lines))
    assert read_chat_query() == ""


def test_chat_paste_mode_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    lines = iter(["/paste", "x" * (MAX_PROMPT_FILE_BYTES + 1)])
    monkeypatch.setattr(builtins, "input", lambda _: next(lines))
    with pytest.raises(ValueError, match="2 MiB"):
        read_chat_query()


def test_ask_reads_one_multiline_prompt_from_utf8_file(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("line one\nline two\n", encoding="utf-8")
    args = argparse.Namespace(file=prompt, query=None)
    assert resolve_ask_query(args, StringIO()) == "line one\nline two\n"


def test_ask_reads_stdin_and_rejects_ambiguous_sources(tmp_path: Path) -> None:
    stdin_args = argparse.Namespace(file=None, query="-")
    assert resolve_ask_query(stdin_args, StringIO("one\ntwo")) == "one\ntwo"

    prompt = tmp_path / "prompt.txt"
    prompt.write_text("file", encoding="utf-8")
    ambiguous = argparse.Namespace(file=prompt, query="argument")
    with pytest.raises(ValueError, match="either"):
        resolve_ask_query(ambiguous, StringIO())


def test_prompt_file_must_be_utf8_and_bounded(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.txt"
    invalid.write_bytes(b"\xff\xfe")
    with pytest.raises(ValueError, match="UTF-8"):
        read_prompt_file(invalid)

    oversized = tmp_path / "oversized.txt"
    oversized.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="too large"):
        read_prompt_file(oversized)

    stdin_args = argparse.Namespace(file=None, query="-")
    with pytest.raises(ValueError, match="2 MiB"):
        resolve_ask_query(
            stdin_args,
            StringIO("я" * (MAX_PROMPT_FILE_BYTES // 2 + 1)),
        )
