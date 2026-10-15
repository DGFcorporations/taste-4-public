"""Imported active declarations do not apply until local confirmation."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from taste.api import create_app

DIRECTIVE = "Never mention the audit canary phrase zephyr-quill"


def test_import_keeps_declared_state_but_stays_out_of_the_packet(tmp_path: Path):
    client = TestClient(create_app(tmp_path))
    imported = client.post("/standards/import", json={
        "profile_id": "owner",
        "standards": [{
            "directive": DIRECTIVE,
            "polarity": "never",
            "category": "anti",
            "state": "active",
            "rationale": "portable declaration, not a local approval",
            "provenance": "persona",
            "confidence": 0.8,
            "profile_id": "owner",
        }],
    })
    assert imported.status_code == 200
    assert imported.json()["imported"] == 1
    row = client.get("/standards").json()["standards"][0]
    assert row["state"] == "active"
    assert row["variant_meta"]["local_approved"] is False
    packet = client.post("/packet", json={
        "profile_id": "owner", "task_ctx": {"type": "write_doc"}}).json()
    assert DIRECTIVE not in json.dumps(packet)

    confirmed = client.post(
        f"/standards/{row['id']}/confirm",
        json={"rationale": "owner confirms this exact rule"})
    assert confirmed.status_code == 200
    packet = client.post("/packet", json={
        "profile_id": "owner", "task_ctx": {"type": "write_doc"}}).json()
    assert DIRECTIVE in json.dumps(packet)


def test_invalid_state_filter_is_422(tmp_path: Path):
    response = TestClient(create_app(tmp_path)).get(
        "/standards", params={"state": "not-a-state"})
    assert response.status_code == 422
    err = response.json()["error"]
    assert err["code"] == "invalid_state"
    assert err["message"]
