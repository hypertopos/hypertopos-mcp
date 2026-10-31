# Security

## Current version: 0.10.3

`hypertopos-mcp` runs locally as an MCP server over stdio or local HTTP. No auth layer, no multi-tenancy.

## What to watch for

- **Sphere paths** — `open_sphere(path)` reads from the local filesystem. Don't point it at user-controlled paths without validation.
- **Transports** — stdio talks to one agent host. `--transport http` binds to the loopback interface with no authentication, and any session can open any sphere path the server process can read: it is for local use on one machine and must not be exposed to a network.
- **Force reload** — `open_sphere(force_reload=true)` reloads Python modules and is intended for development only. Not thread-safe; do not call from concurrent agents.
- **Inherits from `hypertopos`** — see [hypertopos-py SECURITY.md](https://github.com/hypertopos/hypertopos-py/blob/main/SECURITY.md) for the underlying core library considerations (sphere file trust).

## Supported Versions

| Version | Supported |
|---|---|
| Latest 0.10.x | Yes |
| < 0.10 | No |

## Reporting

If you find a security issue: [GitHub private vulnerability reporting](https://github.com/hypertopos/hypertopos-mcp/security/advisories/new) or email [contact@hypertopos.com](mailto:contact@hypertopos.com).
