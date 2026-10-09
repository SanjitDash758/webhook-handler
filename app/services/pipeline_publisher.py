import json
import time
from typing import Optional
import redis.asyncio as redis
from redis.exceptions import RedisError
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

PIPELINE_CHANNEL = "pipeline:events"


class PipelinePublisher:
    def __init__(self, redis_client: Optional[redis.Redis] = None):
        self._redis = redis_client

    def _client(self) -> redis.Redis:
        if self._redis is None:
            self._redis = redis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
            )
        return self._redis

    async def publish(self, *, receipt_id, provider: str, stage: str) -> None:
        event = {
            "receipt_id": str(receipt_id),
            "provider": provider,
            "stage": stage,
            "ts": time.time(),
        }
        try:
            await self._client().publish(PIPELINE_CHANNEL, json.dumps(event))
        except RedisError as exc:
            logger.warning(
                f"Pipeline event publish failed (stage={stage}, "
                f"receipt_id={receipt_id}): {exc}"
            )