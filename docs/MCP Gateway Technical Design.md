# TrustLogix Take-Home: Agent & MCP Gateway Design

Oct 31, 2026 · @Sk

## Summary

Two agents reach GitHub only through one MCP gateway, which allows, denies or redacts each call based on four inputs: the agent's identity, its declared intent, the human user behind it, and the data being requested. How the gateway reaches GitHub is a single swappable layer: it ships as its own MCP gateway calling the GitHub REST API, and switching to a proxy in front of GitHub's official MCP server is one config change.

- **Brief (Deepak Reddy):** two AI agents with different purposes, each exposed through Streamlit. GitHub is the data source. An MCP gateway in between enforces governance, using identity/intent and data context to allow or deny actions.
- **Thesis:** enforcement lives outside the model. The LLM proposes tool calls; the gateway decides. This mirrors TrustAI's MCP Data Gateway and Intent-Based Access Control.

**Goals**

- Every agent call to GitHub passes through the gateway, which allows or denies it based on agent identity, declared intent, human user and data context.
- The gateway is the only component holding GitHub credentials.
- Switching between own-gateway and proxy mode changes no agent, policy, test or demo code.
- Every decision is deterministic, cites a rule, and is recorded.

**Non-goals**

- Real user login. A user picker simulates the identity provider.
- Production hardening: high availability, secrets management, rate limiting.
- Policy languages such as OPA or Cedar. YAML is enough for the demo.

**Demo cases**

- **Must have:** happy path, unknown agent, intent spoofing, wrong resource type for the intent, sensitive path, same call with different agents, default deny, prompt injection, audit trail.
- **Should have:** repo attribute (ABAC), read vs. write, filtered tool discovery, human identity propagation, kill switch, fail closed.
- **Nice to have:** output redaction, anomaly flag, tamper-evident audit log.

## Key decision: own gateway first, proxy switchable

The brief reads like a gateway that owns its GitHub tools, so that is the default mode. TrustLogix has not yet answered the clarifying question, and if they prefer the proxy, it is a config change plus one adapter.

| Mode | Config | What happens behind each tool | Status |
| --- | --- | --- | --- |
| Own gateway | `backend: rest` | `RestBackend` calls the GitHub REST API | Default; built first |
| MCP proxy | `backend: mcp` | `McpBackend` calls GitHub's official MCP server | Built if requested, about half a day |

**What makes the switch instant:**

- **Agents always see the gateway's own tool catalog**, never upstream tool names. The agents, the policy and the demo are therefore identical in both modes.
- **The policy engine never sees GitHub.** It takes a data context (repo, resource type, tier, action) and returns a decision.
- **Both backends return the same typed objects**, so redaction and audit behave identically.
- **One contract test suite** runs against every backend. A backend is done when it passes.

**How to describe proxy mode in the README:** a curated facade over the upstream MCP server. Exposing a fixed catalog instead of passing upstream tools through is itself a least-privilege decision.

## The two agents

The two agents serve different people and need overlapping but different data. Only one of them reads content that outsiders can write, which makes it the natural target for the prompt-injection demo.

| Agent | ID | Used by | What it does | Never needs |
| --- | --- | --- | --- | --- |
| Issue Triage Assistant | `triage-agent` | Support engineers, for working the incoming issue queue | Summarizes new issues, finds likely duplicates, suggests labels and applies them | Source code, config, secrets; closing, merging or editing issue text |
| PR Review Assistant | `review-agent` | Software engineers, for reviewing pull requests | Reads a PR's description, diff, changed files and linked issues; explains code; posts review comments | Secrets; approving or merging; CI logs; security alerts; admin actions |

- **Why this pair:** issues are untrusted input, since anyone can file one, while PR review needs code. The same file read is legitimate for one agent and a violation for the other, which powers the "same call, different agents" demo.
- **Agents are thin:** each one is a tool-calling loop around a hosted LLM plus an MCP client pointed at the gateway. Agents hold no GitHub credentials and no policy logic.

## Human roles and entitlements

Every request carries a human user. What the agent can do is capped by what that person may do, so the same agent can get different answers for different users.

| User | Role | May use agents | Repos | Max tier | May write |
| --- | --- | --- | --- | --- | --- |
| alice | engineer | review-agent, triage-agent | storefront (public), payments-api (private) | T2 confidential | yes |
| bob | support | triage-agent | storefront (public) | T1 internal | labels only |

