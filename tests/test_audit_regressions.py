"""Regression tests for the independent audit of taste-4 (2026-10-12).

Each test failed before its fix. They preserve the variant's axes: flat
files, no embeddings, lexical scoring, confidence-ordered truncation.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from taste.api import create_app
from taste.score import score_text


def _rule(directive, polarity="never", confidence=0.5, rationale="because"):
    return {
        "directive": directive, "polarity": polarity, "category": "anti",
        "rationale": rationale, "provenance": "persona", "state": "active",
        "confidence": confidence, "profile_id": "owner",
    }


def _client(tmp_path: Path, **kw) -> TestClient:
    return TestClient(create_app(tmp_path, **kw), raise_server_exceptions=False)


def _import(client, rows):
    """Import, then confirm. Truncation tests need an approved corpus.

    Unconfirmed import is pinned separately: a declared active state
    must not enter the packet.
    """
    body = client.post("/standards/import",
                       json={"profile_id": "owner", "standards": rows}).json()
    for row in client.get("/standards").json()["standards"]:
        meta = row.get("variant_meta") or {}
        if row["state"] == "active" and meta.get("local_approved") is False:
            client.post(f"/standards/{row['id']}/confirm",
                        json={"rationale": row["rationale"] or "owner confirms"})
    return body


# 1 -- never-rules are guardrails, not relevance picks -------------------
def test_never_rules_survive_truncation(tmp_path):
    client = _client(tmp_path, token_cap=10)
    _import(client, [_rule(f"do thing {i} with care", "do", 0.9) for i in range(3)]
            + [_rule("Never leak secrets", "never", 0.2)])
    pkt = client.post("/packet", json={"profile_id": "owner", "task_ctx": {}}).json()
    assert [c["text"] for c in pkt["constraints"]] == ["Never leak secrets"]
    assert pkt["variant_meta"]["truncated"] is True
    assert pkt["variant_meta"]["dropped"], "dropped do/why rules must be reported"


def test_truncation_still_confidence_ordered_for_do_rules(tmp_path):
    client = _client(tmp_path, token_cap=6)
    _import(client, [_rule("alpha beta gamma", "do", 0.9),
                     _rule("delta epsilon zeta", "do", 0.1)])
    texts = [d["text"] for d in client.post(
        "/packet", json={"profile_id": "owner", "task_ctx": {}}).json()["directives"]]
    assert texts == ["alpha beta gamma", "delta epsilon zeta"][:len(texts)]
    assert "delta epsilon zeta" not in texts[:1]


def test_never_rules_over_cap_are_flagged_not_dropped(tmp_path):
    client = _client(tmp_path, token_cap=3)
    _import(client, [_rule("Never use filler words in any prose ever", "never")])
    pkt = client.post("/packet", json={"profile_id": "owner", "task_ctx": {}}).json()
    assert len(pkt["constraints"]) == 1
    assert pkt["variant_meta"]["never_over_cap"] is True


# 2 -- unquoted never-rules must fire -----------------------------------
def test_unquoted_never_rule_fires_on_plain_text():
    rows = [_rule("Never use filler words")]
    hit = score_text("This draft uses filler words everywhere.", rows)
    assert hit["violations"] and hit["score"] < 0
    assert score_text("Clean prose.", rows)["violations"] == []


# 3 -- apostrophes are not quote marks ----------------------------------
def test_apostrophes_do_not_create_phantom_quotes():
    rows = [_rule("Never say don't, it's fine")]
    assert score_text("the t, it works", rows)["violations"] == []
    assert score_text("well don't, it's fine really", rows)["violations"]


def test_single_quoted_phrase_still_matches():
    rows = [_rule("Never open with 'in today's world'")]
    assert score_text("In today's world, we ship.", rows)["violations"]


# 4 -- MCP bad arguments are 422, never 500 -----------------------------
def test_mcp_invalid_arguments_return_422(tmp_path):
    client = _client(tmp_path)
    for tool, args in (("taste_score", {"candidates": "nope"}),
                       ("taste_packet", {"profile_id": 5}),
                       ("taste_feedback", {})):
        r = client.post("/mcp/call", json={"tool": tool, "arguments": args})
        assert r.status_code == 422, (tool, r.status_code, r.text)
        assert r.json()["error"]["code"] == "invalid_request"


# 5 -- ingest is idempotent --------------------------------------------
def test_reingest_does_not_duplicate(tmp_path):
    client = _client(tmp_path)
    doc = {"profile_id": "owner",
           "docs": [{"id": "p", "text": "- Never pad prose.\n- Lead with the angle.\n"}]}
    client.post("/ingest", json=doc)
    client.post("/ingest", json=doc)
    rows = client.get("/standards").json()["standards"]
    assert len(rows) == 2


# 6 -- polarity needs a word boundary -----------------------------------
def test_polarity_prefix_is_word_bounded(tmp_path):
    client = _client(tmp_path)
    r = client.post("/ingest", json={"profile_id": "owner", "docs": [{
        "id": "p", "text": "- Nevertheless, keep it short.\n- Whyever not lead first.\n"
                           "- Never pad prose.\n- Why: verdict-first saves time.\n"}]}).json()
    got = {c["directive"]: c["polarity"] for c in r["candidates"]}
    assert got["Nevertheless, keep it short."] == "do"
    assert got["Whyever not lead first."] == "do"
    assert got["Never pad prose."] == "never"
    assert got["Why: verdict-first saves time."] == "why"


def test_uds_socket_is_owner_only(tmp_path, monkeypatch):
    """uvicorn has no uds_permissions; serve must bind under a 0o077 umask."""
    import os
    import socket
    import stat

    import uvicorn

    from taste import serve

    captured = {}

    def fake_run(app, uds, **kwargs):
        bound = socket.socket(socket.AF_UNIX)
        bound.bind(uds)
        captured["mode"] = stat.S_IMODE(os.stat(uds).st_mode)
        bound.close()

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setenv("TASTE_SOCK", str(tmp_path / "t.sock"))
    serve.main()
    assert captured["mode"] == 0o600


def test_hone_strips_quoted_never_violations(tmp_path: Path):
    """F3 hone is honest: it repairs the only thing it mechanically can."""
    client = TestClient(create_app(tmp_path))
    _import(client, [{
        "directive": 'Never open with "In today\'s world"',
        "polarity": "never", "category": "anti", "state": "active",
        "rationale": "slop opener", "provenance": "persona",
    }])
    draft = "In today's world, teams ship code."
    body = client.post("/hone", json={
        "profile_id": "owner",
        "segment_ctx": {"type": "write_doc"},
        "draft": draft,
    }).json()
    assert body["iterate"] is False
    assert len(body["candidates"]) == 1
    candidate = body["candidates"][0]
    assert "In today's world" not in candidate["content"]
    assert candidate["score"] == body["score"]
    # A draft with no quoted violation comes back scored, not echoed-claimed.
    clean = client.post("/hone", json={
        "profile_id": "owner", "segment_ctx": {}, "draft": "Teams ship code.",
    }).json()
    assert clean["candidates"][0]["content"] == "Teams ship code."
