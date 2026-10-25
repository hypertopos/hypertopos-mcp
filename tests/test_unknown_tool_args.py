# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""A misspelled or unknown tool argument is an error with a hint, never silently dropped."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

import hypertopos_mcp.tools.aggregation  # noqa: F401
import hypertopos_mcp.tools.analysis  # noqa: F401
import hypertopos_mcp.tools.detection  # noqa: F401
import hypertopos_mcp.tools.geometry  # noqa: F401
import hypertopos_mcp.tools.navigation  # noqa: F401
import hypertopos_mcp.tools.observability  # noqa: F401
import hypertopos_mcp.tools.session  # noqa: F401
import hypertopos_mcp.tools.smart  # noqa: F401


def _arg_model(tool_name: str):
    from hypertopos_mcp.server import mcp

    tool = mcp._tool_manager.get_tool(tool_name)
    assert tool is not None
    return tool.fn_metadata.arg_model


def test_unknown_argument_is_rejected_with_a_suggestion() -> None:
    model = _arg_model("find_anomalies")
    with pytest.raises(ValidationError) as excinfo:
        model.model_validate({"pattern_id": "p", "topn": 5})
    message = str(excinfo.value)
    assert "unknown parameter 'topn'" in message
    assert "did you mean 'top_n'" in message


def test_unknown_argument_without_close_match_lists_valid_names() -> None:
    model = _arg_model("goto")
    with pytest.raises(ValidationError) as excinfo:
        model.model_validate({"primary_key": "k", "line_id": "l", "zzz": 1})
    message = str(excinfo.value)
    assert "unknown parameter 'zzz'" in message
    assert "line_id" in message and "primary_key" in message


def test_known_arguments_still_validate() -> None:
    model = _arg_model("find_anomalies")
    parsed = model.model_validate({"pattern_id": "p", "top_n": 5})
    assert parsed.top_n == 5


def test_every_registered_tool_rejects_unknown_arguments() -> None:
    from hypertopos_mcp.server import mcp

    tools = mcp._tool_manager.list_tools()
    assert len(tools) > 50
    accepting = []
    for tool in tools:
        required = {
            name: None
            for name, field in tool.fn_metadata.arg_model.model_fields.items()
            if field.is_required()
        }
        try:
            tool.fn_metadata.arg_model.model_validate({**required, "not_a_parameter": 1})
        except ValidationError as exc:
            if "unknown parameter 'not_a_parameter'" in str(exc):
                continue
        accepting.append(tool.name)
    assert accepting == []
