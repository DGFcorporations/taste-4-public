"""Contract v1 surface for taste-4. create_app() takes no required arguments."""

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from taste import models
from taste.score import QUOTED, score_text
from taste.store import STATES, Corpus, HoneBudgetExhausted, applies, normalize, now_iso

DOMAIN_KEYWORDS = {
    "media": ["video", "audio", "mp4", "render", "youtube", "tiktok", "reels", "voice", "sync", "lip", "thumbnail", "kallaway", "hormozi", "elevenlabs", "qwen", "deepgram", "camera", "frame", "cut"],
    "content": ["copy", "text", "headline", "hook", "story", "framing", "script", "editorial", "email", "angle", "jargon", "fluff"],
    "engineering": ["code", "bug", "fix", "feature", "git", "commit", "test", "tdd", "branch", "refactor", "security", "fail-closed", "clean", "architecture"],
    "systems": ["container", "docker", "service", "systemd", "server", "port", "daemon", "proxy", "cloudflare", "postgres", "redis", "bridge", "rclone"],
    "governance": ["gate", "audit", "compliance", "phi", "legal", "secret", "env", "credential", "permission", "sterile-field"],
}


def _score_row(row: dict, query_lower: str, tokens: set[str]) -> float:
    text = (str(row.get("directive", "")) + " " + str(row.get("rationale", ""))).lower()
    score = 0.0
    for t in tokens:
        if len(t) > 3 and t in text:
            score += 3.0
        elif t in text:
            score += 1.0
    for domain, kws in DOMAIN_KEYWORDS.items():
        if any(k in query_lower for k in kws):
            if any(k in text for k in kws):
                score += 5.0
    conf = float(row.get("confidence", 0.5) if row.get("confidence") is not None else 0.5)
    return score * (0.5 + conf)


MAX_BODY = 1_048_576
QUESTIONS = (
    ("lead", "What should lead?"),
    ("slop", "What would be slop?"),
    ("constraints", "Any hard constraints?"),
)
PERMUTATION = {
    "A": "A4", "B": "B5", "C": "C4", "D": "D1", "E": "E2",
    "F": "F3", "G": "G3", "H": "H7", "I": "I1", "J": "J3",
}
MCP_TOOLS = (
    {"name": "taste_packet", "description": "POST /packet"},
    {"name": "taste_score", "description": "POST /score"},
    {"name": "taste_feedback", "description": "POST /feedback"},
    {"name": "taste_recall", "description": "GET /standards"},
)


HONE_LIMIT = 15


def _task_key(ctx: dict) -> str:
    """Segments of one task share a hone budget. Attendance does not reset it."""
    for field in ("task_ref", "ref"):
        value = ctx.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    relevant = {key: value for key, value in ctx.items() if key not in {"segment", "attended"}}
    canonical = json.dumps(relevant, sort_keys=True, separators=(",", ":"), default=str)
    return "task_" + hashlib.sha256(canonical.encode()).hexdigest()


def _log(event: str, **fields: object) -> None:
    logging.getLogger("taste").info(json.dumps({"event": event, **fields}, default=str))


