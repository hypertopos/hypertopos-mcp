# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Entry point for the Hypertopos MCP server."""

import argparse

# Import tool modules to register them with the FastMCP instance
import hypertopos_mcp.tools.aggregation  # noqa: F401
import hypertopos_mcp.tools.analysis  # noqa: F401
import hypertopos_mcp.tools.detection  # noqa: F401
import hypertopos_mcp.tools.geometry  # noqa: F401
import hypertopos_mcp.tools.navigation  # noqa: F401
import hypertopos_mcp.tools.observability  # noqa: F401
import hypertopos_mcp.tools.session  # noqa: F401
import hypertopos_mcp.tools.smart  # noqa: F401
from hypertopos_mcp.server import _unregister_phase2_tools, mcp

# Start in Phase 1 mode — only always-tier tools visible until open_sphere
_unregister_phase2_tools()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hypertopos_mcp.main",
        description="Hypertopos MCP server entry point.",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default="stdio",
        help="Transport protocol (default: stdio). Use 'http' for the MCP streamable-HTTP transport.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="TCP port for the HTTP transport (default: 8080). Ignored for stdio.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    if args.transport == "http":
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
