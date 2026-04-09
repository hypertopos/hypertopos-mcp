# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Entry point for the Hypertopos MCP server."""

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


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
