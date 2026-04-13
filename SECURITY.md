# Security

## Current version: 0.3.3

`hypertopos-mcp` runs locally as a stdio MCP server. No network services, no auth layer, no multi-tenancy.

## What to watch for

- **Sphere paths** — `open_sphere(path)` reads from the local filesystem. Don't point it at user-controlled paths without validation.
- **stdio transport** — communicates with the agent host over stdio. Not designed for network exposure.
- **Force reload** — `open_sphere(force_reload=true)` reloads Python modules and is intended for development only. Not thread-safe; do not call from concurrent agents.
- **Inherits from `hypertopos`** — see [hypertopos-py SECURITY.md](https://github.com/hypertopos/hypertopos-py/blob/main/SECURITY.md) for the underlying core library considerations (pickle chain cache, sphere file trust).

## Reporting

If you find a security issue: [GitHub private vulnerability reporting](https://github.com/hypertopos/hypertopos-mcp/security/advisories/new) or email [contact@hypertopos.com](mailto:contact@hypertopos.com).
