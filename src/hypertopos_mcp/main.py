# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Entry point for the Hypertopos MCP server."""

import argparse
import os

# Import tool modules to register them with the MCPServer instance
import hypertopos_mcp.tools.aggregation  # noqa: F401
import hypertopos_mcp.tools.analysis  # noqa: F401
import hypertopos_mcp.tools.detection  # noqa: F401
import hypertopos_mcp.tools.geometry  # noqa: F401
import hypertopos_mcp.tools.navigation  # noqa: F401
import hypertopos_mcp.tools.observability  # noqa: F401
import hypertopos_mcp.tools.session  # noqa: F401
import hypertopos_mcp.tools.smart  # noqa: F401
from hypertopos_mcp.server import (
    mcp,
    profile_names,
    select_profile,
    set_header_session_idle_timeout,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hypertopos_mcp.main",
        description="Hypertopos MCP server entry point.",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default="stdio",
        help=(
            "Transport protocol (default: stdio). Use 'http' for the MCP streamable-HTTP transport."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="TCP port for the HTTP transport (default: 8080). Ignored for stdio.",
    )
    parser.add_argument(
        "--session-idle-timeout",
        type=float,
        default=1800.0,
        help=(
            "Seconds an HTTP client session, including one keyed by the "
            "X-Hypertopos-Session header, may stay idle before the server ends it "
            "and closes its sphere (default: 1800). Ignored for stdio."
        ),
    )
    parser.add_argument(
        "--max-sessions",
        type=int,
        default=10000,
        help="Most concurrent HTTP client sessions (default: 10000). Ignored for stdio.",
    )
    parser.add_argument(
        "--profile",
        default=os.environ.get("HYPERTOPOS_MCP_PROFILE"),
        help=(
            "Tool profile(s) the server lists, comma-separated and combined: "
            f"{', '.join(profile_names())} (default: investigator; env HYPERTOPOS_MCP_PROFILE)."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        select_profile(args.profile)
    except ValueError as err:
        parser.error(str(err))
    if args.transport == "http":
        set_header_session_idle_timeout(args.session_idle_timeout)
        mcp.run(
            transport="streamable-http",
            port=args.port,
            session_idle_timeout=args.session_idle_timeout,
            max_sessions=args.max_sessions,
        )
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
