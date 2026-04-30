from dataclasses import dataclass, field
from time import time
from typing import Any


@dataclass(frozen=True)
class PendingRequest:
    request_id: str
    trace_id: str
    deadline_ms: int
    future: Any | None = None
    envelope_type: str = ""
    created_at: float = field(default_factory=time)

    def snapshot(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "deadline_ms": self.deadline_ms,
            "envelope_type": self.envelope_type,
            "created_at": self.created_at,
        }


@dataclass
class BridgeRuntimeState:
    server_id: str = ""
    connection_id: str = ""
    ready: bool = False
    pending_requests: dict[str, PendingRequest] = field(default_factory=dict)
    endpoints: dict[str, dict[str, Any]] = field(default_factory=dict)

    def mark_connected(self, *, server_id: str, connection_id: str) -> None:
        if not server_id:
            raise ValueError("server_id must be non-empty")
        if not connection_id:
            raise ValueError("connection_id must be non-empty")
        self.server_id = server_id
        self.connection_id = connection_id
        self.ready = True

    def mark_disconnected(self) -> list[PendingRequest]:
        self.ready = False
        self.connection_id = ""
        pending = list(self.pending_requests.values())
        self.pending_requests.clear()
        return pending

    def add_pending(self, request: PendingRequest) -> None:
        if request.request_id in self.pending_requests:
            raise ValueError(f"duplicate pending request: {request.request_id}")
        self.pending_requests[request.request_id] = request

    def register_endpoint(
        self,
        *,
        server_id: str,
        endpoint_id: str,
        server_name: str,
        source_endpoint: str,
        target_endpoint: str,
        schema_version: str,
        features: dict[str, Any],
        capabilities: dict[str, Any],
    ) -> None:
        if not server_id:
            raise ValueError("server_id must be non-empty")
        if not endpoint_id:
            raise ValueError("endpoint_id must be non-empty")
        self.endpoints[endpoint_id] = {
            "server_id": server_id,
            "endpoint_id": endpoint_id,
            "server_name": server_name,
            "source_endpoint": source_endpoint,
            "target_endpoint": target_endpoint,
            "schema_version": schema_version,
            "features": dict(features),
            "capabilities": dict(capabilities),
        }
