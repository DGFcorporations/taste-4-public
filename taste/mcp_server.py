"""taste-4 MCP server — the four contract tools over stdio.

The HTTP routes stay the source of the response. These tools call those
same callables, so a packet, a score, a decision, and a recall match
POST /packet, POST /score, POST /feedback, and GET /standards.

Run: python -m taste.mcp_server   (env TASTE_CORPUS selects the directory)
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from taste.api import create_app
from taste.models import FeedbackRequest, PacketRequest, ScoreRequest

TOOL_DESCRIPTIONS = {
    "taste_packet": "Compile the taste packet for a task. Same body as POST /packet.",
    "taste_score": "Score candidate output against the flat rules. Same body as POST /score.",
    "taste_feedback": "Log an owner decision. Rationale is required. Same body as POST /feedback.",
    "taste_recall": "Export standards for a profile. Same body as GET /standards.",
}


def _dump(result: object) -> str:
    body = getattr(result, "body", None)
    if isinstance(body, (bytes, bytearray)):
        return body.decode()
    if hasattr(result, "model_dump_json"):
        return result.model_dump_json()
    return json.dumps(result)


def create_tools(app) -> dict[str, Callable]:
    def taste_packet(task_ctx: dict, profile_id: str = "owner",
                     answers: dict | None = None,
                     guidelines: list[str] | None = None) -> str:
        req = PacketRequest(
            profile_id=profile_id, task_ctx=task_ctx,
            answers=answers or {}, guidelines=guidelines or [])
        return _dump(app.state.call_packet(req))

    def taste_score(candidates: list[dict], task_ctx: dict | None = None,
                    profile_id: str = "owner") -> str:
        req = ScoreRequest.model_validate({
            "profile_id": profile_id,
            "task_ctx": task_ctx or {},
            "candidates": candidates,
        })
        return _dump(app.state.call_score(req))

    def taste_feedback(pick: str, rationale: str, task_ctx: dict | None = None,
                       profile_id: str = "owner",
                       rejected: list[str] | None = None) -> str:
        if not rationale or not str(rationale).strip():
            return json.dumps({"error": {
                "code": "rationale_required",
                "message": "rationale is required",
            }})
        req = FeedbackRequest(
            profile_id=profile_id, task_ctx=task_ctx or {},
            pick=pick, rationale=rationale, rejected=rejected or [])
        return _dump(app.state.call_feedback(req))

    def taste_recall(profile_id: str = "owner", state: str | None = None,
                     category: str | None = None) -> str:
        if category == "check":
            return _dump(app.state.call_check(profile_id))
        return _dump(app.state.call_standards(profile_id=profile_id, state=state, category=category))


    return {
        "taste_packet": taste_packet,
        "taste_score": taste_score,
        "taste_feedback": taste_feedback,
        "taste_recall": taste_recall,
    }


def create_server(app) -> MCPServer:
    server = MCPServer("taste-4")
    for name, fn in create_tools(app).items():
        server.tool(description=TOOL_DESCRIPTIONS[name])(fn)
    return server


def main() -> None:
    import os
    root = os.environ.get("TASTE_CORPUS")
    app = create_app(Path(root) if root else None)
    asyncio.run(create_server(app).run_stdio_async())


if __name__ == "__main__":
    main()
