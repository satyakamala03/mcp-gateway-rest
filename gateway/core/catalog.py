"""MCP Tool definitions for the eleven canonical tools. Dangerous
operations (merge, approve, close, code search) simply don't exist here --
the catalog itself is the first layer of least privilege.

Each tool's resource type (for filtering discovery) comes from
classification.yaml's tool_resource_types, except read_file, whose
resource type depends on its path argument at call time -- it's included
in an agent's catalog if any of its registered intents grant at least one
file-shaped resource type (code, docs, config, secret).
"""

from __future__ import annotations

from mcp import types

FILE_SHAPED_RESOURCE_TYPES = {"code", "docs", "config", "secret"}

TOOLS: list[types.Tool] = [
    types.Tool(
        name="read_file",
        description="Read a file's contents from a repo. Path rules set the tier.",
        inputSchema={
            "type": "object",
            "properties": {"repo": {"type": "string"}, "path": {"type": "string"}},
            "required": ["repo", "path"],
        },
    ),
    types.Tool(
        name="list_issues",
        description="List issues in a repo by state.",
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "state": {"type": "string", "enum": ["open", "closed"]},
            },
            "required": ["repo", "state"],
        },
    ),
    types.Tool(
        name="get_issue",
        description="Get a single issue by number.",
        inputSchema={
            "type": "object",
            "properties": {"repo": {"type": "string"}, "number": {"type": "integer"}},
            "required": ["repo", "number"],
        },
    ),
    types.Tool(
        name="get_issue_comments",
        description="Get the comments on an issue.",
        inputSchema={
            "type": "object",
            "properties": {"repo": {"type": "string"}, "number": {"type": "integer"}},
            "required": ["repo", "number"],
        },
    ),
    types.Tool(
        name="search_issues",
        description="Search issues within one repo.",
        inputSchema={
            "type": "object",
            "properties": {"repo": {"type": "string"}, "query": {"type": "string"}},
            "required": ["repo", "query"],
        },
    ),
    types.Tool(
        name="list_labels",
        description="List the labels defined on a repo.",
        inputSchema={
            "type": "object",
            "properties": {"repo": {"type": "string"}},
            "required": ["repo"],
        },
    ),
    types.Tool(
        name="add_labels",
        description="Add labels to an issue. Can only add labels; no other field exists.",
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "number": {"type": "integer"},
                "labels": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["repo", "number", "labels"],
        },
    ),
    types.Tool(
        name="get_pr",
        description="Get a pull request's title, description and metadata.",
        inputSchema={
            "type": "object",
            "properties": {"repo": {"type": "string"}, "number": {"type": "integer"}},
            "required": ["repo", "number"],
        },
    ),
    types.Tool(
        name="get_pr_diff",
        description="Get a pull request's diff. Tier is the riskiest changed path.",
        inputSchema={
            "type": "object",
            "properties": {"repo": {"type": "string"}, "number": {"type": "integer"}},
            "required": ["repo", "number"],
        },
    ),
    types.Tool(
        name="list_commits",
        description="List recent commits in a repo.",
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["repo"],
        },
    ),
    types.Tool(
        name="post_review_comment",
        description="Post a comment on a pull request. Comment only; there is no approve tool.",
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "number": {"type": "integer"},
                "body": {"type": "string"},
            },
            "required": ["repo", "number", "body"],
        },
    ),
]

TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}
