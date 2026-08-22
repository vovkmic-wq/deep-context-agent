"""Tests for web, retrieval, and safe filesystem tools."""

import json
import socket
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from context_agent.context_store import ContextStore
from context_agent.errors import WebSearchError
from context_agent.tools import (
    build_agent_tools,
    fetch_public_web_page,
    fetch_pypi_package_info,
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


class FlakySearchClient:
    calls = 0

    def text(self, query: str, **kwargs: Any) -> list[dict[str, str]]:
        del query, kwargs
        type(self).calls += 1
        if type(self).calls < 3:
            raise TimeoutError("temporary timeout")
        return [{"title": "Recovered", "href": "https://example.test"}]


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


class FailingPageFetcher:
    def __call__(self, url: str, *, max_chars: int) -> dict[str, Any]:
        del url, max_chars
        raise TimeoutError("page timeout")


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


class FakePypiResponse(FakeResponse):
    def __init__(
        self,
        body: bytes,
        final_url: str = "https://pypi.org/pypi/langchain/json",
    ) -> None:
        super().__init__(body)
        self.headers.replace_header("Content-Type", "application/json")
        self.final_url = final_url

    def geturl(self) -> str:
        return self.final_url


class FakePypiOpener:
    def __init__(
        self,
        body: bytes,
        final_url: str = "https://pypi.org/pypi/langchain/json",
    ) -> None:
        self.body = body
        self.final_url = final_url

    def open(self, request: Any, *, timeout: float) -> FakePypiResponse:
        assert request.full_url == "https://pypi.org/pypi/langchain/json"
        assert timeout == 10.0
        return FakePypiResponse(self.body, self.final_url)


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


def fake_pypi_resolver(
    host: str,
    port: int,
    family: int,
    socket_type: int,
) -> list[tuple[Any, ...]]:
    assert host == "pypi.org"
    assert port == 443
    assert family == 0
    assert socket_type == socket.SOCK_STREAM
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("151.101.0.223", port))]


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
        search_web(
            "current docs",
            client_factory=FailingSearchClient,
            attempts=1,
        )
    with pytest.raises(WebSearchError, match="two characters"):
        search_web("x")


def test_web_search_retries_transient_failure_with_backoff() -> None:
    FlakySearchClient.calls = 0
    delays: list[float] = []
    results = search_web(
        "current docs",
        client_factory=FlakySearchClient,
        attempts=3,
        retry_delay=0.25,
        sleeper=delays.append,
    )
    assert results[0]["title"] == "Recovered"
    assert FlakySearchClient.calls == 3
    assert delays == [0.25, 0.5]


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


def test_pypi_package_info_uses_official_bounded_json() -> None:
    body = json.dumps({"info": {"name": "langchain", "version": "9.8.7"}}).encode()

    package = fetch_pypi_package_info(
        "langchain",
        resolver=fake_pypi_resolver,
        opener_factory=lambda resolver: FakePypiOpener(body),
    )

    assert package == {
        "package": "langchain",
        "version": "9.8.7",
        "project_url": "https://pypi.org/project/langchain/",
        "api_url": "https://pypi.org/pypi/langchain/json",
    }


def test_pypi_package_info_rejects_invalid_and_oversized_payloads() -> None:
    with pytest.raises(WebSearchError, match="package name"):
        fetch_pypi_package_info("../private")

    oversized = b"x" * 2_000_001
    with pytest.raises(WebSearchError, match="size limit"):
        fetch_pypi_package_info(
            "langchain",
            resolver=fake_pypi_resolver,
            opener_factory=lambda resolver: FakePypiOpener(oversized),
        )

    body = json.dumps({"info": {"name": "langchain", "version": "9.8.7"}}).encode()
    with pytest.raises(WebSearchError, match="redirected outside"):
        fetch_pypi_package_info(
            "langchain",
            resolver=fake_pypi_resolver,
            opener_factory=lambda resolver: FakePypiOpener(
                body,
                "https://93.184.216.34/metadata",
            ),
        )


def test_bound_web_tools_return_structured_current_errors(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with ContextStore(tmp_path / "context.sqlite3") as store:
        tools = _tool_map(
            build_agent_tools(
                store,
                workspace,
                search_client_factory=FailingSearchClient,
                page_fetcher=FailingPageFetcher(),
                web_retry_attempts=1,
            )
        )
        search_payload = json.loads(
            tools["web_search"].invoke({"query": "current docs"})
        )
        page_payload = json.loads(
            tools["fetch_web_page"].invoke({"url": "https://example.test/docs"})
        )

    assert search_payload["status"] == "error"
    assert search_payload["results"] == []
    assert search_payload["checked_at"].endswith("+00:00")
    assert page_payload["status"] == "error"
    assert page_payload["message"] == "Web page fetch failed: TimeoutError"
    assert page_payload["checked_at"].endswith("+00:00")


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
                pypi_fetcher=lambda package: {
                    "package": package,
                    "version": "9.8.7",
                    "project_url": "https://pypi.org/project/langchain/",
                    "api_url": "https://pypi.org/pypi/langchain/json",
                },
                runtime_metadata={"model": "test-model"},
            )
        )
        runtime_payload = json.loads(tools["runtime_info"].invoke({}))
        assert runtime_payload == {"model": "test-model"}
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
        assert page_payload["checked_at"].endswith("+00:00")
        pypi_payload = json.loads(
            tools["get_pypi_package_info"].invoke({"package": "langchain"})
        )
        assert pypi_payload["status"] == "success"
        assert pypi_payload["version"] == "9.8.7"
        assert pypi_payload["checked_at"].endswith("+00:00")

        directory_payload = json.loads(
            tools["make_directory"].invoke({"path": "/workspace/notes"})
        )
        assert directory_payload["status"] == "success"
        assert (workspace / "notes").is_dir()
        file_path = workspace / "notes" / "a.txt"
        file_path.write_text("data", encoding="utf-8")
        remove_payload = json.loads(
            tools["remove_path"].invoke({"path": "notes/a.txt"})
        )
        assert remove_payload["status"] == "success"
        assert not file_path.exists()

        missing_payload = json.loads(
            tools["remove_path"].invoke({"path": "notes/missing.txt"})
        )
        assert missing_payload["status"] == "not_found"

        sentinel = workspace / "sentinel.txt"
        sentinel.write_text("keep", encoding="utf-8")
        denied_payload = json.loads(
            tools["remove_path"].invoke({"path": "/workspace", "recursive": True})
        )
        assert denied_payload["status"] == "denied"
        assert sentinel.read_text(encoding="utf-8") == "keep"
