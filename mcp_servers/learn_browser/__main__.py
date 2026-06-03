"""CLI entry point: ``python -m mcp_servers.learn_browser``."""

from __future__ import annotations

import argparse
import logging
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m mcp_servers.learn_browser",
        description="Custom MCP server exposing Microsoft Learn search/fetch + Azure GitHub samples.",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="Run over streamable HTTP instead of stdio (default: stdio).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="HTTP bind host (default: 127.0.0.1). Ignored unless --http is set.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="HTTP bind port (default: 8765). Ignored unless --http is set.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable INFO-level logging to stderr.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    # Import lazily so ``--help`` doesn't pay the import cost.
    from .server import run_http, run_stdio

    if args.http:
        run_http(host=args.host, port=args.port)
    else:
        run_stdio()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