- **Effective permission = human ∩ agent ∩ intent:** repos are the overlap of the intent's and the user's repos; the tier cap is the lower of the two; a write needs both sides to allow it.
- **Intersection in action:** alice may read T2 config herself, but no agent intent reaches T2, so her agents still cannot read config.
- **Source of truth:** in the demo, users are a section of the policy YAML. In production they would come from the identity provider (Okta, Entra) as claims in a signed token.

## Data classification

The gateway labels every request with a resource type and one of four sensitivity tiers. T3 restricted data is never released to any agent, under any intent.

**Tiers:** T0 public · T1 internal · T2 confidential · T3 restricted.

| Resource type | Examples | Tier | How the gateway classifies it |
| --- | --- | --- | --- |
| `secret` | `.env*`, `*.pem`, `*.key`, `secrets/**`, `id_rsa*` | T3 | File path pattern; checked first, wins over everything |
| `config` | `config/**`, `deploy/**` | T2 | File path pattern |
| `ci_log` | Actions run logs | T2 | No catalog tool exposes it |
| `security_alert` | Code scanning, Dependabot, secret scanning | T2 | No catalog tool exposes it |
| `org_member` | Org members, teams | T2 | No catalog tool exposes it |
| `code` | `src/**`, `tests/**`, any other file | T1 | File path, the default for files |
| `pr_diff` | PR diff, changed-file list | T1, raised to the highest tier among changed files | Tool name, then the changed paths |
| `pull_request` | PR title, description, metadata | T1 | Tool name |
| `commit` | Commit messages, metadata | T1 | Tool name |
| `issue` | Issue title and body | T1 | Tool name |
| `issue_comment` | Issue and PR comments | T1 | Tool name |
| `docs` | `README*`, `docs/**` | T0 | File path pattern |
| `label` | Label list, labels on an issue | T0 | Tool name |
| `repo_meta` | Repo info, branches, tags, releases | T0 | Tool name |

- **Path rules run first-match, most restrictive first.** So `docs/.env` is a secret, not a doc.
- **Repo visibility is a separate attribute** (public or private), read from the policy file's repo registry rather than looked up at request time.
- **A PR diff inherits its riskiest file.** A PR that touches `.env` makes its whole diff T3, so neither agent can read it.

## Intent catalog (the policy)

Four intents, two per agent. Each grants a fixed set of resource types, a tier cap, a repo list and at most one narrowly constrained write. Anything not listed is denied.

| Agent        | Intent            | Purpose                                | Read: resource types                                                   | Write: action and constraint                                                        | Max tier | Repos                    |
| ------------ | ----------------- | -------------------------------------- | ---------------------------------------------------------------------- | ----------------------------------------------------------------------------------- | -------- | ------------------------ |
| triage-agent | `triage_issues`   | Classify and label incoming issues     | issue, issuecomment, label, repometa                                   | `add_labels`: only the labels field may change                                      | T1       | storefront, payments-api |
| triage-agent | `find_duplicates` | Find issues that duplicate a given one | issue, label                                                           | none                                                                                | T1       | storefront, payments-api |
| review-agent | `review_pr`       | Review a pull request                  | pullrequest, prdiff, code, docs, commit, issue, issuecomment, repometa | `post_review_comment`: review event must be COMMENT (no APPROVE, no REQUESTCHANGES) | T1       | storefront, payments-api |
| review-agent | `explain_code`    | Answer questions about the codebase    | code, docs, repometa                                                   | none                                                                                | T1       | storefront, payments-api |

**Global rules, applied to every intent:**

- T3 `secret` is always denied, with no override.
- Merge, approve, close, delete, branch and workflow actions appear in no intent, so they are always denied.
- Code search is denied for every intent, because its results contain snippets from any file and bypass path rules.

**YAML shape (excerpt):**

