"""Standalone FastAPI service for knowledge store/search endpoints.

Run from `backend/`:
- `uvicorn knowledge.service:app --host 127.0.0.1 --port 5100`
- `python -m knowledge.service`
"""

from __future__ import annotations

import os
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from . import capture, rating, semantic, store

app = FastAPI(title="Knowledge Service", version="1.0")


def _safe_pid(pid: str | None) -> str:
    value = (pid or "default").strip()
    return value or "default"


def _pid_or_default(
    pid: str | None = None,
    x_profile_id: str | None = None,
) -> str:
    return _safe_pid(pid or x_profile_id or "default")


class ContactUpdate(BaseModel):
    contact_info: dict[str, Any] | None = None
    summary: str | None = None


class AutofillUpdate(BaseModel):
    autofill: dict[str, Any] | None = None


class ExperienceUpdate(BaseModel):
    experience: list[Any] | None = None


class EducationUpdate(BaseModel):
    education: list[Any] | None = None


class SkillsUpdate(BaseModel):
    skills: dict[str, Any] | None = None


class AnswersUpdate(BaseModel):
    common_answers: dict[str, Any] | None = None


class LearnRequest(BaseModel):
    host: str
    label: str
    value: str


class RateRequest(BaseModel):
    proficiency: int
    evidence: str | None = None


class CaptureProposeRequest(BaseModel):
    raw_text: str
    source: str = ""


class CaptureCommitRequest(BaseModel):
    event_id: int
    edited_delta: dict[str, Any] | None = None


class SearchRequest(BaseModel):
    query_text: str
    k: int = 5
    kind_filter: str | None = None


@app.get("/profile")
def get_profile_header(x_profile_id: str | None = Header(default=None)) -> dict[str, Any]:
    return store.get_profile(_pid_or_default(x_profile_id=x_profile_id))


@app.get("/profile/{pid}")
def get_profile(pid: str) -> dict[str, Any]:
    return store.get_profile(_pid_or_default(pid=pid))


@app.put("/profile/{pid}")
def put_profile(pid: str, payload: dict[str, Any]) -> dict[str, Any]:
    store.save_profile(_pid_or_default(pid=pid), payload or {})
    return {"ok": True}


@app.put("/profile/{pid}/contact")
def put_contact(pid: str, payload: ContactUpdate) -> dict[str, Any]:
    pid = _pid_or_default(pid=pid)
    if payload.contact_info is not None:
        store.merge_section(pid, "contact_info", payload.contact_info)
    if payload.summary is not None:
        store.replace_section(pid, "summary", payload.summary)
    return {"ok": True}


@app.put("/profile/{pid}/autofill")
def put_autofill(pid: str, payload: AutofillUpdate) -> dict[str, Any]:
    if payload.autofill is not None:
        store.merge_section(_pid_or_default(pid=pid), "autofill", payload.autofill)
    return {"ok": True}


@app.put("/profile/{pid}/experience")
def put_experience(pid: str, payload: ExperienceUpdate) -> dict[str, Any]:
    store.replace_section(
        _pid_or_default(pid=pid),
        "experience",
        payload.experience if payload.experience is not None else [],
    )
    return {"ok": True}


@app.put("/profile/{pid}/education")
def put_education(pid: str, payload: EducationUpdate) -> dict[str, Any]:
    store.replace_section(
        _pid_or_default(pid=pid),
        "education",
        payload.education if payload.education is not None else [],
    )
    return {"ok": True}


@app.put("/profile/{pid}/skills")
def put_skills(pid: str, payload: SkillsUpdate) -> dict[str, Any]:
    if payload.skills is not None:
        store.merge_section(_pid_or_default(pid=pid), "skills", payload.skills)
    return {"ok": True}


@app.put("/profile/{pid}/answers")
def put_answers(pid: str, payload: AnswersUpdate) -> dict[str, Any]:
    if payload.common_answers is not None:
        store.merge_section(_pid_or_default(pid=pid), "common_answers", payload.common_answers)
    return {"ok": True}


@app.post("/autofill/learn")
def post_autofill_learn(
    payload: LearnRequest,
    x_profile_id: str | None = Header(default=None),
) -> dict[str, Any]:
    pid = _pid_or_default(x_profile_id=x_profile_id)
    saved = store.set_learned_answer(pid, payload.host, payload.label, payload.value)
    return {"ok": True, "saved": saved}


@app.get("/autofill/learned")
def get_autofill_learned(
    host: str,
    x_profile_id: str | None = Header(default=None),
) -> dict[str, Any]:
    return store.get_learned_answers(_pid_or_default(x_profile_id=x_profile_id), host)


@app.get("/skills/unrated")
def get_skills_unrated(pid: str | None = None, x_profile_id: str | None = Header(default=None)) -> list[dict[str, Any]]:
    return rating.list_unrated(_pid_or_default(pid=pid, x_profile_id=x_profile_id))


@app.post("/skills/{skill_id}/rate")
def post_rate_skill(
    skill_id: int,
    payload: RateRequest,
    pid: str | None = None,
    x_profile_id: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        return rating.set_rating(
            _pid_or_default(pid=pid, x_profile_id=x_profile_id),
            skill_id,
            payload.proficiency,
            payload.evidence,
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/capture/propose")
def post_capture_propose(
    payload: CaptureProposeRequest,
    pid: str | None = None,
    x_profile_id: str | None = Header(default=None),
) -> dict[str, Any]:
    return capture.propose(
        _pid_or_default(pid=pid, x_profile_id=x_profile_id),
        payload.raw_text,
        payload.source,
    )


@app.post("/capture/commit")
def post_capture_commit(
    payload: CaptureCommitRequest,
    pid: str | None = None,
    x_profile_id: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        return capture.commit(
            _pid_or_default(pid=pid, x_profile_id=x_profile_id),
            payload.event_id,
            payload.edited_delta,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/search")
def post_search(
    payload: SearchRequest,
    pid: str | None = None,
    x_profile_id: str | None = Header(default=None),
) -> list[dict[str, Any]]:
    return semantic.search(
        _pid_or_default(pid=pid, x_profile_id=x_profile_id),
        payload.query_text,
        payload.k,
        payload.kind_filter,
    )


def main() -> None:
    port = int(os.getenv("KNOWLEDGE_SERVICE_PORT", "5100"))
    uvicorn.run("knowledge.service:app", host="127.0.0.1", port=port, reload=False)


if __name__ == "__main__":
    main()
