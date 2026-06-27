# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

MikroCLAW is an MCP server that exposes MikroTik RouterOS (v7 REST API) to Claude Code as schema'd tools, plus "Pulse", a read-only live web dashboard. The codebase, docstrings, README, and skills are written in **Indonesian** — match that language when adding tools or docs.

## Commands

```bash
uv sync                          # install deps + create .venv (run after editing pyproject.toml)
uv run mikroclaw                 # run the MCP server over stdio (what Claude Code launches)
uv run mikroclaw-web             # run Pulse dashboard → http://127.0.0.1:8800 (or: python -m mikroclaw.web)

# Verify all tools register without touching a router (do this after editing server.py):
uv run python -c "import asyncio; from mikroclaw.server import mcp; print(len(asyncio.run(mcp.list_tools())), 'tools')"

# Smoke-test REST connectivity + credentials before wiring up Claude:
source .env && curl -sk -u "$MIKROTIK_USER:$MIKROTIK_PASSWORD" "https://$MIKROTIK_HOST/rest/system/resource" | jq .
```

There is **no automated test suite or linter** configured — the list_tools one-liner and the curl check are the verification path. Running the server or Pulse requires a populated `.env` (copy `.env.example`); `Config.from_env()` raises immediately if `MIKROTIK_HOST` is unset.

## Architecture

Four-file core under `src/mikroclaw/`, layered transport → tools:

- **config.py** — `Config.from_env()` reads env/`.env` into a frozen dataclass. `load_dotenv()` runs **once at import time**, and `server.py` caches `Config`/`RouterOSClient` as module singletons (`_cfg`, `_client`). Consequence: **editing `.env` has no effect until the MCP connection is restarted** (toggle via `/mcp`). `base_url` builds `{http|https}://host:port/rest`.
- **client.py** — `RouterOSClient`, a thin async `httpx` wrapper. The whole abstraction is the REST verb mapping (also documented in the class docstring): `GET`=read, `PUT`=add item, `PATCH /<path>/<id>`=modify by `.id`, `DELETE /<path>/<id>`=remove by `.id`, `POST`=command (ping, reboot, …). All failures (connection, TLS, 4xx/5xx) are wrapped in `RouterOSError`.
- **server.py** — one `FastMCP("mikroclaw")` instance with ~92 `@mcp.tool()` functions split into two sections: **READ** (always available) and **WRITE** (config-mutating). The README advertises the exact count (currently "92 tool — 70 read + 22 write") — keep that in sync when adding/removing tools.
- **web/** — Pulse, a self-contained read-only dashboard reusing the same `Config`/`RouterOSClient`. `poller.py` runs four async loops at staggered cadences (fast 1s: resource/health/interface throughput · mid 5s: clients/firewall/queues · slow 30s: WAN/services/certs · ping 5s) and holds live state in-memory + `deque` ring-buffers for 60s sparklines. **Throughput is derived from `/interface` rx/tx counter deltas** (one request for all interfaces), not per-interface `monitor-traffic`. `app.py` is Starlette + SSE: `/api/stream` pushes a JSON snapshot on every poll tick, `/` serves the dependency-free `static/index.html`. No new dependencies — Starlette/uvicorn ship transitively with `mcp`.

## Conventions when editing

- **The write-gate is the central safety invariant.** Every config-mutating tool must call `_require_write()` as its first line — it raises unless `MIKROCLAW_ALLOW_WRITE=true`. Read tools never call it. Don't add a write tool to the read section or omit the gate.
- **A tool's docstring is its description shown to Claude** — write it clearly, in Indonesian, and document every parameter. This is the primary interface contract, not an afterthought.
- **Version portability:** when a RouterOS menu path differs across versions/packages (e.g. wifiwave2 `/interface/wifi` vs legacy `/interface/wireless`, or `/caps-man` vs wifiwave2 CAPsMAN), use `_first_ok(path_a, path_b, …)` in server.py (returns the first path that succeeds) or `_safe_first(...)` in the poller. Don't hard-code a single path for these.
- Adding a tool = an `async def` decorated with `@mcp.tool()` that delegates to `_ros().get/post/put/patch/delete(...)`. For ad-hoc paths not worth a dedicated tool, `rest_get` (read) and `rest_write` (gated) already exist.
- After editing server.py, restart the MCP connection in Claude Code (`/mcp`) so new tools are picked up, then run the list_tools one-liner to confirm registration.

## RouterOS v6 note

REST exists **only in RouterOS v7**. To support v6, the tool layer in server.py stays unchanged — only swap client.py's transport to the binary API (port 8728/8729) via a lib like `librouteros`, mapping `get/put/patch/delete` onto binary commands.

## Skills

`.claude/skills/` holds six read-only orchestration playbooks (health-check, firewall-audit, security-audit, network-overview, troubleshoot, backup-snapshot) that compose the atomic MCP tools into workflows. Each is a `SKILL.md` with frontmatter (`name`, `description` with Indonesian trigger phrases) followed by a tool list, procedure, and report format. Mirror that structure for new skills.
