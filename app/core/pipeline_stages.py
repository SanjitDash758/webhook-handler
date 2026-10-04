"""
Canonical pipeline stage names for the live diagram.

Single source of truth on the Python side — PipelinePublisher,
webhook_service.py, and process_webhook.py all import from here
instead of using raw string literals.
"""

from enum import StrEnum


class PipelineStage(StrEnum):
    RECEIVED = "received"
    QUEUED = "queued"
    PROCESSING = "processing"
    RETRYING = "retrying"
    DEAD_LETTERED = "dead_lettered"
    SUCCESS = "success"