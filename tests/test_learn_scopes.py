"""Tests for the Learn scope filter."""

from __future__ import annotations

from blog_writer.config import AppConfig
from blog_writer.tools.learn_mcp import LearnHit, LearnScopeFilter, load_learn_scopes


def test_load_learn_scopes_from_repo() -> None:
    config = AppConfig(stub=True, provider="stub")
    scope = load_learn_scopes(config)
    assert scope.allow_list, "allow_list must not be empty"
    assert "/azure/cloud-adoption-framework/" in scope.allow_list
    assert "/azure/well-architected/" in scope.allow_list


def test_filter_keeps_in_scope_and_drops_out_of_scope() -> None:
    scope = LearnScopeFilter(
        allow_list=(
            "/azure/cloud-adoption-framework/",
            "/azure/well-architected/",
        )
    )
    hits = [
        LearnHit(
            title="CAF page",
            url="https://learn.microsoft.com/en-us/azure/cloud-adoption-framework/scenarios/ai/",
            excerpt="...",
        ),
        LearnHit(
            title="WAF page",
            url="https://learn.microsoft.com/en-us/azure/well-architected/ai/",
            excerpt="...",
        ),
        LearnHit(
            title="Random SDK reference",
            url="https://learn.microsoft.com/en-us/python/api/azure-identity/?view=azure-python",
            excerpt="...",
        ),
        LearnHit(
            title="External blog",
            url="https://example.com/some-post",
            excerpt="...",
        ),
    ]
    kept = scope.filter_hits(hits)
    assert {h.title for h in kept} == {"CAF page", "WAF page"}