```yaml
agents:
  triage-agent:
    allowed_roles: [support, engineer]
    intents:
      triage_issues:
        read: [issue, issue_comment, label, repo_meta]
        write:
          add_labels: {only_fields: [labels]}
        max_tier: T1
        repos: [storefront, payments-api]
      find_duplicates:
        read: [issue, label]
        max_tier: T1
        repos: [storefront, payments-api]
  review-agent:
    allowed_roles: [engineer]
    intents:
      review_pr:
        read: [pull_request, pr_diff, code, docs, commit, issue, issue_comment, repo_meta]
        write:
          post_review_comment: {require: {event: COMMENT}}
        max_tier: T1
        repos: [storefront, payments-api]
      explain_code:
        read: [code, docs, repo_meta]
        max_tier: T1
        repos: [storefront, payments-api]
users:
  alice: {role: engineer, repos: [storefront, payments-api], max_tier: T2, write: any}
  bob:   {role: support,  repos: [storefront],               max_tier: T1, write: [add_labels]}
```

## Decision procedure

Each tool call runs through ordered checks. The first check that fails denies the call and names its rule. A call that passes every check is allowed. Every outcome writes exactly one audit row.

1. **R01 Agent authenticated:** the agent token is valid, or DENY `unknown_agent`.
2. **R02 Kill switch:** the agent is not disabled, or DENY `agent_disabled`.
3. **R03 Session token valid:** the session token is signed by the Streamlit app and not expired, or DENY `invalid_session`. It gives the user and the declared intent.
4. **R04 Intent registered:** the intent belongs to this agent, or DENY `intent_not_registered`.
5. **R05 User may use agent:** the user's role is in the agent's allowed roles, or DENY `user_not_entitled`.
6. **R06 Tool known:** the tool is in the canonical tool catalog, or DENY `unmapped_tool`.
7. **R07 Argument constraints:** for example, labels only, or event must be COMMENT; otherwise DENY `arg_constraint`.
8. **R08 Restricted data:** a T3 resource is always DENY `restricted_data`.
9. **R09 Within intent:** the resource type and action are in the intent, or DENY `outside_intent`.
10. **R10 Repo permitted:** the repo is in both the intent's and the user's lists, or DENY `repo_not_permitted`.
11. **R11 Tier permitted:** the tier is at or below the lower of the intent's and the user's caps, or DENY `tier_exceeded`.
12. **R12 Write permitted:** for writes, the user may perform this write, or DENY `write_not_permitted`.
13. **ALLOW:** forward the call to the backend, run the response through redaction (R20), then return it.

- **Fail closed:** any error, such as a bad policy file, an unreachable upstream or a parse failure, returns DENY `fail_closed`. Never ALLOW.
- **Filtered discovery:** `tools/list` returns only the tools mapped to resource types in the agent's registered intents. Discovery is a convenience, not the control; the call-time checks above enforce regardless.
- **Audit row fields:** timestamp, agent, user, intent, tool, repo, resource type, tier, action, decision, rule ID, latency.

## System overview

The gateway has three layers: a northbound MCP server exposing a fixed tool catalog, a core that makes every decision, and a southbound backend that is the only code touching GitHub.

