# MCP Gateway

Two AI agents (Issue Triage, PR Review) reach GitHub only through one MCP gateway, which allows, denies or redacts each call based on agent identity, declared intent, the human user behind it, and the sensitivity of the data being requested. The LLM proposes tool calls; the gateway decides.

Full design rationale lives in [`docs/MCP Gateway Technical Design.md`](docs/MCP%20Gateway%20Technical%20Design.md). This README covers what's actually built and how to run it.

## What's built (v1 scope)

- **Backend: own-gateway / REST mode only.** `RestBackend` calls the GitHub REST API directly. A proxy backend in front of GitHub's official MCP server (`McpBackend`) is **not implemented** -- `gateway/backends/base.py`'s `GitHubBackend` protocol is the only trace of that seam. To add it later: implement every protocol method against an MCP client pointed at `github-mcp-server`, register it as `gateway/backends/mcp_proxy.py:McpBackend`, and flip `config/gateway.yaml`'s `backend:` field to `mcp`. No other code changes -- the policy engine, canonical catalog, and both agents are identical in both modes by design.
- **All must-have and should-have demo cases**, proven as offline scenario tests against `FakeBackend` through the real MCP transport (see `tests/scenarios/`), plus one live smoke test against `RestBackend` and the real demo repos (`tests/contract/`, marked `integration`).
- **Nice-to-haves cut for v1**: output redaction beyond the basic regex pass, an anomaly flag, and a tamper-evident hash-chained audit log are not built.

## Fixes made to the original design during implementation

A few things in the design doc didn't survive contact with real code; this is what changed and why:

1. **`get_pr_diff`'s decision order.** The original design fetches the PR's changed-file list from GitHub *before* checking role/repo/intent -- contradicting its own "decide before fetching" principle. Fixed by splitting `policy.decide()` into `decide_phase1` (role/repo/intent/arg checks, no I/O) and `decide_phase2` (tier checks); the metadata lookup only runs if phase 1 passes, and a failure during that lookup itself fails closed rather than silently falling through to ALLOW.
2. **`policy.yaml` needed a `repos:` block.** The design referenced a "repo registry" for visibility (public/private) but never showed its schema. Added as a top-level `repos:` map, validated at load time against every repo referenced elsewhere in the file.
3. **`post_review_comment` has no `event` argument.** The design's canonical tool catalog lists this tool's args as `repo, number, body` only (no approve/request-changes capability exists), but the policy YAML excerpt had a `require: {event: COMMENT}` constraint as if `event` were a real argument. Removed -- "comment only" is structural (the backend method literally cannot approve anything), not an argument check.
4. **Post-ALLOW backend failures stay ALLOW.** If policy allows a call but the GitHub fetch itself then fails (404/403/500/rate-limit), the audit row keeps `outcome: ALLOW` plus a `fetch_error` field, and the agent gets a generic tool error (no rule ID, since no rule denied it). This is distinct from a failure *before* a decision is finalized (e.g. the `get_pr_diff` metadata lookup raising), which fails closed as `DENY fail_closed`.
5. **Session expiry is a hard stop.** 15-minute JWT, no silent renewal. An expired session denies `invalid_session` until the user re-picks their identity in Streamlit.

## Running it

### Local (uv)

```bash
cp .env.example .env   # fill in GITHUB_TOKEN, GITHUB_OWNER, signing/admin keys, agent tokens, ANTHROPIC_API_KEY
uv sync

# terminal 1
uv run python -m gateway.server

# terminal 2
uv run streamlit run app/Home.py
```

Without a GitHub token handy, `scripts/run_demo_gateway.py` runs the same app wired to `FakeBackend` instead of `RestBackend` -- useful for rehearsing the demo or developing the UI without touching real GitHub.

### Docker

```bash
cp .env.example .env
docker compose up --build
```

Gateway on `:8080`, Streamlit on `:8501`.

## Testing

```bash
uv run pytest tests/unit tests/scenarios        # offline, no credentials needed
uv run pytest tests/contract                     # FakeBackend always; RestBackend only with GITHUB_TOKEN/GITHUB_OWNER set
uv run pytest tests/contract -m integration      # explicitly run the live-GitHub checks
```

Every demo case (must-have and should-have) is a scenario test in `tests/scenarios/`, run through the real MCP streamable-HTTP transport against `FakeBackend` -- not an in-memory shortcut, so the auth headers, filtered discovery, and audit rows are exercised exactly as production carries them.

## Repo layout

```
gateway/           # the gateway itself: northbound MCP server, core decision engine, backends
  server.py         # MCP server + /admin/* routes; orchestrates auth -> decide -> fetch -> redact -> audit
  core/             # models, auth (R01-R03), context (classification), policy (R04-R12), redact, audit, config
  backends/         # GitHubBackend protocol, RestBackend, FakeBackend
config/             # policy.yaml, classification.yaml, gateway.yaml
agents/             # thin tool-calling loop + Claude client + prompts
app/                # Streamlit: Home, Triage, Review, Admin pages
tests/
  unit/              # policy/context/auth/redact, no I/O
  contract/          # same assertions against every backend
  scenarios/         # one file per demo case, full gateway + FakeBackend, real MCP transport
scripts/run_demo_gateway.py   # dev-only: gateway wired to FakeBackend for local UI testing
```
