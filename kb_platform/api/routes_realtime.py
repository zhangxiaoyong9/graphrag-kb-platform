"""WebSocket endpoint: per-job realtime step/unit progress."""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/jobs/{job_id}/events")
async def job_events(websocket: WebSocket, job_id: int):
    await websocket.accept()
    hub = getattr(websocket.app.state, "realtime", None)
    if hub is None:
        # Lifespan didn't run (e.g. a non-context-manager test client). Close cleanly.
        await websocket.close()
        return
    await websocket.send_json(hub.subscribe(job_id, websocket))
    try:
        while True:
            await websocket.receive_text()  # keep-alive; client text is ignored
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe(job_id, websocket)
