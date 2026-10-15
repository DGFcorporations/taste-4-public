"""Flat-file corpus. Private files live under data/corpus and stay gitignored."""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


class HoneBudgetExhausted(Exception):
    """A task has used its hone calls. The cap is per task, across segments."""


STATES = ("active", "provisional", "superseded", "retired")
POLARITIES = ("do", "never", "why")
CATEGORIES = ("structure", "aesthetic", "procedural", "doctrine", "anti")
PROVENANCE = ("persona", "decision", "correction", "inference", "amendment")


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def as_iso(value: object) -> str:
    if isinstance(value, datetime):
        current = value if value.tzinfo else value.replace(tzinfo=UTC)
        return current.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if value is None or value == "":
        return now_iso()
    return str(value).replace("+00:00", "Z")


def token_count(text: str) -> int:
    return max(1, len(text.split()))


def applies(row: dict) -> bool:
    """Declared active is not enough. Import leaves local_approved false."""
    meta = row.get("variant_meta") or {}
    return row.get("state") == "active" and meta.get("local_approved") is not False


def identity(row: dict) -> tuple[str, str, str]:
    directive = " ".join(str(row.get("directive", "")).lower().split())
    return (str(row.get("profile_id", "owner")), str(row.get("polarity", "do")), directive)


def normalize(raw: dict, profile_id: str | None = None) -> dict:
    polarity = str(raw.get("polarity") or "do")
    if polarity not in POLARITIES:
        polarity = "do"
    category = str(raw.get("category") or ("anti" if polarity == "never" else "structure"))
    if category not in CATEGORIES:
        category = "structure"
    provenance = str(raw.get("provenance") or "persona")
    if provenance not in PROVENANCE:
        provenance = "persona"
    state = str(raw.get("state") or "provisional")
    if state not in STATES:
        state = "provisional"
    rationale = str(raw.get("rationale") or "")
    if state == "active" and not rationale.strip():
        state = "provisional"
    if provenance in ("amendment", "inference") and state == "active":
        state = "provisional"
    directive = " ".join(str(raw.get("directive", "")).split())
    return {
        "id": str(raw.get("id") or f"std_{uuid4().hex[:12]}"),
        "profile_id": str(raw.get("profile_id") or profile_id or "owner"),
        "category": category,
        "polarity": polarity,
        "directive": directive,
        "rationale": rationale,
        "confidence": float(raw.get("confidence", 0.5)),
        "provenance": provenance,
        "state": state,
        "supersedes": [str(item) for item in (raw.get("supersedes") or [])],
        "created": as_iso(raw.get("created")),
        "updated": as_iso(raw.get("updated") or now_iso()),
        "variant_meta": dict(raw.get("variant_meta") or {}),
    }


def render_rules(rows: list[dict]) -> str:
    lines = ["# corpus/rules.md", ""]
    for section in STATES:
        lines.append(f"## {section}")
        for row in rows:
            if row["state"] == section:
                lines.append(f"- [{row['polarity']}] {row['directive']}")
        lines.append("")
    return "\n".join(lines)


