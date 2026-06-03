"""Tests for the external web search backends."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from blog_writer.tools import bing_search


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove any inherited search-API env vars so tests are deterministic."""
    for var in ("TAVILY_API_KEY", "BING_SEARCH_API_KEY", "BING_GROUNDING_CONNECTION_NAME"):
        monkeypatch.delenv(var, raising=False)


def _make_transport(handler: Any) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


async def test_search_web_returns_empty_when_no_backend_configured() -> None:
    hits = await bing_search.search_web("agent framework on azure")
    assert hits == []


async def test_search_web_uses_tavily_when_key_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")

    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Landing zones for agentic workloads",
                        "url": "https://example.com/landing-zones",
                        "content": "Best practices for hosting agents in Azure.",
                    },
                    {
                        "title": "Untitled hit with no URL",
                        "url": "",
                        "content": "should be dropped",
                    },
                ]
            },
        )

    monkeypatch.setattr(
        httpx, "AsyncClient", _make_async_client_factory(handler)
    )

    hits = await bing_search.search_web("landing zones", max_results=3)

    assert captured["url"].startswith("https://api.tavily.com/search")
    assert captured["auth"] == "Bearer test-key"
    assert captured["body"]["query"] == "landing zones"
    assert captured["body"]["max_results"] == 3
    assert len(hits) == 1
    assert hits[0]["title"] == "Landing zones for agentic workloads"
    assert hits[0]["url"] == "https://example.com/landing-zones"
    assert hits[0]["type"] == "external"


async def test_search_web_uses_bing_v7_when_only_bing_key_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BING_SEARCH_API_KEY", "bing-key")

    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["key"] = request.headers.get("ocp-apim-subscription-key")
        return httpx.Response(
            200,
            json={
                "webPages": {
                    "value": [
                        {
                            "name": "AI gateway patterns",
                            "url": "https://example.com/ai-gateway",
                            "snippet": "Patterns for routing LLM calls.",
                        }
                    ]
                }
            },
        )

    monkeypatch.setattr(httpx, "AsyncClient", _make_async_client_factory(handler))

    hits = await bing_search.search_web("ai gateway")

    assert "api.bing.microsoft.com" in captured["url"]
    assert captured["key"] == "bing-key"
    assert len(hits) == 1
    assert hits[0]["url"] == "https://example.com/ai-gateway"
    assert hits[0]["snippet"].startswith("Patterns")


async def test_search_web_swallows_backend_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    monkeypatch.setattr(httpx, "AsyncClient", _make_async_client_factory(handler))

    hits = await bing_search.search_web("anything")
    # Failure should degrade gracefully to an empty list, letting the caller
    # decide whether to fall back to the canned stub.
    assert hits == []


async def test_tavily_takes_priority_over_bing_v7(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tk")
    monkeypatch.setenv("BING_SEARCH_API_KEY", "bk")

    called_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called_hosts.append(request.url.host)
        return httpx.Response(200, json={"results": []})

    monkeypatch.setattr(httpx, "AsyncClient", _make_async_client_factory(handler))

    await bing_search.search_web("query")

    assert called_hosts == ["api.tavily.com"]


def _make_async_client_factory(handler: Any):
    """Return a factory that produces httpx.AsyncClient(transport=MockTransport(handler))."""
    original = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    return factory