def _configure_logging() -> None:
    logger = logging.getLogger("taste")
    if logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def _validation_message(exc: RequestValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "request failed validation"
    message = str(errors[0].get("msg") or "request failed validation")
    prefix = "Value error, "
    message = message.removeprefix(prefix)
    return message


def _default_root() -> Path:
    override = os.environ.get("TASTE_CORPUS")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[1] / "data" / "corpus"


def _open_gaps(standards: list[dict], task_ctx: dict) -> list[str]:
    active = [row for row in standards if row["state"] == "active"
              and (row.get("variant_meta") or {}).get("local_approved") is not False]
    gaps = []
    if not any(row["polarity"] == "do" for row in active):
        gaps.append("lead")
    if not any(row["polarity"] == "never" for row in active):
        gaps.append("slop")
    if not task_ctx.get("constraints"):
        gaps.append("constraints")
    return gaps


def _parse_doc(text: str, profile_id: str) -> list[dict]:
    found = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith(("-", "*")):
            continue
        body = line.lstrip("-*").strip()
        if not body:
            continue
        head = body.lower()
        if re.match(r"never\b", head):
            polarity, category = "never", "anti"
        elif re.match(r"why\b", head):
            polarity, category = "why", "doctrine"
        else:
            polarity, category = "do", "structure"
        found.append(normalize({
            "directive": body,
            "polarity": polarity,
            "category": category,
            "state": "provisional",
            "provenance": "persona",
            "confidence": 0.4,
            "rationale": "",
            "profile_id": profile_id,
        }))
    return found


def create_app(corpus: Path | Corpus | None = None, token_cap: int = 4000) -> FastAPI:
    _configure_logging()
    if isinstance(corpus, Corpus):
        store = corpus
        if token_cap != 4000:
            store.token_cap = token_cap
    else:
        root = Path(corpus) if corpus is not None else _default_root()
        store = Corpus(root, token_cap=token_cap)

    app = FastAPI(title="taste-4", version="0.1.0")
    app.state.store = store

    @app.middleware("http")
    async def bound_body(request: Request, call_next):
        declared = request.headers.get("content-length")
        if declared:
            try:
                size = int(declared)
            except ValueError:
                return _error(400, "invalid_request", "content-length is not an integer")
            if size > MAX_BODY:
                return _error(413, "payload_too_large", "body exceeds 1 MiB")
        body = await request.body()
        if len(body) > MAX_BODY:
            return _error(413, "payload_too_large", "body exceeds 1 MiB")
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, exc: RequestValidationError):
        return _error(422, "invalid_request", _validation_message(exc))

    @app.exception_handler(Exception)
    async def internal_error(_request: Request, exc: Exception):
        _log("error", error_type=type(exc).__name__)
        return _error(500, "internal", "internal error")

    @app.get("/meta")
    def meta():
        return models.MetaResponse(
            name="taste-4", version="0.1.0", contract="v1", permutation=PERMUTATION,
        )

    @app.get("/check")
    def check(profile_id: str = "owner"):
        all_stds = store.list_standards(profile_id)
        active = [row for row in all_stds if applies(row)]
        cats: dict[str, int] = {}
        pols: dict[str, int] = {}
        for s in active:
            c = s.get("category", "unknown")
            p = s.get("polarity", "unknown")
            cats[c] = cats.get(c, 0) + 1
            pols[p] = pols.get(p, 0) + 1
        return {
            "status": "healthy",
            "name": "taste-4",
            "version": "0.1.0",
            "active_standards": len(active),
            "total_standards": len(all_stds),
            "categories": cats,
            "polarities": pols,
        }

    @app.post("/interview")
    def interview(req: models.InterviewRequest):
        gaps = _open_gaps(store.list_standards(req.profile_id), req.task_ctx)
        questions = [
            models.InterviewQuestion(id=qid, text=text)
            for qid, text in QUESTIONS if qid in gaps
        ]
        resolved = [qid for qid, _text in QUESTIONS if qid not in gaps]
        return models.InterviewResponse(
            questions=questions,
            coverage={"resolved": resolved, "gaps": gaps},
        )

    @app.post("/mirror")
    def mirror(req: models.MirrorRequest):
        attended = req.task_ctx.get("attended") is True
        kind = str(req.task_ctx.get("type") or "the work")
        if attended:
            return models.MirrorResponse(
                mirror=f"Produce {kind} for {req.profile_id}.",
                assumptions=[],
                inferred=False,
                gate="required",
            )
        store.add_review({
            "id": f"rev_{uuid4().hex[:12]}",
            "profile_id": req.profile_id,
            "task_ctx": req.task_ctx,
            "ts": now_iso(),
            "status": "queued",
        })
        return models.MirrorResponse(
            mirror=f"Produce {kind} for {req.profile_id}.",
            assumptions=["Owner is away. The review is queued and this call does not wait."],
            inferred=True,
            gate="queued",
        )

    def _packet(req: models.PacketRequest) -> models.Packet:
        kept, dropped = store.active_slice(req.profile_id)
        if dropped:
            _log("truncation", profile_id=req.profile_id, dropped=dropped, token_cap=store.token_cap)

        query_parts = [str(v) for v in req.task_ctx.values() if isinstance(v, str)]
        for ans in req.answers.values():
            query_parts.append(ans)
        query = " ".join(query_parts).strip()
        query_lower = query.lower()
        tokens = set(re.findall(r"\w+", query_lower))

        ranked_kept = sorted(
            kept,
            key=lambda row: (_score_row(row, query_lower, tokens), float(row.get("confidence", 0.5)), row.get("id", "")),
            reverse=True,
        ) if query else kept

        directives = [
            models.Directive(polarity=row["polarity"], text=row["directive"], standard_id=row["id"])
            for row in ranked_kept
        ]
        for guideline in req.guidelines:
            directives.append(models.Directive(polarity="do", text=guideline))
        return models.Packet(
            profile_id=req.profile_id,
            task_ctx=req.task_ctx,
            directives=directives,
            exemplars=[
                models.Exemplar(text=row["directive"], why=row["rationale"])
                for row in ranked_kept if row["polarity"] == "why" and row["rationale"]
            ],
            constraints=[
                models.Constraint(hard=True, text=row["directive"])
                for row in ranked_kept if row["polarity"] == "never"
            ],
            free_degrees=["wording that no active rule names"],
            variant_meta={
                "truncated": bool(dropped),
                "dropped": dropped,
                "never_over_cap": store.never_over_cap(kept),
                "token_cap": store.token_cap,
                "permutation": PERMUTATION,
            },
        )

    @app.post("/packet")
    def packet(req: models.PacketRequest):
        return _packet(req)


    @app.post("/score")
    def score(req: models.ScoreRequest):
        kept, _dropped = store.active_slice(req.profile_id)
        scores = []
        for candidate in req.candidates:
            graded = score_text(candidate.content or "", kept)
            scores.append(models.CandidateScore(
                ref=candidate.label,
                score=graded["score"],
                breakdown=graded["breakdown"],
                violations=graded["violations"],
            ))
        return models.ScoreResult(scores=scores)

    @app.post("/feedback")
    def feedback(req: models.FeedbackRequest):
        if not req.rationale or not req.rationale.strip():
            return _error(422, "rationale_required", "rationale is required")
        record_id = f"dec_{uuid4().hex[:12]}"
        store.add_decision({
            "id": record_id,
            "profile_id": req.profile_id,
            "task_ctx": req.task_ctx,
            "candidates": [],
            "pick": req.pick,
            "rationale": req.rationale.strip(),
            "agent_role": str(req.task_ctx.get("role", "")),
            "provenance": "owner_pick",
            "ts": now_iso(),
            "rejected": list(req.rejected),
        })
        return models.FeedbackResponse(
            accepted=True, record_id=record_id, extracted={"candidates": 0},
        )

    @app.post("/hone")
    def hone(req: models.HoneRequest):
        """F3 single-shot hone: one candidate, no best-of-n, iterate=false.

        The call is still counted. Fifteen calls per task is the contract
        bound; the sixteenth is refused. There is no generator.

        This variant ships no generator, so the honest repair a driverless
        engine can make is mechanical: strip verbatim quoted phrases that an
        active never-rule forbids, then rescore. Unquoted violations cannot
        be fixed by deletion — they surface through the real score instead
        of being hidden.
        """
        try:
            store.reserve_hone(req.profile_id, _task_key(req.segment_ctx), HONE_LIMIT)
        except HoneBudgetExhausted as exc:
            return _error(429, "hone_budget_exhausted", str(exc))
        draft = req.draft or ""
        kept, _dropped = store.active_slice(req.profile_id)
        candidate = draft
        for row in kept:
            if row["polarity"] != "never":
                continue
            for left, right in QUOTED.findall(str(row["directive"])):
                phrase = left or right
                candidate = re.sub(
                    re.escape(phrase), "", candidate, flags=re.IGNORECASE
                )
        candidate = " ".join(candidate.split())
        graded = score_text(candidate, kept)
        return models.HoneResponse(
            candidates=[
                models.Candidate(label="A", content=candidate, score=graded["score"])
            ],
            pick="A",
            score=graded["score"],
            iterate=False,
        )

    @app.post("/ingest")
    def ingest(req: models.IngestRequest):
        candidates = []
        restatements = []
        for doc in req.docs:
            for row in _parse_doc(str(doc.get("text", "")), req.profile_id):
                stored = store.add_standard_once(row)
                if stored is None:  # re-ingest is idempotent
                    continue
                layer = models.Standard.model_validate(stored)
                candidates.append(layer)
                restatements.append(models.Restatement(
                    layer=layer,
                    implications=[f"Provisional until the owner confirms: {stored['directive']}"],
                ))
        return models.IngestResponse(candidates=candidates, restatements=restatements)

    @app.get("/standards")
    def standards(profile_id: str = "owner", state: str | None = None, category: str | None = None):
        if category == "check":
            return check(profile_id)
        if state is not None and state not in STATES:
            return _error(422, "invalid_state",
                          f"invalid state: {state!r} — expected one of {list(STATES)}")
        rows = store.list_standards(profile_id, state=state)
        return {"standards": rows}

    @app.post("/standards/{sid}/confirm")
    def confirm_standard(sid: str, body: dict | None = None):
        rationale = str((body or {}).get("rationale") or "")
        if not rationale.strip():
            return _error(422, "invalid_request",
                          "activation requires a nonblank rationale")
        if not store.confirm(sid, rationale):
            return _error(404, "not_found", f"no standard {sid}")
        return {"activated": sid}

    @app.post("/standards/import")
    def standards_import(req: models.ImportRequest):
        incoming = [item.model_dump(mode="json") for item in req.standards]
        return store.import_standards(req.profile_id, incoming)

    @app.get("/mcp/tools")
    def mcp_tools():
        return {"tools": list(MCP_TOOLS)}

    @app.post("/mcp/call")
    def mcp_call(body: dict):
        tool = str(body.get("tool") or "")
        arguments = dict(body.get("arguments") or {})
        try:
            if tool == "taste_packet":
                return _packet(models.PacketRequest.model_validate(arguments))
            if tool == "taste_score":
                return score(models.ScoreRequest.model_validate(arguments))
            if tool == "taste_feedback":
                return feedback(models.FeedbackRequest.model_validate(arguments))
            if tool == "taste_recall":
                return standards(
                    profile_id=str(arguments.get("profile_id") or "owner"),
                    state=arguments.get("state"),
                    category=arguments.get("category"),
                )
        except ValidationError:
            return _error(422, "invalid_request", "request failed validation")
        return _error(404, "unknown_tool", "tool is not one of the four contract tools")

    app.state.call_packet = _packet
    app.state.call_score = score
    app.state.call_feedback = feedback
    app.state.call_standards = standards
    app.state.call_check = check
    return app



app = create_app()
