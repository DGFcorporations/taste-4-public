"""Behavior the contract and the taste-4 blueprint both require."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from taste.api import create_app
from taste.store import Corpus


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path))


def test_wire_models_do_not_reach_outside_the_repo():
    source = Path(__file__).resolve().parents[1].joinpath("taste", "api.py").read_text()
    assert "taste-bench" not in source
    assert "_contract" not in source
    from taste.models import MetaResponse
    assert MetaResponse(name="taste-4", version="0.1.0").contract == "v1"


def test_hone_budget_is_fifteen_calls_per_task(tmp_path: Path):
    client = _client(tmp_path)
    body = {
        "profile_id": "owner",
        "segment_ctx": {"type": "write_doc", "task_ref": "budget-1"},
        "draft": "Lead with the verdict.",
    }
    for index in range(15):
        segment = {**body["segment_ctx"], "segment": f"part-{index % 3}"}
        response = client.post("/hone", json={**body, "segment_ctx": segment})
        assert response.status_code == 200, response.text
        assert response.json()["iterate"] is False
    blocked = client.post("/hone", json=body)
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "hone_budget_exhausted"
    other = client.post("/hone", json={
        "profile_id": "owner",
        "segment_ctx": {"task_ref": "budget-2"},
        "draft": "Lead with the verdict.",
    })
    assert other.status_code == 200


def test_fixed_interview_is_three_questions(tmp_path: Path):
    body = _client(tmp_path).post("/interview", json={"profile_id": "owner", "task_ctx": {}}).json()
    assert [q["text"] for q in body["questions"]] == [
        "What should lead?",
        "What would be slop?",
        "Any hard constraints?",
    ]
    assert body["coverage"]["gaps"]


def test_empty_rationale_is_422_with_error_shape(tmp_path: Path):
    response = _client(tmp_path).post("/feedback", json={
        "profile_id": "owner", "task_ctx": {}, "pick": "A", "rationale": "  ",
    })
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "rationale_required"


def test_ingest_stays_provisional_and_out_of_the_packet(tmp_path: Path):
    client = _client(tmp_path)
    ingested = client.post("/ingest", json={
        "profile_id": "owner",
        "docs": [{"id": "p1", "text": '- Never use the opener "In today\'s world".\n', "format": "md"}],
    }).json()
    assert ingested["candidates"][0]["state"] == "provisional"
    packet = client.post("/packet", json={"profile_id": "owner", "task_ctx": {"type": "write_doc"}}).json()
    texts = [d["text"] for d in packet["directives"]]
    assert all("today" not in text for text in texts)
    assert packet["free_degrees"]


def test_oversized_corpus_truncates_deterministically(tmp_path: Path):
    corpus = Corpus(tmp_path, token_cap=8)
    for index in range(6):
        corpus.add_standard({
            "directive": f"rule number {index} " + ("word " * 20),
            "polarity": "do",
            "state": "active",
            "confidence": 1 - (index / 10),
            "rationale": "kept",
            "provenance": "persona",
            "category": "structure",
            "profile_id": "owner",
        })
    client = TestClient(create_app(tmp_path, token_cap=8))
    first = client.post("/packet", json={"profile_id": "owner", "task_ctx": {}}).json()
    second = client.post("/packet", json={"profile_id": "owner", "task_ctx": {}}).json()
    assert first == second
    assert first["variant_meta"]["truncated"]
    assert len(first["directives"]) < 6


def test_import_does_not_activate_provisional_or_duplicate(tmp_path: Path):
    client = _client(tmp_path)
    standard = {
        "id": "std_fixed",
        "profile_id": "owner",
        "category": "anti",
        "polarity": "never",
        "directive": "No generic openers",
        "rationale": "",
        "confidence": 0.4,
        "provenance": "persona",
        "state": "provisional",
        "supersedes": [],
        "created": "2026-10-12T00:00:00Z",
        "updated": "2026-10-12T00:00:00Z",
    }
    client.post("/standards/import", json={"profile_id": "owner", "standards": [standard, standard]})
    exported = client.get("/standards", params={"profile_id": "owner"}).json()["standards"]
    matches = [row for row in exported if row["directive"] == "No generic openers"]
    assert len(matches) == 1
    assert matches[0]["state"] == "provisional"
    assert matches[0]["provenance"] == "persona"


def test_mcp_tools_match_http(tmp_path: Path):
    client = _client(tmp_path)
    names = {tool["name"] for tool in client.get("/mcp/tools").json()["tools"]}
    assert names == {"taste_packet", "taste_score", "taste_feedback", "taste_recall"}
    via_http = client.post("/packet", json={"profile_id": "owner", "task_ctx": {"type": "x"}}).json()
    via_mcp = client.post("/mcp/call", json={
        "tool": "taste_packet",
        "arguments": {"profile_id": "owner", "task_ctx": {"type": "x"}},
    }).json()
    assert via_mcp == via_http


def test_malformed_json_has_no_traceback(tmp_path: Path):
    response = _client(tmp_path).post(
        "/packet", content=b"{", headers={"content-type": "application/json"},
    )
    assert response.status_code == 422
    assert "Traceback" not in response.text
    assert "error" in response.json()


def test_inferred_mirror_is_queued_and_does_not_block(tmp_path: Path):
    client = _client(tmp_path)
    response = client.post("/mirror", json={
        "profile_id": "owner",
        "task_ctx": {"type": "write_doc", "attended": False},
    })
    assert response.status_code == 200
    body = response.json()
    assert body["inferred"] is True
    assert body["gate"] == "queued"
    reviews = (tmp_path / "reviews.jsonl").read_text().strip().splitlines()
    assert len(reviews) == 1


def test_consolidate_stays_provisional(tmp_path: Path):
    client = _client(tmp_path)
    accepted = client.post("/feedback", json={
        "profile_id": "owner",
        "task_ctx": {"type": "write_doc"},
        "pick": "A",
        "rationale": "Prefer the verdict first because readers skim",
    }).json()
    assert accepted["extracted"]["candidates"] == 0
    made = Corpus(tmp_path).consolidate()
    assert made[0]["state"] == "provisional"
    packet = client.post("/packet", json={"profile_id": "owner", "task_ctx": {}}).json()
    assert all("skim" not in item["text"] for item in packet["directives"])


def test_supersede_keeps_the_retired_row(tmp_path: Path):
    corpus = Corpus(tmp_path)
    old = corpus.add_standard({
        "directive": "lead with the angle",
        "polarity": "do",
        "state": "active",
        "rationale": "older",
        "provenance": "persona",
        "category": "structure",
        "profile_id": "owner",
    })
    corpus.supersede(old["id"], {
        "directive": "lead with the verdict",
        "polarity": "do",
        "state": "active",
        "rationale": "readers skim",
        "provenance": "correction",
        "category": "structure",
        "profile_id": "owner",
    })
    rows = {row["directive"]: row["state"] for row in corpus.list_standards("owner")}
    assert rows["lead with the angle"] == "superseded"
    assert rows["lead with the verdict"] == "active"


def test_no_embedding_module():
    import importlib.util
    assert importlib.util.find_spec("taste.embed") is None


def test_quoted_never_scores_negative(tmp_path: Path):
    Corpus(tmp_path).add_standard({
        "directive": 'Never use the opener "In today\'s world"',
        "polarity": "never",
        "state": "active",
        "rationale": "slop",
        "provenance": "persona",
        "category": "anti",
        "profile_id": "owner",
        "confidence": 1,
    })
    body = TestClient(create_app(tmp_path)).post("/score", json={
        "profile_id": "owner",
        "task_ctx": {},
        "candidates": [{"label": "A", "content": "In today's world, we begin."}],
    }).json()
    assert body["scores"][0]["violations"]
    assert body["scores"][0]["breakdown"]["negative"] > 0


def test_http_over_unix_socket(tmp_path: Path):
    import os
    import subprocess
    import sys
    import time

    import httpx

    sock = tmp_path / "taste.sock"
    env = os.environ.copy()
    env["TASTE_SOCK"] = str(sock)
    env["TASTE_CORPUS"] = str(tmp_path / "corpus")
    proc = subprocess.Popen(
        [sys.executable, "-m", "taste.serve"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.time() + 8
        while time.time() < deadline and not sock.exists():
            if proc.poll() is not None:
                raise AssertionError(proc.stderr.read().decode())
            time.sleep(0.05)
        assert sock.exists()
        transport = httpx.HTTPTransport(uds=str(sock))
        with httpx.Client(transport=transport, base_url="http://taste", timeout=2.0) as client:
            response = client.get("/meta")
        assert response.status_code == 200
        assert response.json()["contract"] == "v1"
        assert "Traceback" not in response.text
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_stdio_mcp_matches_http(tmp_path: Path):
    import asyncio
    import json

    from taste.mcp_server import create_server, create_tools

    app = create_app(tmp_path)
    tools = create_tools(app)
    via_http = _client(tmp_path).post(
        "/packet", json={"profile_id": "owner", "task_ctx": {"type": "x"}},
    ).json()
    via_mcp = json.loads(tools["taste_packet"](task_ctx={"type": "x"}, profile_id="owner"))
    assert via_mcp == via_http
    scored = json.loads(tools["taste_score"](
        candidates=[{"label": "A", "content": "Lead with the verdict."}]))
    assert scored["scores"][0]["ref"] == "A"
    assert "positive" in scored["scores"][0]["breakdown"]
    bad = json.loads(tools["taste_feedback"](pick="A", rationale="   "))
    assert bad["error"]["code"] == "rationale_required"
    recalled = json.loads(tools["taste_recall"](profile_id="owner"))
    assert recalled["standards"] == []
    names = {tool.name for tool in asyncio.run(create_server(app).list_tools())}
    assert names == {"taste_packet", "taste_score", "taste_feedback", "taste_recall"}


def test_rejects_oversized_body(tmp_path: Path):
    response = _client(tmp_path).post("/packet", json={
        "profile_id": "owner",
        "task_ctx": {},
        "padding": "x" * 1_200_000,
    })
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert "Traceback" not in response.text
