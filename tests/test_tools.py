"""Tests for web, retrieval, and safe filesystem tools."""

import json
import socket
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from context_agent.context_store import ContextStore
from context_agent.errors import PathSecurityError, WebSearchError
from context_agent.tools import (
    build_agent_tools,
    fetch_public_web_page,
    search_web,
)


class FakeSearchClient:
    """Deterministic DDGS-compatible test client."""

    def text(self, query: str, **kwargs: Any) -> list[dict[str, str]]:
        assert query == "current docs"
        assert kwargs["max_results"] == 2
        return [
            {
                "title": "Official page",
                "href": "https://example.test/docs",
                "body": "Current documentation.",
            }
        ]


class FailingSearchClient:
    def text(self, query: str, **kwargs: Any) -> list[dict[str, str]]:
        del query, kwargs
        raise TimeoutError("network timeout")


class FakePageFetcher:
    def __call__(self, url: str, *, max_chars: int) -> dict[str, Any]:
        assert url == "https://example.test/docs"
        assert max_chars == 2000
        return {
            "url": url,
            "title": "Official page",
            "content_type": "text/html",
            "text": "Version 9.8.7",
            "truncated": False,
        }


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body
        self.headers = Message()
        self.headers["Content-Type"] = "text/html; charset=utf-8"

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        del args

    def geturl(self) -> str:
        return "https://example.test/final"

    def read(self, size: int) -> bytes:
        return self._body[:size]


class FakeOpener:
    def open(self, request: Any, *, timeout: float) -> FakeResponse:
        assert request.full_url == "https://example.test/docs"
        assert timeout == 10.0
        body = (
            b"<html><head><title>Release</title><script>ignore()</script></head>"
            b"<body>Current version: 9.8.7</body></html>"
        )
        return FakeResponse(body)


def fake_resolver(
    host: str,
    port: int,
    family: int,
    socket_type: int,
) -> list[tuple[Any, ...]]:
    assert host == "example.test"
    assert port == 443
    assert family == 0
    assert socket_type == socket.SOCK_STREAM
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def fake_opener_factory(resolver: Any) -> FakeOpener:
    assert resolver is fake_resolver
    return FakeOpener()


def _tool_map(tools: list[Any]) -> dict[str, Any]:
    return {tool.name: tool for tool in tools}


def test_web_search_normalizes_results() -> None:
    results = search_web(
        "current docs",
        max_results=2,
        client_factory=FakeSearchClient,
    )
    assert results == [
        {
            "title": "Official page",
            "url": "https://example.test/docs",
            "snippet": "Current documentation.",
        }
    ]


def test_web_search_has_bounded_errors() -> None:
    with pytest.raises(WebSearchError, match="TimeoutError"):
        search_web("current docs", client_factory=FailingSearchClient)
    with pytest.raises(WebSearchError, match="two characters"):
        search_web("x")


def test_public_web_page_fetch_extracts_text_and_blocks_scripts() -> None:
    page = fetch_public_web_page(
        "https://example.test/docs",
        max_chars=2000,
        resolver=fake_resolver,
        opener_factory=fake_opener_factory,
    )
    assert page["url"] == "https://example.test/final"
    assert page["title"] == "Release"
    assert "Current version: 9.8.7" in page["text"]
    assert "ignore()" not in page["text"]
    assert page["truncated"] is False


def test_public_web_page_fetch_rejects_private_addresses() -> None:
    with pytest.raises(WebSearchError, match="public IP"):
        fetch_public_web_page(
            "http://127.0.0.1/private",
            opener_factory=fake_opener_factory,
        )


def test_bound_context_and_filesystem_tools(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with ContextStore(tmp_path / "context.sqlite3") as store:
        store.add_text("memory://one", "Project code is ORBITAL.")
        tools = _tool_map(
            build_agent_tools(
                store,
                workspace,
                search_client_factory=FakeSearchClient,
                page_fetcher=FakePageFetcher(),
            )
        )
        context_payload = json.loads(
            tools["search_context"].invoke({"query": "ORBITAL"})
        )
        assert context_payload["results"][0]["source"] == "memory://one"
        sources_payload = json.loads(tools["list_context_sources"].invoke({}))
        assert sources_payload["sources"][0]["source"] == "memory://one"
        page_payload = json.loads(
            tools["fetch_web_page"].invoke(
                {"url": "https://example.test/docs", "max_chars": 2000}
            )
        )
        assert page_payload["page"]["text"] == "Version 9.8.7"

        tools["make_directory"].invoke({"path": "/workspace/notes"})
        assert (workspace / "notes").is_dir()
        file_path = workspace / "notes" / "a.txt"
        file_path.write_text("data", encoding="utf-8")
        tools["remove_path"].invoke({"path": "notes/a.txt"})
        assert not file_path.exists()

        with pytest.raises(PathSecurityError):
            tools["remove_path"].invoke({"path": "/workspace", "recursive": True})
