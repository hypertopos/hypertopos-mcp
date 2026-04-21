# hypertopos-mcp

> MCP tools for exploring geometric data spaces with AI agents.

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-green.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-compatible-green.svg)](https://modelcontextprotocol.io)
[![Version](https://img.shields.io/badge/version-0.5.1-%235500FF.svg)](pyproject.toml)

hypertopos-mcp gives AI agents a way to explore data built with [hypertopos](https://github.com/hypertopos/hypertopos-py). Instead of writing queries, agents navigate a geometric space — finding anomalies, tracing relationships, comparing populations, and tracking change over time.

## Quick start

```bash
pip install hypertopos-mcp
```

Configure your MCP client:

```json
{
  "mcpServers": {
    "hypertopos": {
      "command": "hypertopos-mcp",
      "env": {
        "HYPERTOPOS_SPHERE_PATH": "path/to/your/sphere"
      }
    }
  }
}
```

The agent starts with `open_sphere`, then either:
- `detect_pattern("find anomalous accounts")` — smart detection in a single call
- `sphere_overview()` — unlock the full manual toolset for step-by-step exploration

## What an agent can do

- Find anomalies — entities far from the population norm
- Discover clusters and structural archetypes
- Navigate between related entities
- Trace relationship chains and transaction flows
- Compare populations across groups or time windows
- Track drift and detect regime changes
- Score contagion risk through network proximity

## How it works

1. Build a geometric space from relational data using [hypertopos-py](https://github.com/hypertopos/hypertopos-py)
2. Connect the space via hypertopos-mcp
3. The agent explores — each finding leads to the next

Tools are registered dynamically. The agent starts with a small set and unlocks more as it explores — keeping context lean.

## Works best with skills

hypertopos-mcp provides tools. [hypertopos-skills](https://github.com/hypertopos/hypertopos-skills) provides judgment — structured investigation workflows that guide agents through real tasks like fraud detection, anomaly triage, and drift monitoring.

## Documentation

| | |
|---|---|
| [Tool Reference](docs/tools.md) | All tool parameters, return shapes, filters |
| [MCP Specification](docs/mcp-spec.md) | Server spec, lifecycle, transport, error codes |

## Status

Research-stage project. Tooling and API may evolve.

## License

[Apache License 2.0](LICENSE)
