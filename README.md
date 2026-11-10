# MCP Gateway

![CI](https://github.com/satyakamala03/mcp-gateway-rest/actions/workflows/ci.yml/badge.svg)

Two AI agents (Issue Triage, PR Review) reach GitHub only through one MCP gateway. The LLM proposes tool calls; the gateway decides.

Start at [`docs/technical-documentation.md`](docs/technical-documentation.md) for the approach, decisions, and architecture diagram. This README covers how to run and test it.

## What's built (v1 scope)

- **Backend: own-gateway / REST mode only.** `RestBackend` calls the GitHub REST API directly. A proxy backend in front of GitHub's official MCP server (`McpBackend`) is **not implemented** -- `gateway/backends/base.py`'s `GitHubBackend` protocol is the only trace of that seam. To add it later: implement every protocol method against an MCP client pointed at `github-mcp-server`, register it as `gateway/backends/mcp_proxy.py:McpBackend`, and flip `config/gateway.yaml`'s `backend:` field to `mcp`. No other code changes -- the policy engine, canonical catalog, and both agents are identical in both modes by design.
- **All must-have and should-have demo cases**, proven as offline scenario tests against `FakeBackend` through the real MCP transport (see `tests/scenarios/`), plus one live smoke test against `RestBackend` and the real demo repos (`tests/contract/`, marked `integration`).
- **Hot policy reload**: `POST /admin/policy/reload` (admin-token gated) re-reads `policy.yaml`/`classification.yaml` and swaps them in without restarting the process. Fails closed -- an invalid file is rejected with the parse error and the last-known-good policy keeps serving. Also a "Reload Policy" button on the Admin page.
- **Nice-to-haves cut for v1**: output redaction beyond the basic regex pass, an anomaly flag, and a tamper-evident hash-chained audit log are not built.

## Running it

### Local (uv)

```bash
cp .env.example .env   # fill in GITHUB_TOKEN, GITHUB_OWNER, signing/admin keys, agent tokens, OPENAI_API_KEY
uv sync

# terminal 1
uv run python -m gateway.server

# terminal 2
uv run streamlit run app/Home.py
```

Without a GitHub token handy, `scripts/run_demo_gateway.py` runs the same app wired to `FakeBackend` instead of `RestBackend` -- useful for rehearsing the demo or developing the UI without touching real GitHub.

## Testing

```bash
uv run ruff check .                              # lint
uv run mypy gateway agents                       # type-check (core + agents; app/ excluded, see below)

uv run pytest tests/unit tests/scenarios        # offline, no credentials needed

set -a && source .env && set +a
uv run pytest tests/contract                     # FakeBackend always; RestBackend only with GITHUB_TOKEN/GITHUB_OWNER set
uv run pytest tests/contract -m integration      # explicitly run the live-GitHub checks
```

`app/` is excluded from `mypy` for now -- Streamlit's multipage convention (no `__init__.py`, `app/common.py` imported both as `app.common` and as a bare module from the page scripts) trips mypy's module-resolution, not a real type issue.

CI (`.github/workflows/ci.yml`) runs lint, type-check, and `tests/unit`/`tests/scenarios` on every push and PR -- no secrets needed, since those run fully offline against `FakeBackend`. `tests/contract` (including the live-GitHub integration tests) stays a local-only, manual check.

Every demo case (must-have and should-have) is a scenario test in `tests/scenarios/`, run through the real MCP streamable-HTTP transport against `FakeBackend` -- not an in-memory shortcut, so the auth headers, filtered discovery, and audit rows are exercised exactly as production carries them.

## Repo layout

```
gateway/           # the gateway itself: northbound MCP server, core decision engine, backends
  server.py         # MCP server + /admin/* routes; orchestrates auth -> decide -> fetch -> redact -> audit
  core/             # models, auth (R01-R03), context (classification), policy (R04-R12), redact, audit, config
  backends/         # GitHubBackend protocol, RestBackend, FakeBackend
config/             # policy.yaml, classification.yaml, gateway.yaml
agents/             # thin tool-calling loop + OpenAI client + prompts
app/                # Streamlit: Home, Triage, Review, Admin pages
tests/
  unit/              # policy/context/auth/redact, no I/O
  contract/          # same assertions against every backend
  scenarios/         # one file per demo case, full gateway + FakeBackend, real MCP transport
scripts/run_demo_gateway.py   # dev-only: gateway wired to FakeBackend for local UI testing
```

Full design rationale (pre-implementation): `docs/MCP Gateway Technical Design.md`

## License

MIT — see [LICENSE](LICENSE).
