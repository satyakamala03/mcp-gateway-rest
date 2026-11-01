"""Northbound: the MCP server exposing the canonical tool catalog, plus
plain HTTP /admin/* routes for the Streamlit admin page.

Orchestrates the request lifecycle: authenticate (R01-R03) -> resolve
context -> decide (R04-R12) -> fetch -> redact -> audit. get_pr_diff is the
one tool whose decision splits into two phases around a metadata fetch
(see gateway/core/policy.py's module docstring).
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.types import Receive, Scope, Send

from gateway.backends.base import BackendError, GitHubBackend
from gateway.core import context as context_mod
from gateway.core import policy as policy_mod
from gateway.core.audit import AuditLog
from gateway.core.auth import AgentTokenStore, AuthError, KillSwitch, verify_session
from gateway.core.catalog import FILE_SHAPED_RESOURCE_TYPES, TOOLS, TOOLS_BY_NAME
from gateway.core.models import ClassificationConfig, DataContext, Decision, PolicyConfig, SessionClaims, ToolCall
from gateway.core.redact import redact

logger = logging.getLogger("gateway")


@dataclass
class GatewayDeps:
    policy: PolicyConfig
    classification: ClassificationConfig
    audit: AuditLog
    token_store: AgentTokenStore
    kill_switch: KillSwitch
    signing_key: str
    admin_token: str
    backend: GitHubBackend


def _agent_catalog(agent_id: str, policy: PolicyConfig) -> list[types.Tool]:
    """Filtered discovery: only tools mapped to resource types granted by
    any of this agent's registered intents. Convenience only -- call-time
    checks enforce regardless of what's listed here.
    """
    agent = policy.agents.get(agent_id)
    if agent is None:
        return []

    granted_reads: set[str] = set()
    granted_writes: set[str] = set()
    for intent in agent.intents.values():
        granted_reads.update(intent.read)
        granted_writes.update(intent.write.keys())

    tools = []
    for tool in TOOLS:
        if tool.name == "read_file":
            if granted_reads & FILE_SHAPED_RESOURCE_TYPES:
                tools.append(tool)
            continue
        if tool.name in ("add_labels", "post_review_comment"):
            if tool.name in granted_writes:
                tools.append(tool)
            continue
        entry = _CLASSIFICATION_FOR_FILTER.tool_resource_types.get(tool.name)
        if entry and entry.resource_type in granted_reads:
            tools.append(tool)
    return tools


# set once at startup by create_app(); read-only afterwards. Only
# resource-type membership is needed for filtering, not tiers, so a module
# level reference to the loaded classification config is fine here.
_CLASSIFICATION_FOR_FILTER: ClassificationConfig | None = None


def _extract_headers(request: Request | None) -> dict[str, str]:
    """The streamable-HTTP transport sets RequestContext.request to the
    Starlette Request for the in-flight call; its Headers mapping is
    already case-insensitive.
    """
    if request is None:
        return {}
    return {"authorization": request.headers.get("authorization", ""),
            "x-session-token": request.headers.get("x-session-token", "")}


class _AuthContext:
    """Per-call auth result, derived from headers on the current request."""

    def __init__(self, agent_id: str, session: SessionClaims):
        self.agent_id = agent_id
        self.session = session


def _authenticate(headers: dict[str, str], deps: GatewayDeps) -> _AuthContext:
    auth_header = headers.get("authorization", "")
    presented_token = auth_header[len("Bearer ") :] if auth_header.startswith("Bearer ") else ""
    agent_id = deps.token_store.identify(presented_token)
    if agent_id is None:
        raise AuthError("R01", "agent token is invalid or unrecognized")

    deps.kill_switch.check(agent_id)

    session_token = headers.get("x-session-token", "")
    session = verify_session(session_token, signing_key=deps.signing_key, calling_agent_id=agent_id)
    return _AuthContext(agent_id, session)


def _deny_result(decision: Decision) -> types.CallToolResult:
    payload = {"decision": "DENY", "rule_id": decision.rule_id, "reason": decision.reason}
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(payload))],
        isError=True,
    )


def _fetch_error_result(message: str) -> types.CallToolResult:
    payload = {"decision": "ALLOW", "error": "fetch_failed", "message": message}
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(payload))],
        isError=True,
    )


def _success_result(data: Any) -> tuple[types.CallToolResult, int]:
    cleaned, redactions = redact(_to_jsonable(data))
    payload = {"result": cleaned, "redactions": redactions}
    result = types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(payload))],
        structuredContent=payload,
        isError=False,
    )
    return result, redactions


def _to_jsonable(obj: Any) -> Any:
    if isinstance(obj, list):
        return [_to_jsonable(o) for o in obj]
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return obj


_BACKEND_CALLS = {
    "read_file": lambda b, a: b.read_file(a["repo"], a["path"]),
    "list_issues": lambda b, a: b.list_issues(a["repo"], a["state"]),
    "get_issue": lambda b, a: b.get_issue(a["repo"], a["number"]),
    "get_issue_comments": lambda b, a: b.get_issue_comments(a["repo"], a["number"]),
    "search_issues": lambda b, a: b.search_issues(a["repo"], a["query"]),
    "list_labels": lambda b, a: b.list_labels(a["repo"]),
    "get_pr": lambda b, a: b.get_pr(a["repo"], a["number"]),
    "list_commits": lambda b, a: b.list_commits(a["repo"], a.get("limit", 20)),
}

_WRITE_CALLS = {
    "add_labels": lambda b, a: b.add_labels(a["repo"], a["number"], a["labels"]),
    "post_review_comment": lambda b, a: b.post_review_comment(a["repo"], a["number"], a["body"]),
}


def create_mcp_server(deps: GatewayDeps) -> Server:
    global _CLASSIFICATION_FOR_FILTER
    _CLASSIFICATION_FOR_FILTER = deps.classification

    server = Server("mcp-gateway")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        req_ctx = server.request_context
        headers = _extract_headers(req_ctx.request)
        try:
            auth = _authenticate(headers, deps)
        except AuthError:
            return []
        return _agent_catalog(auth.agent_id, deps.policy)

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> types.CallToolResult:
        start = time.perf_counter()
        req_ctx = server.request_context
        headers = _extract_headers(req_ctx.request)

        try:
            auth = _authenticate(headers, deps)
        except AuthError as exc:
            # No DataContext exists yet (we don't even know the intent is
            # valid), so this denial is audited with a minimal placeholder
            # context rather than skipped -- every outcome writes one row.
            placeholder = DataContext(repo=arguments.get("repo", "unknown"), visibility=None, resource_type="unknown", action="read")
            decision = Decision(outcome="DENY", rule_id=exc.rule_id, reason=exc.reason)
            deps.audit.record(
                agent="unknown", user="unknown", intent="unknown", tool=name,
                context=placeholder, decision=decision,
                latency_ms=(time.perf_counter() - start) * 1000,
            )
            return _deny_result(decision)

        call = ToolCall(agent_id=auth.agent_id, session=auth.session, tool=name, args=arguments)

        try:
            if name == "get_pr_diff":
                return await _handle_get_pr_diff(call, deps, start)
            return await _handle_standard_tool(call, deps, start)
        except Exception:
            # Fail closed: anything unexpected here (a bad classification
            # entry, a malformed arg the schema let through, ...) must
            # never fall through to an implicit ALLOW. The decision wasn't
            # finalized, so this is a policy-side failure, not a
            # post-ALLOW fetch_error.
            logger.exception("unexpected error deciding tool call %s", name)
            decision = Decision(outcome="DENY", rule_id="fail_closed", reason="internal error during decision")
            placeholder = DataContext(
                repo=arguments.get("repo", "unknown"), visibility=None,
                resource_type="unknown", action="read",
            )
            deps.audit.record(
                agent=auth.agent_id, user=auth.session.user, intent=auth.session.intent,
                tool=name, context=placeholder, decision=decision,
                latency_ms=(time.perf_counter() - start) * 1000,
            )
            return _deny_result(decision)

    return server


async def _handle_standard_tool(call: ToolCall, deps: GatewayDeps, start: float) -> types.CallToolResult:
    name, arguments = call.tool, call.args
    context = context_mod.resolve_context(name, arguments, deps.policy, deps.classification)
    decision = policy_mod.decide(call, context, deps.policy)

    if decision.outcome == "DENY":
        deps.audit.record(
            agent=call.agent_id, user=call.session.user, intent=call.session.intent,
            tool=name, context=context, decision=decision,
            latency_ms=(time.perf_counter() - start) * 1000,
        )
        return _deny_result(decision)

    fetch = _BACKEND_CALLS.get(name) or _WRITE_CALLS.get(name)
    try:
        result = fetch(deps.backend, arguments)
    except BackendError as exc:
        deps.audit.record(
            agent=call.agent_id, user=call.session.user, intent=call.session.intent,
            tool=name, context=context, decision=decision,
            latency_ms=(time.perf_counter() - start) * 1000, fetch_error=str(exc),
        )
        return _fetch_error_result(str(exc))

    if result is None:  # writes return None on success
        result = {"status": "ok"}

    response, redactions = _success_result(result)
    deps.audit.record(
        agent=call.agent_id, user=call.session.user, intent=call.session.intent,
        tool=name, context=context, decision=decision,
        latency_ms=(time.perf_counter() - start) * 1000, redactions=redactions,
    )
    return response


async def _handle_get_pr_diff(call: ToolCall, deps: GatewayDeps, start: float) -> types.CallToolResult:
    context = context_mod.resolve_pr_diff_provisional(call.tool, call.args, deps.policy)

    decision = policy_mod.decide_phase1(call, context, deps.policy)
    if decision is not None:
        deps.audit.record(
            agent=call.agent_id, user=call.session.user, intent=call.session.intent,
            tool=call.tool, context=context, decision=decision,
            latency_ms=(time.perf_counter() - start) * 1000,
        )
        return _deny_result(decision)

    try:
        paths = deps.backend.list_pr_files(call.args["repo"], call.args["number"])
    except BackendError as exc:
        # The decision isn't finalized yet (tier is still unknown) -- this
        # fails closed, unlike a BackendError on the actual diff fetch
        # below, which happens only after an ALLOW is already recorded.
        decision = Decision(outcome="DENY", rule_id="fail_closed", reason=f"metadata lookup failed: {exc}")
        deps.audit.record(
            agent=call.agent_id, user=call.session.user, intent=call.session.intent,
            tool=call.tool, context=context, decision=decision,
            latency_ms=(time.perf_counter() - start) * 1000,
        )
        return _deny_result(decision)
    context.paths_checked = paths
    context.tier = context_mod.tier_for_paths(paths, deps.classification)

    decision = policy_mod.decide_phase2(call, context, deps.policy)
    if decision.outcome == "DENY":
        deps.audit.record(
            agent=call.agent_id, user=call.session.user, intent=call.session.intent,
            tool=call.tool, context=context, decision=decision,
            latency_ms=(time.perf_counter() - start) * 1000,
        )
        return _deny_result(decision)

    try:
        diff = deps.backend.get_pr_diff(call.args["repo"], call.args["number"])
    except BackendError as exc:
        deps.audit.record(
            agent=call.agent_id, user=call.session.user, intent=call.session.intent,
            tool=call.tool, context=context, decision=decision,
            latency_ms=(time.perf_counter() - start) * 1000, fetch_error=str(exc),
        )
        return _fetch_error_result(str(exc))

    response, redactions = _success_result(diff)
    deps.audit.record(
        agent=call.agent_id, user=call.session.user, intent=call.session.intent,
        tool=call.tool, context=context, decision=decision,
        latency_ms=(time.perf_counter() - start) * 1000, redactions=redactions,
    )
    return response


def _require_admin(request: Request, deps: GatewayDeps) -> JSONResponse | None:
    auth_header = request.headers.get("authorization", "")
    token = auth_header[len("Bearer ") :] if auth_header.startswith("Bearer ") else ""
    if token != deps.admin_token:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return None


def create_app(deps: GatewayDeps) -> Starlette:
    mcp_server = create_mcp_server(deps)
    session_manager = StreamableHTTPSessionManager(app=mcp_server, json_response=True)

    async def mcp_asgi(scope: Scope, receive: Receive, send: Send) -> None:
        await session_manager.handle_request(scope, receive, send)

    async def admin_audit(request: Request) -> JSONResponse:
        unauthorized = _require_admin(request, deps)
        if unauthorized:
            return unauthorized
        rows = deps.audit.query(
            agent=request.query_params.get("agent"),
            user=request.query_params.get("user"),
            outcome=request.query_params.get("outcome"),
            limit=int(request.query_params.get("limit", 200)),
        )
        return JSONResponse({"rows": rows})

    async def admin_agents(request: Request) -> JSONResponse:
        unauthorized = _require_admin(request, deps)
        if unauthorized:
            return unauthorized
        states = deps.audit.list_agent_states()
        for agent_id in deps.policy.agents:
            states.setdefault(agent_id, True)
        return JSONResponse({"agents": states})

    async def admin_toggle(request: Request) -> JSONResponse:
        unauthorized = _require_admin(request, deps)
        if unauthorized:
            return unauthorized
        agent_id = request.path_params["agent_id"]
        body = await request.json()
        deps.audit.set_agent_enabled(agent_id, bool(body.get("enabled", True)))
        return JSONResponse({"agent_id": agent_id, "enabled": bool(body.get("enabled", True))})

    app = Starlette(
        routes=[
            Mount("/mcp", app=mcp_asgi),
            Route("/admin/audit", admin_audit, methods=["GET"]),
            Route("/admin/agents", admin_agents, methods=["GET"]),
            Route("/admin/agents/{agent_id}/toggle", admin_toggle, methods=["POST"]),
        ],
        lifespan=lambda app: session_manager.run(),
    )
    # exposed so tests can drive session_manager.run() directly when the
    # transport they exercise the app with (e.g. httpx.ASGITransport)
    # doesn't itself trigger ASGI lifespan events.
    app.state.session_manager = session_manager
    return app


def build_deps_from_env() -> GatewayDeps:
    from pathlib import Path

    from dotenv import load_dotenv

    from gateway.backends.rest import RestBackend
    from gateway.core.config import load_classification, load_policy

    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env")
    config_dir = root / "config"
    policy = load_policy(config_dir / "policy.yaml")
    classification = load_classification(config_dir / "classification.yaml")
    audit = AuditLog(os.environ.get("AUDIT_DB_PATH", "data/audit.db"))
    token_store = AgentTokenStore.from_env(list(policy.agents))
    kill_switch = KillSwitch(is_enabled=audit.is_agent_enabled)
    signing_key = os.environ["SESSION_SIGNING_KEY"]
    admin_token = os.environ["ADMIN_TOKEN"]
    backend = RestBackend(token=os.environ["GITHUB_TOKEN"])

    return GatewayDeps(
        policy=policy, classification=classification, audit=audit,
        token_store=token_store, kill_switch=kill_switch,
        signing_key=signing_key, admin_token=admin_token, backend=backend,
    )


def main() -> None:
    import uvicorn

    deps = build_deps_from_env()
    app = create_app(deps)
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))


if __name__ == "__main__":
    main()
