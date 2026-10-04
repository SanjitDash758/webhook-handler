import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from app.utils.redis_client import get_redis
from app.core.config import settings

router = APIRouter()

PIPELINE_CHANNEL = "pipeline:events"


@router.websocket("/ws/pipeline")
async def pipeline_events(websocket: WebSocket, token: str = Query(...)):
    if token != settings.WS_PUBLIC_TOKEN:  # CHANGED — was settings.DASHBOARD_API_TOKEN
        await websocket.close(code=4401)
        return

    await websocket.accept()

    redis = await get_redis()
    pubsub = redis.pubsub()
    await pubsub.subscribe(PIPELINE_CHANNEL)

    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            await websocket.send_text(message["data"])
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe(PIPELINE_CHANNEL)
        await pubsub.close()