"""WebSocket relay for progress + live project updates."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..auth import auth_enabled, decode_token
from ..services import storage
from ..services.events import bus, project_channel

router = APIRouter(tags=["ws"])


@router.websocket("/ws/projects/{project_id}")
async def project_stream(websocket: WebSocket, project_id: str, token: str = "") -> None:
    owner = "local"
    if auth_enabled():
        payload = decode_token(token) if token else None
        if not payload:
            await websocket.close(code=4401)
            return
        owner = payload.get("sub", "local")
    record = storage.get_project(project_id, owner_id=owner)
    if record is None:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    queue = bus.subscribe(project_channel(project_id))
    try:
        while True:
            event = await queue.get()
            await websocket.send_text(json.dumps(event, default=str))
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        bus.unsubscribe(project_channel(project_id), queue)


@router.websocket("/ws/global")
async def global_stream(websocket: WebSocket, token: str = "") -> None:
    if auth_enabled():
        payload = decode_token(token) if token else None
        if not payload:
            await websocket.close(code=4401)
            return
    await websocket.accept()
    from ..services.events import global_channel
    queue = bus.subscribe(global_channel())
    try:
        while True:
            event = await queue.get()
            await websocket.send_text(json.dumps(event, default=str))
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        from ..services.events import global_channel as gc
        bus.unsubscribe(gc(), queue)
