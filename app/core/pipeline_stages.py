from enum import StrEnum


class PipelineStage(StrEnum):
    RECEIVED = "received"
    QUEUED = "queued"
    PROCESSING = "processing"
    RETRYING = "retrying"
    DEAD_LETTERED = "dead_lettered"
    SUCCESS = "success"