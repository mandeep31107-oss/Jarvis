"""Conversational assistant with full project context."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import current_owner
from ..services import storage
from ..services.ai.base import AIError
from ..services.ai.features import build_facts
from ..services.ai.registry import get_llm
from ..services.events import done, progress

router = APIRouter(prefix="/api/projects/{project_id}/chat", tags=["chat"])

SYSTEM = (
    "You are the design assistant inside a house-design app. You have structured facts about "
    "the user's project. Answer briefly and concretely, quoting real numbers from the facts. "
    "Never invent dimensions, prices, codes or structural advice. If a fact is missing, say so "
    "and ask one short clarifying question. Always remind the user that structural, legal and "
    "safety questions need a licensed professional.")


class ChatBody(BaseModel):
    text: str = Field(max_length=1200)


@router.get("")
def history(project_id: str, owner: str = Depends(current_owner)) -> Dict[str, Any]:
    record = storage.get_project(project_id, owner_id=owner)
    if record is None:
        raise HTTPException(404, "Project not found.")
    return {"messages": storage.list_messages(project_id, owner_id=owner)}


@router.post("")
async def send(project_id: str, body: ChatBody,
               owner: str = Depends(current_owner)) -> Dict[str, Any]:
    record = storage.get_project(project_id, owner_id=owner)
    if record is None:
        raise HTTPException(404, "Project not found.")
    storage.add_message(project_id, "user", body.text, owner_id=owner)
    adapter, note = get_llm()
    progress(project_id, "chat", 30, f"{adapter.name} is reading the project")
    facts = build_facts(record.data)
    try:
        reply = await adapter.complete(task="chat", facts=facts, question=body.text,
                                       system=SYSTEM)
    except AIError as exc:
        raise HTTPException(503 if exc.setup_required else 502,
                           {"message": str(exc), "setup_required": exc.setup_required,
                            "provider": exc.provider})
    storage.add_message(project_id, "assistant", reply, meta={"provider": adapter.name},
                        owner_id=owner)
    done(project_id, "chat", "Reply ready")
    payload = {"reply": reply, "provider": adapter.name,
               "messages": storage.list_messages(project_id, owner_id=owner)}
    if note:
        payload["provider_note"] = note
    return payload


@router.delete("")
def clear(project_id: str, owner: str = Depends(current_owner)) -> Dict[str, Any]:
    record = storage.get_project(project_id, owner_id=owner)
    if record is None:
        raise HTTPException(404, "Project not found.")
    return {"cleared": storage.clear_messages(project_id, owner_id=owner)}
