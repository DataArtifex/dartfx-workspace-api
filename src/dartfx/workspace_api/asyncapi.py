"""AsyncAPI 3.0.0 specification generator for async job streams and WebSockets."""

from typing import Any

from dartfx.workspace.models import TaskEvent
from workspace_api.__about__ import __version__


def get_asyncapi_schema(server_prefix: str = "") -> dict[str, Any]:
    """Generate a valid AsyncAPI 3.0.0 schema from TaskEvent Pydantic models."""
    prefix = server_prefix.rstrip("/")
    task_event_schema = TaskEvent.model_json_schema(ref_template="#/components/schemas/{model}")
    defs = task_event_schema.pop("$defs", {})

    schemas: dict[str, Any] = {
        "TaskEvent": task_event_schema,
        "JobCommand": {
            "type": "object",
            "required": ["action", "job_id"],
            "properties": {
                "action": {"type": "string", "enum": ["cancel", "ping"]},
                "job_id": {"type": "string"},
            },
        },
    }
    schemas.update(defs)

    return {
        "asyncapi": "3.0.0",
        "info": {
            "title": "Data Artifex Workspace AsyncAPI",
            "version": __version__,
            "description": (
                "Event-driven specification for real-time workspace job telemetry "
                "over WebSockets and Server-Sent Events."
            ),
        },
        "servers": {
            "websocket": {
                "host": "localhost:8000",
                "pathname": f"{prefix}/ws/jobs",
                "protocol": "ws",
                "description": "Bi-directional WebSocket endpoint for live workspace job events and cancellation.",
            },
            "sse": {
                "host": "localhost:8000",
                "pathname": f"{prefix}/jobs/{{job_id}}/events",
                "protocol": "http",
                "description": "Server-Sent Events (text/event-stream) channel per job.",
            },
        },
        "channels": {
            "jobEventsStream": {
                "address": f"{prefix}/jobs/{{job_id}}/events",
                "messages": {
                    "taskEvent": {"$ref": "#/components/messages/TaskEventMessage"},
                },
                "parameters": {
                    "job_id": {"description": "UUID of the asynchronous background job."},
                },
            },
            "jobsWebSocket": {
                "address": f"{prefix}/ws/jobs",
                "messages": {
                    "taskEvent": {"$ref": "#/components/messages/TaskEventMessage"},
                    "jobCommand": {"$ref": "#/components/messages/JobCommandMessage"},
                },
            },
        },
        "operations": {
            "receiveTaskEvents": {
                "action": "receive",
                "channel": {"$ref": "#/channels/jobsWebSocket"},
                "messages": [{"$ref": "#/channels/jobsWebSocket/messages/taskEvent"}],
            },
            "sendJobCommand": {
                "action": "send",
                "channel": {"$ref": "#/channels/jobsWebSocket"},
                "messages": [{"$ref": "#/channels/jobsWebSocket/messages/jobCommand"}],
            },
        },
        "components": {
            "messages": {
                "TaskEventMessage": {
                    "name": "TaskEvent",
                    "contentType": "application/json",
                    "payload": {"$ref": "#/components/schemas/TaskEvent"},
                },
                "JobCommandMessage": {
                    "name": "JobCommand",
                    "contentType": "application/json",
                    "payload": {"$ref": "#/components/schemas/JobCommand"},
                },
            },
            "schemas": schemas,
        },
    }
