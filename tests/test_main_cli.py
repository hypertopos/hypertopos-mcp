# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Tests for the MCP server entry-point CLI."""

from unittest.mock import patch

import pytest
from hypertopos_mcp.main import _build_parser, main


def test_parser_defaults_to_stdio():
    args = _build_parser().parse_args([])
    assert args.transport == "stdio"
    assert args.port == 8080


def test_parser_accepts_http_transport_and_port():
    args = _build_parser().parse_args(["--transport", "http", "--port", "9001"])
    assert args.transport == "http"
    assert args.port == 9001


def test_parser_rejects_unknown_transport():
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["--transport", "sse"])


def test_main_dispatches_stdio_by_default():
    with patch("hypertopos_mcp.main.mcp") as mock_mcp:
        main([])
    mock_mcp.run.assert_called_once_with(transport="stdio")


def test_main_dispatches_streamable_http_when_requested():
    with patch("hypertopos_mcp.main.mcp") as mock_mcp:
        main(["--transport", "http", "--port", "8080"])
    assert mock_mcp.settings.port == 8080
    mock_mcp.run.assert_called_once_with(transport="streamable-http")


def test_main_propagates_custom_port():
    with patch("hypertopos_mcp.main.mcp") as mock_mcp:
        main(["--transport", "http", "--port", "9090"])
    assert mock_mcp.settings.port == 9090
    mock_mcp.run.assert_called_once_with(transport="streamable-http")
