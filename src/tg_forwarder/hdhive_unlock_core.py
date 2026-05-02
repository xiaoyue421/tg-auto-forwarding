"""自动解锁判定与响应解析（与仓库根 ``hdhive/unlock_core.py`` 同逻辑，供已安装包使用）。"""

from __future__ import annotations

from typing import Any, Optional


def is_free_resource(share_data: dict[str, Any]) -> bool:
    return share_data.get("unlock_message") == "免费资源" and share_data.get("unlock_points") is None


def can_unlock_by_points(
    share_data: dict[str, Any],
    allow_paid: bool,
    max_points: Optional[int],
) -> bool:
    if not allow_paid or max_points is None:
        return False
    unlock_points = share_data.get("unlock_points")
    return isinstance(unlock_points, int) and unlock_points <= max_points


def classify_share_for_auto_unlock(
    share_data: dict[str, Any],
    *,
    allow_paid: bool,
    max_points: Optional[int],
) -> tuple[bool, str]:
    """
    返回 ``(是否应调用 unlock, skip_reason)``。
    不应解锁时 ``skip_reason`` 为 ``unknown_or_not_free`` / ``over_threshold`` / ``paid_unlock_disabled``。
    应解锁时 ``skip_reason`` 为空串。
    """
    unlock_message = str(share_data.get("unlock_message") or "").strip()
    unlock_points_raw = share_data.get("unlock_points")
    unlock_points = unlock_points_raw if isinstance(unlock_points_raw, int) else None

    is_free = unlock_message == "免费资源" and unlock_points_raw is None
    if is_free:
        return True, ""
    if allow_paid and max_points is not None and unlock_points is not None and unlock_points <= max_points:
        return True, ""
    if unlock_points is None:
        return False, "unknown_or_not_free"
    if allow_paid and max_points is not None and unlock_points > max_points:
        return False, "over_threshold"
    return False, "paid_unlock_disabled"


def extract_share_link_from_unlock_response(unlock_resp: dict[str, Any]) -> str:
    """从 OpenAPI unlock 响应 JSON 中提取最终可转发链接。"""
    payload_data = unlock_resp.get("data")
    if not isinstance(payload_data, dict):
        payload_data = {}
    share_link = str(payload_data.get("full_url") or payload_data.get("url") or "").strip()
    access_code = str(payload_data.get("access_code") or "").strip()
    if not share_link and str(payload_data.get("url") or "").strip() and access_code:
        base_url = str(payload_data.get("url") or "").strip()
        joiner = "&" if "?" in base_url else "?"
        share_link = f"{base_url}{joiner}password={access_code}"
    return share_link


def preview_decision_from_share_data(
    share_data: dict[str, Any],
    *,
    allow_paid: bool,
    max_points: Optional[int],
) -> tuple[bool, int | None, str]:
    """
    预览用：根据 share 详情判定是否会走 unlock（不发起网络请求）。
    返回 ``(would_unlock, unlock_points, skip_reason)``；``skip_reason`` 为空表示会尝试解锁。
    """
    unlock_points_raw = share_data.get("unlock_points")
    unlock_points = unlock_points_raw if isinstance(unlock_points_raw, int) else None
    would, skip = classify_share_for_auto_unlock(
        share_data,
        allow_paid=allow_paid,
        max_points=max_points,
    )
    return would, unlock_points, skip
