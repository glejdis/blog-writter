"""Smoke tests for the FastMCP server module — tool registration + CLI parsing."""

from __future__ import annotations

import pytest

from mcp_servers.learn_browser import __main__ as cli
from mcp_servers.learn_browser import server


async def test_server_registers_expected_tools() -> None:
    tools = await server.mcp.list_tools()
    names = {t.name for t in tools}
    expected = {
        "search_all_learn",
        "search_curated_learn",
        "fetch_learn_page",
        "search_learn_code_samples",
        "search_github_azure_samples",
    }
    assert expected.issubset(names), f"missing tools: {expected - names}"


async def test_server_tool_metadata_is_populated() -> None:
    tools = await server.mcp.list_tools()
    by_name = {t.name: t for t in tools}
    for name in ("search_all_learn", "fetch_learn_page"):
        t = by_name[name]
        assert t.description, f"tool {name} is missing a description"
        assert t.inputSchema, f"tool {name} is missing an input schema"


def test_cli_help_does_not_crash(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "stdio" in out
    assert "--http" in out


def test_cli_parses_http_flags() -> None:
    """Verify --http / --host / --port are recognised; don't actually start the server."""
    from unittest.mock import patch

    with patch("mcp_servers.learn_browser.server.run_http") as mock_http:
        rc = cli.main(["--http", "--host", "0.0.0.0", "--port", "9999"])
    assert rc == 0
    mock_http.assert_called_once_with(host="0.0.0.0", port=9999)
