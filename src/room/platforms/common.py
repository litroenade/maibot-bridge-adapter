from typing import Any, Literal, Mapping


RoomDeliveryKind = Literal["sdk", "bridge"]


def require_id(value: Any, field_name: str) -> str:
    """读取必须存在的 endpoint ID。

    Args:
        value: 原始配置值。
        field_name: 报错时显示的字段名。

    Returns:
        str: 去除空白后的 ID。
    """
    normalized = optional_id(value, field_name)
    if not normalized:
        raise ValueError(f"{field_name} must be a non-empty string")
    return normalized


def optional_id(value: Any, field_name: str) -> str:
    """读取可选 endpoint ID。

    Args:
        value: 原始配置值。
        field_name: 报错时显示的字段名。

    Returns:
        str: 空值返回空字符串，数字 ID 会转为字符串。
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a string")
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value.strip()


def reject_present(raw_member: Mapping[str, Any], field_name: str) -> None:
    """拒绝旧字段或不属于该平台的字段。

    Args:
        raw_member: 单个 room member 的原始配置。
        field_name: 不允许出现的字段名。
    """
    if raw_member.get(field_name) is not None:
        optional_id(raw_member.get(field_name), field_name)
        raise ValueError(f"{field_name} is not supported in room member config; use channel_id")


def coerce_id(value: Any) -> str:
    """把路由来源中的 ID 宽松转为字符串"""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
    if isinstance(value, str):
        return value.strip()
    return ""


def base_payload(*, room_id: str, text: str, source_member_id: str) -> dict[str, Any]:
    """构造各平台共享的 room 出站 payload"""
    return {
        "text": text,
        "room_id": room_id,
        "source_member_id": source_member_id,
    }