[embedded content: gateway layers · backend: rest (solid) or mcp (dashed)

| Component | Responsibility | Knows about GitHub? |
| --- | --- | --- |
| Streamlit app | Pages per agent and an admin page; user and intent pickers; signs the session token | No |
| Agents | Tool-calling loop around a hosted LLM; MCP client pointed at the gateway | No |
| Northbound | MCP server over streamable HTTP; registers the canonical tools; filters tool discovery per agent | No |
| Core | Authenticates, resolves data context, evaluates policy, redacts, audits | No |
| Southbound | `GitHubBackend` interface with `RestBackend`, `McpBackend` and a `FakeBackend` for tests | Yes, only here |

## Request lifecycle

A tool call goes from the user's message to GitHub and back in nine steps. The policy decision happens before any GitHub data is fetched, and every outcome writes one audit row.

1. **Session start (Streamlit):** the user picks who they are and an intent. The app signs a session token carrying user, agent, intent and expiry, and hands it to the agent code. The LLM never sees it.
2. **Tool proposal (agent):** the LLM proposes a tool call, for example `read_file(repo="storefront", path=".env")`.
3. **Transport (agent → gateway):** the agent's MCP client sends the call with two headers: its own agent token, and the session token.
4. **Authenticate (core):** verify the agent token, check the kill switch, verify the session token's signature and expiry, and confirm the session's agent matches the calling agent.
5. **Resolve context (core):** turn the tool name and arguments into a data context: repo, visibility, resource type, tier, action.
6. **Decide (core):** run the ordered policy rules (R01 to R12). The first failing rule returns DENY with its rule ID.
7. **Fetch (southbound):** on ALLOW, the backend makes the GitHub call and returns a typed object.
8. **Redact (core):** scrub secret-shaped strings and emails from the response, counting what was removed.
9. **Audit and return:** write the audit row, then return the result. On DENY, the agent gets a structured error naming the rule, so the LLM can explain it to the user.

**Exception: PR diffs need metadata first.** A diff's tier depends on which files it touches. For `get_pr_diff`, step 5 asks the backend for the changed-file list (paths only, no contents), classifies the diff by its riskiest path, and only then decides. That metadata lookup is itself logged.

## Canonical tool catalog

Agents see these eleven tools and nothing else, in both modes. Dangerous operations such as merge, approve, close and code search simply do not exist in the catalog, so the catalog itself is the first layer of least privilege.

| Tool                  | Arguments            | Resource type                           | Action | Notes                                      |
| --------------------- | -------------------- | --------------------------------------- | ------ | ------------------------------------------ |
| `read_file`           | repo, path           | From path: secret, config, docs or code | read   | Path rules set the tier                    |
| `list_issues`         | repo, state          | issue                                   | read   |                                            |
| `get_issue`           | repo, number         | issue                                   | read   |                                            |
| `get_issue_comments`  | repo, number         | issuecomment                            | read   |                                            |
| `search_issues`       | repo, query          | issue                                   | read   | Always scoped to one repo                  |
| `list_labels`         | repo                 | label                                   | read   |                                            |
| `add_labels`          | repo, number, labels | label                                   | write  | Can only add labels; no other field exists |
| `get_pr`              | repo, number         | pullrequest                             | read   |                                            |
| `get_pr_diff`         | repo, number         | prdiff                                  | read   | Tier = riskiest changed path               |
| `list_commits`        | repo, limit          | commit                                  | read   |                                            |
| `post_review_comment` | repo, number, body   | pullrequest                             | write  | Comment only; there is no approve tool     |

- **Argument constraints become structural.** In own-gateway mode, `add_labels` cannot edit an issue's title because the tool has no such argument. In proxy mode, `McpBackend` keeps the same guarantee by sending only the labels field to the upstream tool and only the COMMENT review event.
- **Defense in depth:** the core still default-denies any tool name not in this table, in case one is registered by mistake.

## Backend interface and data models

One interface, three implementations. The core only ever talks to the interface, and every implementation returns the same typed objects.

```python
class GitHubBackend(Protocol):
    # reads
    def read_file(self, repo: str, path: str) -> FileContent: ...
    def list_issues(self, repo: str, state: str) -> list[Issue]: ...
    def get_issue(self, repo: str, number: int) -> Issue: ...
    def get_issue_comments(self, repo: str, number: int) -> list[Comment]: ...
    def search_issues(self, repo: str, query: str) -> list[Issue]: ...
    def list_labels(self, repo: str) -> list[str]: ...
    def get_pr(self, repo: str, number: int) -> PullRequest: ...
    def list_pr_files(self, repo: str, number: int) -> list[str]: ...  # paths only, used for classification
    def get_pr_diff(self, repo: str, number: int) -> PrDiff: ...
    def list_commits(self, repo: str, limit: int) -> list[Commit]: ...
    # writes
    def add_labels(self, repo: str, number: int, labels: list[str]) -> None: ...
    def post_review_comment(self, repo: str, number: int, body: str) -> None: ...
```

| Implementation | Used for | How it works |
| --- | --- | --- |
| `RestBackend` | Own-gateway mode (default) | Calls the GitHub REST API with a fine-grained token limited to the two demo repos |
| `McpBackend` | Proxy mode | MCP client to the pinned official GitHub MCP server; maps each method to the upstream tool and arguments |
| `FakeBackend` | Tests | In-memory repos and issues; makes every core test fast and offline |

**Core data models (pydantic):**

| Model           | Fields                                                                              |
| --------------- | ----------------------------------------------------------------------------------- |
| `SessionClaims` | user, agent, intent, issuedat, expiresat, sessionid                                 |
| `ToolCall`      | agentid, session (SessionClaims), tool, args                                        |
| `DataContext`   | repo, visibility, resourcetype, tier, action, paths                                 |
| `Decision`      | outcome (ALLOW, DENY), ruleid, reason                                               |
| `AuditRecord`   | timestamp, agent, user, intent, tool, data context, decision, redactions, latencyms |

`FakeBackend` matters as much as the other two: it is the proof that the seam is real, because the whole core runs and is tested without GitHub.

**Proxy-mode translation (**`McpBackend`**):** each canonical tool maps to one upstream tool, and the adapter keeps the catalog's constraints.

| Canonical tool | Upstream tool (illustrative) | Translation rule |
| --- | --- | --- |
| `read_file` | `get_file_contents` | Pass repo and path |
| `list_issues`, `get_issue`, `get_issue_comments` | Same-named issue tools | Pass through |
| `search_issues` | `search_issues` | Always add the repo qualifier to the query |
| `list_labels` | `list_labels` | Pass through |
| `add_labels` | `update_issue` | Send only the labels field |
| `get_pr` | `get_pull_request` | Pass through |
| `list_pr_files`, `get_pr_diff` | `get_pull_request_files`, `get_pull_request_diff` | Pass through |
| `list_commits` | `list_commits` | Pass through |
| `post_review_comment` | `create_and_submit_pull_request_review` | Event fixed to COMMENT |

Upstream tools never reach agents, so new or renamed upstream tools cannot widen access. Recent `github-mcp-server` releases consolidate some tools into one tool with a method argument; pin a version and build this table from its `tools/list` output.

## Identity: agent tokens and session tokens

Two credentials travel with every call. The agent token proves which agent is calling; the session token, signed by the Streamlit app, proves which human it acts for and which intent was declared.

| Credential | Issued by | Carries | Format | Header |
| --- | --- | --- | --- | --- |
| Agent token | Configured per agent at deploy time | Agent identity | Random secret; the gateway stores only its hash | `Authorization: Bearer ...` |
| Session token | Streamlit app, at session start | user, agent, intent, expiry (15 minutes), session ID | JWT signed with HS256, using a key shared only by the app and the gateway | `X-Session-Token` |

- **Why two tokens:** a stolen agent token alone cannot act for a user, and a session token alone cannot be used by a different agent, because the gateway checks that the session's agent claim matches the authenticated agent.
- **Why the app signs intent:** intent is declared by the application, outside the LLM. Prompt injection can change what the LLM asks for, but not the signed intent the gateway checks against.
- **Production path:** the session token would be issued by the identity provider (OIDC), and agent identity would use workload identity or mTLS instead of shared secrets.

## Policy, redaction, audit and kill switch

Everything in this section lives in the core and is identical in both modes. The rules and intents are specified in the Intent catalog and Decision procedure sections above; this section covers how they run.

| Piece | Implementation | Key behavior |
| --- | --- | --- |
| Policy engine | Pure function: `decide(call, context, policy) -> Decision`, loaded from `policy.yaml` and `classification.yaml` | Ordered rules R01 to R12; first failure wins; default deny; any exception returns DENY `fail_closed` |
| Classification | Path patterns, first match, most restrictive first | `.env*`, `*.pem`, `secrets/**` are T3 and always denied |
| Redaction | Regex pass over every response | Masks token shapes (`ghp_`, `AKIA`, `sk-`, private key headers) and emails; records the count in the audit row |
| Audit log | SQLite, append-only table, one row per decision | Each row stores a hash of the previous row, so editing history breaks the chain (stretch goal) |
| Kill switch | `agent_state` table in the same SQLite file, toggled from the admin page | Checked on every call at R02; takes effect immediately; other agents unaffected |
| Anomaly flag (stretch) | Count of denials per agent in a rolling five-minute window | Over a threshold, the admin page shows a flag on that agent |

- **Policy is a pure function** with no I/O. That makes it trivially testable and keeps every decision reproducible from the audit row.
- **Policy reload:** the YAML is loaded at startup and validated against a schema. An invalid file stops the gateway from starting, rather than running with a partial policy.

## Agents and Streamlit app

The agents are deliberately thin, and the Streamlit app is built to make every gateway decision visible during the demo.

**Agents**

- One shared runner: a tool-calling loop around a hosted LLM, with an MCP client connected to the gateway over streamable HTTP.
- Each agent is a configuration of that runner: its own agent token, system prompt and display name.
- The tool list comes from the gateway's filtered discovery, so each agent's LLM only sees tools its intents can use.
- A DENY comes back as a structured tool error containing the rule ID. The system prompt tells the LLM to explain the denial to the user instead of retrying around it.
- The LLM provider sits behind one small client function, so changing providers touches one file.

**Streamlit pages**

| Page | What the user does | What makes the demo visible |
| --- | --- | --- |
| Triage | Picks user and intent, then chats with the triage agent | Each tool call shows inline with an ALLOW or DENY badge and the rule ID |
| Review | Same, for the review agent | Same badges |
| Admin | Views the audit log, toggles kill switches, sees anomaly flags | Filters by agent, user, decision; the rule ID on every row |

Agents run inside the Streamlit process, but talk to the gateway over HTTP with their own tokens. The process boundary that matters for security is the one between agent and gateway.

## Architecture options

Six ways to put governance between the agents and GitHub were considered. The design does not pick between A and B: it builds both behind one backend interface, with B as the default because it matches the brief's wording.

| Option | How it works | Pros | Cons | Verdict |
| --- | --- | --- | --- | --- |
| **A. MCP proxy in front of GitHub's official MCP server** | Gateway is an MCP server to agents and an MCP client to `github-mcp-server`; it holds the only GitHub token | Governs a server it didn't write, which is the enterprise reality and TrustLogix's problem; real MCP end to end; less GitHub code to own | Large, changing upstream tool surface; generic tools mix powers; one more process | **Supported mode** (`backend: mcp`) |
| **B. Gateway with its own GitHub tools** | Gateway is the MCP server; a handful of tools call the GitHub REST API directly | Full control of the data context; simpler to test; no upstream to track | Gateway and data server blur into one, so the "between" is less visible; you own every tool | **Default mode** (`backend: rest`) |
| **C. Plain HTTP gateway, no MCP** | Agents call REST endpoints on the gateway | Simplest to build and test | Ignores "MCP Gateway" in the brief; loses tool discovery and filtering | Rejected |
| **D. Policy library inside each agent** | Each agent calls a policy check before using its own GitHub token | No extra network hop | Agent holds credentials and can skip the check, so a compromised agent bypasses everything | Rejected |
| **E. GitHub-native scoping only** | A separate fine-grained token or GitHub App per agent, no gateway | No custom code; GitHub enforces it | Cannot see intent, file path, human user or arguments; no decision audit | Rejected as the control; **kept as a defense-in-depth layer** under both modes |
| **F. Off-the-shelf gateway plus a policy plugin** | For example, IBM ContextForge with a custom policy hook | Fast to stand up; production features for free | Hides your work; the policy engine is the assignment; learning curve | Rejected |

**Why E matters most:** it is the "identity is enough" position TrustLogix argues against. Comparing what each layer can enforce is the clearest justification for the gateway:

| Can it enforce... | GitHub token scoping (E) | Gateway |
| --- | --- | --- |
| Which repos | yes | yes |
| Read vs. write per category (contents, issues, PRs) | yes | yes |
| Which file paths (`.env`, `config/`) | no | yes |
| Which intent the agent is pursuing | no | yes |
| Which human the agent acts for | no | yes |
| Argument limits (labels only; comment, not approve) | no | yes |
| An audit row saying who, what, why and which rule | no | yes |
| Instant per-agent kill switch | revoke the token | yes, one flag |

## Risks by mode

Each mode carries known risks, and the design handles all of them. The proxy-only risks are absorbed by the canonical catalog and `McpBackend`; the rest apply to both modes.

| Risk | Applies to | Why it matters | Mitigation |
| --- | --- | --- | --- |
| Upstream tool surface is large and changes between releases | Proxy | A new tool could expose data no rule covers | Agents only ever see the canonical catalog; pin the upstream version; build the translation table from its `tools/list` |
| Generic tools mix powers | Proxy | `update_issue` can change labels and also title, body and state | `McpBackend` sends only the fields the canonical tool allows |
| Tools return more than the request implies | Both | Code search returns snippets from any file; a PR diff can include `.env` | No code search in the catalog; tier a diff by its riskiest changed path; redact responses |
| The gateway's GitHub token is a super-credential | Both | Compromise of the gateway exposes everything the token can reach | Fine-grained token limited to the two demo repos and minimal permissions (option E as a floor). In production: per-user OAuth on behalf of the human, so GitHub also enforces the user's own rights |
| Agents could forge user or intent headers | Both | A compromised agent could claim to be alice or claim a broader intent | The Streamlit app mints a signed, short-lived session token carrying the user and the intent. Agent code forwards it; the LLM never sees it |

## Design decisions

Each choice below is one you should be able to defend in one sentence, along with the alternative you rejected.

| Decision        | Choice                                                                                                             | Why                                                                                                                               | Rejected alternative                                          |
| --------------- | ------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------- |
| Upstream        | Swappable backend behind one interface: own REST tools by default, proxy to GitHub's official MCP server by config | The brief reads like own tools, while TrustLogix's product governs servers it didn't write; the seam supports both without rework | Committing to a single upstream                               |
| Tool catalog    | The gateway's own canonical catalog, in both modes                                                                 | Agents, policy and demo never change between modes; dangerous operations don't exist                                              | Passing upstream tools straight through                       |
| Transport       | MCP over streamable HTTP                                                                                           | A network boundary with headers for tokens; agents and gateway are separate processes                                             | stdio, which runs in-process and has no auth                  |
| Agent identity  | One secret per agent, presented as a bearer token                                                                  | Each agent is distinguishable and revocable                                                                                       | A shared key, which makes agents indistinguishable            |
| User and intent | Signed, short-lived session token minted by the Streamlit app                                                      | Anchors declared intent outside the LLM; agents cannot forge it                                                                   | Headers set by agent code, or intent inferred from the prompt |
| Intent scope    | One intent per session, chosen in the UI                                                                           | Matches one task; limits blast radius; easy to audit                                                                              | Intent per request, which an agent could change mid-task      |
| Policy format   | YAML, default deny, ordered rules with IDs                                                                         | Deterministic, readable, reviewable in a pull request                                                                             | OPA or Cedar (a next step for production)                     |
| Outcomes        | ALLOW and DENY on calls, plus REDACT on responses                                                                  | Covers the brief and adds defense in depth                                                                                        | REQUIREAPPROVAL (a next step)                                 |
| Classification  | Four tiers; path rules first-match, most restrictive first                                                         | Mirrors enterprise data classes; predictable                                                                                      | Content scanning to classify, which is slow and probabilistic |
| Least privilege | Filtered discovery plus call-time checks                                                                           | The agent never sees tools it can't use, and calls are enforced anyway                                                            | Call-time checks only                                         |
| Failure mode    | Fail closed                                                                                                        | A security control must not fail open                                                                                             | Fail open for availability                                    |
| Audit           | SQLite, append-only, one row per decision                                                                          | Easy to query and show in the admin page                                                                                          | Plain log files                                               |
| Kill switch     | A per-agent flag in gateway state, toggled from the admin page                                                     | Takes effect on the next call, other agents unaffected                                                                            | Rotating the agent's secret                                   |
| Agents          | Thin tool-calling loop around a hosted LLM                                                                         | Intelligence in the model, security in the gateway                                                                                | A heavy agent framework                                       |
| Streamlit       | One app: a page per agent plus an admin and audit page                                                             | Meets "each exposed through Streamlit" with less overhead                                                                         | Two separate apps                                             |

## Repo layout and configuration

The folder structure mirrors the three layers, so a reviewer can see the seam just by reading the tree. Only `gateway/backends/` imports anything GitHub-specific.

```
gateway/
  server.py              # northbound: MCP server, canonical tools, filtered discovery
  core/
    models.py            # SessionClaims, ToolCall, DataContext, Decision, AuditRecord
    auth.py              # agent tokens, session token verification, kill switch check
    context.py           # tool + args -> DataContext; path classification
    policy.py            # decide(): ordered rules, pure function
    redact.py
    audit.py             # SQLite audit log, agent_state
  backends/
    base.py              # GitHubBackend protocol
    rest.py              # RestBackend
    mcp_proxy.py         # McpBackend
    fake.py              # FakeBackend
config/
  gateway.yaml           # backend: rest | mcp, upstream URL, ports
  policy.yaml            # agents, intents, users, repos
  classification.yaml    # path patterns -> resource type and tier
agents/
  runner.py              # tool-calling loop + MCP client
  llm.py                 # provider client
  prompts/
app/
  Home.py
  pages/ (1_Triage.py, 2_Review.py, 3_Admin.py)
tests/
  unit/                  # policy, context, redaction
  contract/              # one suite, run against every backend
  scenarios/             # one test per demo case
docker-compose.yml
README.md
```

`config/gateway.yaml`**:**

```yaml
backend: rest                 # switch to mcp for proxy mode
github:
  token_env: GITHUB_TOKEN     # read from environment, never committed
  upstream_mcp_url: http://github-mcp:8082/mcp   # used only when backend: mcp
server:
  port: 8080
audit:
  db_path: data/audit.db
```

**Secrets:** GitHub token, agent tokens and the session signing key all come from environment variables. Nothing secret is committed, and `.env.example` documents what to set.

## Testing strategy

Three layers of tests, each tied to a guarantee. The scenario tests double as the demo script, so every demo case is proven before it is recorded.

| Layer | Runs against | What it proves | Example |
| --- | --- | --- | --- |
| Unit | Policy, context and redaction functions | Each rule fires correctly and cites its ID | `test_r08_secret_path_always_denied` |
| Contract | Every backend: Fake, Rest, Mcp | Backends are interchangeable; switching modes is safe | The same `read_file` and `add_labels` assertions pass for all three |
| Scenario | Full gateway with `FakeBackend` | Each demo case behaves as designed, end to end through MCP | Same `read_file(src/app.py)` call: review-agent ALLOW, triage-agent DENY `outside_intent` |

- **Unit and scenario tests run offline** and in seconds, using `FakeBackend`.
- **Contract tests against real backends** are marked as integration tests and run against the demo repos.
- **Prompt injection is tested at the gateway, not the LLM.** The scenario sends the exact call a hijacked agent would make and asserts DENY. The LLM's behavior is shown in the demo; the guarantee is shown in the test.
- **Fail-closed tests:** an invalid policy file, an expired session token and a backend that raises an exception each produce DENY, never ALLOW.

## Build plan, risks and open items

The plan builds from the inside out: the core first, tested offline, then GitHub, then agents and UI. Assuming a Tuesday, Nov 3 deadline (still to confirm), Wednesday is buffer or the proxy-mode switch.

| Day | Build | Done when |
| --- | --- | --- |
| Sat, Oct 31 | Repo skeleton, models, classification, policy engine, `FakeBackend`, unit tests | Every rule R01 to R12 has a passing test |
| Sun, Nov 1 | Northbound MCP server, auth and session tokens, audit, `RestBackend`; create demo repos and planted data | A scripted MCP client gets correct ALLOW and DENY against real GitHub |
| Mon, Nov 2 | Agent runner, Triage and Review pages with decision badges; scenario tests | All must-have demo cases pass as tests and work in the UI |
| Tue, Nov 3 | Admin page, kill switch, redaction; README; demo video | The demo video covers every must-have case |
| Wed, Nov 4 | Buffer, or `McpBackend` if TrustLogix asks for proxy mode | Contract tests pass with `backend: mcp` |

**Risks**

| Risk | Mitigation |
| --- | --- |
| Per-agent filtering of tool discovery is awkward in the MCP Python SDK | Fall back to call-time enforcement only, and show each agent's catalog in its page |
| Upstream tool names differ from what the design assumes (proxy mode only) | Pin a version and build `McpBackend`'s mapping from its `tools/list` |
| LLM ignores the denial message and keeps retrying | System prompt instructions plus a per-turn cap on tool calls in the runner |
| Deadline is earlier than assumed | Stretch items (anomaly flag, hash chain) are already outside the plan |

**Open items**

- [x] Confirm the deadline and deliverable format
- [x] Hear back on own gateway vs. proxy mode
- [x] Pick the LLM provider
- [x] Create the demo repos (`storefront` public, `payments-api` private) with planted fake secrets, a prompt-injection issue, and an issue containing a pasted log with a fake key and email
- [x] Create the fine-grained GitHub token for the two demo repos
- [ ] If proxy mode is requested: pin a `github-mcp-server` version and build the translation table from its `tools/list`
