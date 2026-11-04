# MCP Gateway — Technical Documentation

## Overview

Two AI agents (Issue Triage, PR Review) reach GitHub only through one MCP gateway. The gateway allows, denies, or redacts every tool call based on agent identity, declared intent, the human user behind the request, and the sensitivity of the data being asked for. The LLM proposes tool calls; the gateway decides — enforcement lives outside the model entirely.

Run instructions, environment setup, and repo layout are in the [README](../README.md). This document covers the approach, the key design decisions, what's built versus deferred, how it's tested, and what's left.

## Approach

![Architecture](architecture.svg)

Governance has to sit outside the agents, not inside their prompts. A careless or compromised agent shouldn't be the only thing standing between a GitHub repo and someone who isn't supposed to read it. The gateway sits in between: agents only ever see its own tool catalog, never GitHub directly, and every call runs through a fixed decision pipeline before anything is fetched.

The two agents share infrastructure but not data access. Issue Triage works the issue queue: reads issues, suggests duplicates, applies labels. PR Review reads code and comments on pull requests. They're deliberately different in what they can touch: issue content is something anyone outside the org can write, while PR review needs real source code. That asymmetry is what makes "the same call behaves differently depending on which agent made it" something the policy engine has to actually prove, not just claim.

The GitHub client sits behind one interface (`GitHubBackend`). This version uses own-gateway mode — the gateway calls the GitHub REST API directly. Proxying GitHub’s official MCP server is sketched as a future seam, not built yet; see [Built vs. Deferred](#built-vs-deferred) below.

## Key Design Decisions

| Decision        | Choice                                                                          | Why                                                                                                                                                      | Rejected                                                                                                                          |
| --------------- | ------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Upstream        | Swappable backend behind one interface                                          | Own tools for now; the product this mirrors also has to govern servers it didn't write, so the seam has to exist either way                              | Committing to a single upstream                                                                                                   |
| Tool catalog    | The gateway's own canonical catalog, identical in every mode                    | Agents, policy, and tests never change between modes; dangerous operations (merge, approve, close) simply don't exist in the catalog                     | Passing upstream tools straight through                                                                                           |
| Transport       | MCP over streamable HTTP                                                        | HTTP has a real network boundary with headers for tokens; agent and gateway are separate processes                                                       | stdio — runs in-process, has no auth boundary                                                                                     |
| User + intent   | Signed, short-lived session token minted by the UI, never set by agent code     | Anchors who's asking and what they're allowed to do outside the LLM's reach — a compromised agent can't forge either one                                 | Headers set by agent code, or intent inferred from the prompt                                                                     |
| Intent scope    | One intent per session                                                          | Matches one task, limits blast radius, easy to audit                                                                                                     | Intent per request — an agent could change it mid-task                                                                            |
| Policy format   | YAML, default deny, ordered numbered rules                                      | Deterministic, readable, reviewable like code                                                                                                            | OPA/Cedar — a real option for production, not needed at this scope                                                                |
| Failure mode    | Fail closed everywhere: bad config, expired session, a mid-decision exception   | A security control that fails open isn't a security control                                                                                              | Fail open for availability                                                                                                        |
| Audit storage   | SQLite, append-only, one row per decision                                       | Queryable with plain SQL for the admin page, zero extra infrastructure — one file, no separate database service to stand up for two users and two repos  | Plain log files (not queryable); Postgres or another client-server DB (real option once usage grows past one process — see [Scale](#scale)) |
| Least privilege | Filtered tool discovery *and* call-time enforcement                             | The agent doesn't even see tools it can't use, and the rule still fires if it tries anyway                                                               | Discovery filtering alone, with no call-time check                                                                                |
| Policy reload   | Manual, admin-triggered (`POST /admin/policy/reload`), not a background watcher | Predictable and easy to demo; a bad reload attempt fails closed — the error is returned and the last-known-good policy keeps serving, nothing in between | An automatic file-watcher — more moving parts, harder to reason about a reload racing an in-flight request                        |

## Built vs. Deferred

This version includes the REST backend, the full decision engine, and every must-have and should-have governance case, all implemented and covered by tests, not just designed. The deferred items below were cut on purpose, not omitted by accident.

- **Proxy mode** (`McpBackend`): the interface exists; the implementation does not. The REST path is enough to prove the policy engine, and the brief describes a gateway that owns its own tools.
- **Anomaly flag**: would mark unusual denial rates per agent. Stretch feature; deferred so the core decision engine could stay solid first.
- **Tamper-evident (hash-chained) audit log**: also deferred to keep the core decision engine solid first. The audit log is complete; it is just not hash-chained.
- **Redaction beyond the basic regex pass**: token-shaped strings and emails are redacted on every response. Deeper content scanning was out of scope for the two demo repos.

## Fixes Made During Implementation

A few things in the original design didn't survive contact with real code:

- `get_pr_diff`**'s decision order** — the original design fetches the PR's changed-file list from GitHub before checking role/repo/intent, contradicting its own "decide before fetching" principle. Fixed by splitting the decision into `decide_phase1` (role/repo/intent/arg checks, no I/O) and `decide_phase2` (tier checks); the metadata lookup only runs if phase 1 passes, and a failure during that lookup fails closed rather than falling through to ALLOW.
- `policy.yaml` **needed a** `repos:` **block** — the design referenced a repo registry for visibility (public/private) but never showed its schema. Added as a top-level `repos:` map, validated at load time against every repo referenced elsewhere in the file.
- `post_review_comment` **has no** `event` **argument** — the canonical tool catalog lists this tool's args as `repo, number, body` only, but the policy YAML excerpt had a constraint as if `event` were a real argument. Removed — "comment only" is structural (the backend method can't approve anything), not an argument check.
- **Post-ALLOW backend failures stay ALLOW** — if policy allows a call but the GitHub fetch then fails, the audit row keeps `outcome: ALLOW` plus a `fetch_error` field, distinct from a failure before the decision is finalized, which fails closed.
- **Session expiry is a hard stop** — 15-minute JWT, no silent renewal.

## Testing

Three layers: unit tests for the decision logic itself (no I/O), contract tests run against every backend, and scenario tests that run the entire gateway — real MCP transport, real auth headers, real audit log — against an in-memory GitHub stand-in. Every governance case below is a passing test, not a claim. Rule IDs below link to what each one actually checks in [Decision Procedure](#decision-procedure).

| Demo case | Test | Result |
|---|---|---|
| Happy path | `test_happy_path.py` | ALLOW |
| Unknown agent | `test_r01_unknown_agent.py` | DENY [`R01`](#r01) |
| Intent spoofing | `test_intent_spoofing.py` | DENY [`R07`](#r07) |
| Wrong resource type for intent | `test_r09_outside_intent.py` | DENY [`R09`](#r09) |
| Sensitive path | `test_r08_secret_path.py` | DENY [`R09`](#r09) (direct read) / DENY [`R08`](#r08) (PR diff touching a secret file) |
| Same call, different agents | `test_same_call_different_agents.py` | ALLOW for the review agent, DENY [`R09`](#r09) for the triage agent |
| Default deny (unmapped tool) | `test_default_deny.py` | DENY [`R06`](#r06) |
| Prompt injection | `test_prompt_injection.py` | DENY [`R09`](#r09) — the hijacked call is blocked regardless of what the issue body asked for |
| Audit trail | `test_audit_trail.py` | Exactly one row per call, including the two-step `get_pr_diff` lookup |
| Repo-attribute access control | `test_repo_abac.py` | DENY [`R10`](#r10) for a user without repo access, ALLOW for one with it |
| Read vs. write | `test_read_vs_write.py` | DENY [`R07`](#r07) for a write outside the intent, ALLOW for one inside it |
| Filtered tool discovery | `test_filtered_discovery.py` | Catalog is filtered per agent; enforcement still applies even if a hidden tool is called directly |
| Human identity propagation | `test_identity_propagation.py` | Same agent, same intent, different user → different outcome |
| Kill switch | `test_kill_switch.py` | DENY [`R02`](#r02), takes effect immediately, other agents unaffected |
| Fail closed | `test_fail_closed.py` | DENY [`R03`](#r03) on an expired session; DENY `fail_closed` on a mid-decision failure |

See [README → Testing](../README.md#testing) for the exact commands, including the live-GitHub contract tests.

## Language Choice

**Current version: Python** — an integration choice, not a performance one. The official MCP SDK is most mature here, and `pydantic`, `httpx`, `Starlette`, and Streamlit cover every other piece with no custom plumbing.

**Commercially, the product isn't GitHub-specific** — it's a general MCP/data-platform governance layer (GitHub is just this demo's one backend). The swappable-backend pattern already built (`GitHubBackend` as a `Protocol`) is what has to generalize: one interface per platform, policy/audit core staying backend-agnostic as it already is. That shifts the language question from "what fits GitHub" to "what fits building many backend adapters safely."

| Language | Strength | Weakness |
|---|---|---|
| Go | Goroutines make the [Scale](#scale) concurrency bug structurally hard to cause, not just avoidable by discipline; the standard language for this product category | Thinner MCP and schema-validation ecosystems than TS/Python |
| TypeScript/Node | Closest to MCP's own reference ecosystem — most community servers are TS-first | Same single-threaded-event-loop exposure as Python |
| Rust | Strongest safety guarantees, useful with many third-party credentials in play | Slower to build and review, smaller hiring pool |

**My pick: Go.** TypeScript is the stronger ecosystem fit, but Go is the stronger reliability fit — it doesn't just make the blocking-call mistake in [Scale](#scale) less likely, it makes it structurally hard to cause at all, which matters more for a product whose entire pitch is trustworthy enforcement under load. Not urgent: Python is fine today, and the [Scale](#scale) fix (an async client) closes the same bug without a rewrite.

**If the product already exists inside a company with an established stack**, that wins over any of the above — shared auth, observability, and deployment tooling matters more than this argument.

## Scale

Numbers below are architectural estimates from how each piece is actually built, not load-test results — but they're concrete, and each limit maps to one specific, small fix.

| Component | Current capacity | What limits it | To scale it |
|---|---|---|---|
| Gateway process | ~1 GitHub call in flight at a time — each one blocks the event loop for its full round trip (typically 100-800ms) | `RestBackend` uses a blocking `httpx.Client`, called directly inside the async MCP handler | Swap to `httpx.AsyncClient` (or run the call via `asyncio.to_thread`) — the loop could then hold hundreds of calls in flight, limited by GitHub, not Python |
| GitHub API | 5,000 requests/hour, shared across every agent and every user (GitHub's documented per-token limit) | One fine-grained PAT for the whole gateway | Per-user OAuth tokens instead of one shared PAT — N active users means N separate 5,000/hour budgets instead of one divided N ways |
| Audit log | Single writer; SQLite on local disk typically sustains low hundreds to low thousands of small inserts/second | One `audit` table, one `threading.Lock`, a commit per row (`gateway/core/audit.py`) | Swap SQLite for Postgres (or enable WAL mode as a smaller first step) — `AuditLog` is one module, so this is a backend swap, not a redesign |
| MCP sessions | Up to 10,000 concurrent sessions (the MCP SDK's own built-in default) | Not a near-term constraint | Pass `max_sessions=N` to `StreamableHTTPSessionManager` in `gateway/server.py` if it ever becomes one — not currently exposed as config, so this is a one-line code change |

At the current scale — 2 configured users, 2 agents, 2 demo repos — none of this is felt: a handful of people clicking through the demo at once, each waiting on their own response, stays well under every limit above.

## Decision Procedure

Each tool call runs through ordered checks. The first check that fails denies the call and names its rule. A call that passes every check is allowed. Every outcome writes exactly one audit row.

Grouped below by the order they actually run in code — not a flat R01→R12 list. As covered in `Fixes Made During Implementation` above, R08 and R11 need a resolved tier, which for `get_pr_diff` isn't known until a GitHub call runs; every cheap, context-free check runs first so an unauthorized caller is denied before that call ever happens.

**Phase 1 — cheap, context-free (**`gateway/core/auth.py` **for R01–R03,** `gateway/core/policy.py`**'s** `decide_phase1` **for the rest):**

1. **R01 Agent authenticated:** the agent token is valid, or DENY `unknown_agent`.
2. **R02 Kill switch:** the agent is not disabled, or DENY `agent_disabled`.
3. **R03 Session token valid:** the session token is signed by the Streamlit app and not expired, or DENY `invalid_session`. It gives the user and the declared intent.
4. **R04 Intent registered:** the intent belongs to this agent, or DENY `intent_not_registered`.
5. **R05 User may use agent:** the user's role is in the agent's allowed roles, or DENY `user_not_entitled`.
6. **R06 Tool known:** the tool is in the canonical tool catalog, or DENY `unmapped_tool`.
7. **R07 Argument constraints:** for example, labels only; otherwise DENY `arg_constraint`.
8. **R09 Within intent:** the resource type and action are in the intent, or DENY `outside_intent`.
9. **R10 Repo permitted:** the repo is in both the intent's and the user's lists, or DENY `repo_not_permitted`.
10. **R12 Write permitted:** for writes, the user may perform this write, or DENY `write_not_permitted`.

**Phase 2 — tier-dependent (**`decide_phase2`**; only reached once phase 1 passes):**

11. **R08 Restricted data:** a T3 resource is always DENY `restricted_data`, no override.
12. **R11 Tier permitted:** the tier is at or below the lower of the intent's and the user's caps, or DENY `tier_exceeded`.

**ALLOW:** forward the call to the backend, run the response through redaction, then return it.

- **Fail closed:** any error, such as a bad policy file, an unreachable upstream, or a parse failure, returns DENY `fail_closed`. Never ALLOW.
- **Filtered discovery:** `tools/list` returns only the tools mapped to resource types in the agent's registered intents. Discovery is a convenience, not the control; the call-time checks above enforce regardless.
- **Audit row fields:** timestamp, agent, user, intent, tool, repo, resource type, tier, action, decision, rule ID, latency.
