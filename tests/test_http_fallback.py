"""Controlled HTTP transport through runtime fallback, not a real LLM test."""

import json
import threading
import time
from contextlib import contextmanager, suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from conftest import SequenceChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from context_agent.config import AppConfig, ProviderConfig
from context_agent.errors import AgentError
from context_agent.runtime import AgentRuntime


class HTTPFixtureModel(SequenceChatModel):
    url: str
    request_timeout: float

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        try:
            response = httpx.post(
                self.url + "/chat/completions",
                json={"messages": []},
                timeout=self.request_timeout,
                trust_env=False,
            )
        except httpx.TimeoutException as exc:
            raise TimeoutError("controlled_http_timeout") from exc
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=content))]
        )


@contextmanager
def endpoint(delay):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(time.monotonic())
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            threading.Event().wait(delay)
            body = json.dumps(
                {
                    "id": "fixture",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "http-fixture",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "HTTP_FIXTURE_OK",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 4,
                        "total_tokens": 14,
                    },
                }
            ).encode()
            with suppress(OSError):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.mark.parametrize(
    "manual, local_only", [(False, False), (True, False), (True, True)]
)
def test_http_timeout_respects_manual_policy_and_attempt_budget(
    tmp_path, manual, local_only
):
    config = AppConfig(
        project_root=tmp_path,
        workspace=tmp_path / "workspace",
        data_dir=tmp_path / "data",
        context_root=tmp_path / "workspace",
        model_call_retries=0,
        task_model_attempts=3,
        model_retry_initial_delay=0,
        model_retry_max_delay=0,
        model_local_only=local_only,
    )
    with endpoint(0.5) as (slow, attempts), endpoint(0) as (fast, fallback):
        primary = ProviderConfig(
            name="openai",
            model="http-fixture",
            api_key="local-fixture-only",
            base_url=slow,
            timeout=0.1,
        )
        secondary = ProviderConfig(
            name="zhipu",
            model="http-fixture",
            api_key="local-fixture-only",
            base_url=fast,
            timeout=2,
        )
        started = time.monotonic()
        with AgentRuntime(
            config,
            primary,
            fallback_provider_configs=(secondary,),
            model=HTTPFixtureModel(url=slow, request_timeout=0.1, responses=[]),
            fallback_models=(
                HTTPFixtureModel(url=fast, request_timeout=2, responses=[]),
            ),
        ) as runtime:
            runtime.set_resource_request("Reply OK", manual=manual)
            if local_only:
                with pytest.raises(AgentError):
                    runtime.ask("Reply OK", auto_context=False)
                assert not fallback
                assert not attempts
            else:
                assert "HTTP_FIXTURE_OK" in runtime.ask("Reply OK", auto_context=False)
                assert len(fallback) == 1
        if not local_only:
            # Manual preserves the explicitly configured fallback (spec R4),
            # but must not add an unconfigured/adaptive candidate.
            assert 1 <= len(attempts) <= 3
        assert time.monotonic() - started < 8