class Corpus:
    def __init__(self, root: Path, token_cap: int = 4000):
        self.root = Path(root)
        self.token_cap = token_cap
        self.root.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _locked(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.root / ".lock", "a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _atomic(self, name: str, text: str) -> None:
        target = self.root / name
        temporary = self.root / f".{name}.tmp"
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, target)

    def _read_unlocked(self) -> list[dict]:
        path = self.root / "standards.json"
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return list(data)

    def _write_unlocked(self, rows: list[dict]) -> None:
        payload = json.dumps(rows, indent=2, sort_keys=True)
        self._atomic("standards.json", payload + "\n")
        self._atomic("rules.md", render_rules(rows))

    def _read_jsonl_unlocked(self, name: str) -> list[dict]:
        path = self.root / name
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def _append_jsonl(self, name: str, row: dict) -> None:
        """True O(1) append under the flock — the log is append-only."""
        line = json.dumps(row, sort_keys=True) + "\n"
        with self._locked(), open(self.root / name, "a", encoding="utf-8") as handle:
            handle.write(line)

    def add_standard(self, raw: dict) -> dict:
        row = normalize(raw)
        with self._locked():
            rows = self._read_unlocked()
            rows.append(row)
            self._write_unlocked(rows)
        return row

    def confirm(self, std_id: str, rationale: str) -> bool:
        """Owner-authority activation — the route the other variants expose
        as POST /standards/{sid}/confirm. Requires a nonblank rationale so
        no layer activates silently; returns False when the id is unknown."""
        if not rationale.strip():
            return False
        with self._locked():
            rows = self._read_unlocked()
            row = next((r for r in rows if r["id"] == std_id), None)
            if row is None:
                return False
            meta = dict(row.get("variant_meta") or {})
            meta["local_approved"] = True
            row["variant_meta"] = meta
            row["state"] = "active"
            row["updated"] = now_iso()
            self._write_unlocked(rows)
        return True

    def list_standards(self, profile_id: str, state: str | None = None) -> list[dict]:
        with self._locked():
            rows = self._read_unlocked()
        chosen = [row for row in rows if row["profile_id"] == profile_id]
        if state:
            chosen = [row for row in chosen if row["state"] == state]
        chosen.sort(key=lambda row: row["id"])
        return chosen

    def import_standards(self, profile_id: str, incoming: list[dict]) -> dict:
        imported = 0
        with self._locked():
            rows = self._read_unlocked()
            seen = {identity(row) for row in rows}
            for raw in incoming:
                row = normalize(raw, profile_id=profile_id)
                if not row["directive"]:
                    continue
                meta = dict(row.get("variant_meta") or {})
                meta["local_approved"] = False
                row["variant_meta"] = meta
                key = identity(row)
                if key in seen:
                    continue
                rows.append(row)
                seen.add(key)
                imported += 1
            self._write_unlocked(rows)
        return {"imported": imported, "skipped": len(incoming) - imported}

    def supersede(self, old_id: str, raw: dict) -> dict:
        with self._locked():
            rows = self._read_unlocked()
            old = next(row for row in rows if row["id"] == old_id)
            new = normalize(raw, profile_id=old["profile_id"])
            new["supersedes"] = list(dict.fromkeys([*new["supersedes"], old_id]))
            old["state"] = "superseded"
            old["updated"] = now_iso()
            rows.append(new)
            self._write_unlocked(rows)
        return new

    def add_decision(self, row: dict) -> None:
        self._append_jsonl("decisions.jsonl", row)

    def add_review(self, row: dict) -> None:
        self._append_jsonl("reviews.jsonl", row)

    def active_slice(self, profile_id: str) -> tuple[list[dict], list[str]]:
        """Active rules for a packet, bounded by the token cap.

        Never-rules are guardrails, not relevance picks: they are always kept
        (``never_over_cap`` reports when they alone exceed the cap). The
        remaining budget goes to do/why rules in confidence order; the tail
        that does not fit is dropped and reported, never silently.
        """
        with self._locked():
            rows = self._read_unlocked()
        active = [row for row in rows
                  if row["profile_id"] == profile_id and applies(row)]
        active.sort(key=lambda row: (-float(row["confidence"]), row["id"]))
        nevers = [row for row in active if row["polarity"] == "never"]
        others = [row for row in active if row["polarity"] != "never"]
        budget = self.token_cap - sum(token_count(row["directive"]) for row in nevers)
        kept_others: list[dict] = []
        cut = len(others)
        for index, row in enumerate(others):
            cost = token_count(row["directive"])
            if cost > budget:
                cut = index
                break
            kept_others.append(row)
            budget -= cost
        dropped = [row["id"] for row in others[cut:]]
        kept = sorted([*nevers, *kept_others], key=lambda row: (-float(row["confidence"]), row["id"]))
        return kept, dropped

    def never_over_cap(self, kept: list[dict]) -> bool:
        return sum(token_count(row["directive"]) for row in kept
                   if row["polarity"] == "never") > self.token_cap

    def add_standard_once(self, raw: dict) -> dict | None:
        """Append a standard unless one with the same identity exists.

        Idempotent: a repeat returns the existing row while it is still
        provisional (so re-ingest keeps returning reviewable candidates) and
        None once the owner has moved it on.
        """
        row = normalize(raw)
        with self._locked():
            rows = self._read_unlocked()
            for existing in rows:
                if identity(existing) == identity(row):
                    return existing if existing["state"] == "provisional" else None
            rows.append(row)
            self._write_unlocked(rows)
        return row

    def reserve_hone(self, profile_id: str, task_key: str, limit: int = 15) -> int:
        """Count one hone call. Raise once this task is past `limit`."""
        if limit < 1:
            raise HoneBudgetExhausted("The task hone budget is exhausted.")
        key = f"{profile_id}\n{task_key}"
        with self._locked():
            path = self.root / "hone-budgets.json"
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            current = int(data.get(key, 0))
            if current >= limit:
                raise HoneBudgetExhausted("The task hone budget is exhausted.")
            data[key] = current + 1
            self._atomic("hone-budgets.json", json.dumps(data, indent=2, sort_keys=True) + "\n")
            return data[key]

    def consolidate(self) -> list[dict]:
        made: list[dict] = []
        with self._locked():
            rows = self._read_unlocked()
            seen = {identity(row) for row in rows}
            for decision in self._read_jsonl_unlocked("decisions.jsonl"):
                rationale = str(decision.get("rationale", "")).strip()
                if not rationale:
                    continue
                row = normalize({
                    "directive": rationale,
                    "polarity": "why",
                    "category": "doctrine",
                    "state": "provisional",
                    "provenance": "decision",
                    "rationale": rationale,
                    "confidence": 0.4,
                    "profile_id": decision.get("profile_id", "owner"),
                    "variant_meta": {"decision_id": decision.get("id", "")},
                })
                key = identity(row)
                if key in seen:
                    continue
                rows.append(row)
                seen.add(key)
                made.append(row)
            if made:
                self._write_unlocked(rows)
        return made
