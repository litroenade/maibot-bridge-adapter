from copy import deepcopy
from time import time
from typing import Any, Iterable, Mapping

SnapshotItem = dict[str, Any]
SnapshotRecord = dict[str, Any]

_VALID_KINDS = frozenset({"tools", "skills", "contexts", "tasks", "sites"})
_DEFAULT_SERVER_ID = "__default__"
_DEFAULT_ENDPOINT_ID = "__default__"
_SENSITIVE_ITEM_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "body",
        "context_value",
        "headers",
        "history",
        "messages",
        "password",
        "prompt",
        "raw_request",
        "raw_response",
        "reference",
        "references",
        "refresh_token",
        "request",
        "response",
        "secret",
        "secrets",
        "token",
        "value",
        "values",
    }
)
_SNAPSHOTS: dict[tuple[str, str, str], SnapshotRecord] = {}


def register_snapshot(
    kind: str,
    items: Iterable[Mapping[str, Any]],
    trace_id: str,
    *,
    server_id: str = _DEFAULT_SERVER_ID,
    endpoint_id: str = _DEFAULT_ENDPOINT_ID,
    snapshot_id: str = "",
    revision: int = 0,
    source: str = "maidbridge",
    visibility: str = "private",
    expires_at: int | None = None,
) -> None:
    _validate_kind(kind)
    normalized_server_id = _normalize_scope(server_id, "server_id")
    normalized_endpoint_id = _normalize_scope(endpoint_id, "endpoint_id")
    if not isinstance(revision, int) or revision < 0:
        raise ValueError("revision must be a non-negative integer")
    _SNAPSHOTS[(kind, normalized_server_id, normalized_endpoint_id)] = {
        "kind": kind,
        "items": [_sanitize_registry_item(item) for item in items],
        "trace_id": trace_id,
        "server_id": normalized_server_id,
        "endpoint_id": normalized_endpoint_id,
        "snapshot_id": snapshot_id or f"{kind}:{normalized_server_id}:{normalized_endpoint_id}:{revision}",
        "revision": revision,
        "generated_at": int(time() * 1000),
        "source": source,
        "visibility": visibility,
        "expires_at": expires_at,
    }


def get_snapshot(
    kind: str,
    *,
    server_id: str = _DEFAULT_SERVER_ID,
    endpoint_id: str = _DEFAULT_ENDPOINT_ID,
) -> SnapshotRecord:
    record = _record_for(kind, server_id=server_id, endpoint_id=endpoint_id)
    return deepcopy(record)


def list_items(
    kind: str,
    *,
    server_id: str = _DEFAULT_SERVER_ID,
    endpoint_id: str = _DEFAULT_ENDPOINT_ID,
) -> list[SnapshotItem]:
    return deepcopy(_record_for(kind, server_id=server_id, endpoint_id=endpoint_id)["items"])


def get_item(
    kind: str,
    key: str,
    *,
    server_id: str = _DEFAULT_SERVER_ID,
    endpoint_id: str | None = None,
) -> SnapshotItem | None:
    for record in _matching_records(kind, server_id=server_id, endpoint_id=endpoint_id):
        for item in record["items"]:
            if item.get("id") == key or item.get("name") == key:
                return deepcopy(item)
    return None


def search_items(
    kind: str,
    text: str,
    *,
    server_id: str = _DEFAULT_SERVER_ID,
    endpoint_id: str | None = None,
) -> list[SnapshotItem]:
    needle = text.casefold()
    matches: list[SnapshotItem] = []
    for record in _matching_records(kind, server_id=server_id, endpoint_id=endpoint_id):
        matches.extend(
            deepcopy(item)
            for item in record["items"]
            if _matches_search_text(item, needle)
        )
    return matches


def _record_for(kind: str, *, server_id: str, endpoint_id: str) -> SnapshotRecord:
    _validate_kind(kind)
    normalized_server_id = _normalize_scope(server_id, "server_id")
    normalized_endpoint_id = _normalize_scope(endpoint_id, "endpoint_id")
    return _SNAPSHOTS.get(
        (kind, normalized_server_id, normalized_endpoint_id),
        {
            "kind": kind,
            "items": [],
            "trace_id": "",
            "server_id": normalized_server_id,
            "endpoint_id": normalized_endpoint_id,
            "snapshot_id": "",
            "revision": 0,
            "generated_at": 0,
            "source": "",
            "visibility": "private",
            "expires_at": None,
        },
    )


def _matching_records(kind: str, *, server_id: str, endpoint_id: str | None) -> list[SnapshotRecord]:
    _validate_kind(kind)
    normalized_server_id = _normalize_scope(server_id, "server_id")
    if endpoint_id is not None:
        return [_record_for(kind, server_id=normalized_server_id, endpoint_id=endpoint_id)]
    return [
        record
        for (record_kind, record_server_id, _), record in _SNAPSHOTS.items()
        if record_kind == kind and record_server_id == normalized_server_id
    ]


def _validate_kind(kind: str) -> None:
    if kind not in _VALID_KINDS:
        allowed = ", ".join(sorted(_VALID_KINDS))
        raise ValueError(f"invalid snapshot kind: {kind!r}; expected one of {allowed}")


def _normalize_scope(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _matches_search_text(item: Mapping[str, Any], needle: str) -> bool:
    return any(
        needle in str(item.get(field, "")).casefold()
        for field in ("id", "name", "description")
    )


def _sanitize_registry_item(item: Mapping[str, Any]) -> SnapshotItem:
    return {
        str(key): _sanitize_registry_value(value)
        for key, value in dict(item).items()
        if str(key).casefold() not in _SENSITIVE_ITEM_KEYS
    }


def _sanitize_registry_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize_registry_value(nested_value)
            for key, nested_value in dict(value).items()
            if str(key).casefold() not in _SENSITIVE_ITEM_KEYS
        }
    if isinstance(value, list):
        return [_sanitize_registry_value(item) for item in value]
    return deepcopy(value)
