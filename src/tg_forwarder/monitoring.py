from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Any

from tg_forwarder.message_index import extract_message_button_values


WHITESPACE_PATTERN = re.compile(r"\s+")


@dataclass(slots=True)
class ForwardLogContext:
    mode: str = "自动"
    rule_name: str | None = None
    source: str | None = None


def monitor_log(
    logger: logging.Logger,
    level: int,
    action: str,
    *,
    message: object | None = None,
    target: str | int | None = None,
    context: ForwardLogContext | None = None,
    note: str | None = None,
    detect: bool = False,
) -> None:
    ctx = context or ForwardLogContext()
    source = ctx.source or build_message_source(message)
    message_id = getattr(message, "id", "-") if message is not None else "-"
    message_type = build_message_type(message)
    preview = build_message_preview(message)
    full_content = build_message_full_content(message)

    parts = [action, f"模式={ctx.mode}"]
    if ctx.rule_name:
        parts.append(f"规则={ctx.rule_name}")
    if source:
        parts.append(f"来源={source}")
    if target not in (None, ""):
        parts.append(f"目标={target}")
    parts.append(f"消息ID={message_id}")
    parts.append(f"类型={message_type}")
    parts.append(f"内容={preview}")
    if note:
        parts.append(note)

    extra: dict[str, object] = {
        "monitor": True,
        "full_content": full_content,
    }
    if detect:
        extra["detect"] = True
    logger.log(level, " | ".join(parts), extra=extra)


def build_message_preview(message: object | None, limit: int = 120) -> str:
    if message is None:
        return "[无消息内容]"

    raw_text = str(getattr(message, "raw_text", "") or "").strip()
    preview = WHITESPACE_PATTERN.sub(" ", raw_text)
    if not preview and getattr(message, "media", None):
        preview = "[媒体消息]"
    if not preview:
        preview = "[无文本内容]"
    if len(preview) > limit:
        preview = preview[: limit - 3] + "..."
    return preview


def build_message_full_content(message: object | None) -> str | None:
    if message is None:
        return None

    text = str(getattr(message, "raw_text", "") or "").strip()
    has_media = bool(getattr(message, "media", None))
    buttons = extract_message_button_values(message)

    parts: list[str] = []
    if text:
        parts.append("正文:")
        parts.append(text)
    elif has_media:
        parts.append("正文:")
        parts.append("[媒体消息，无正文]")
    else:
        parts.append("正文:")
        parts.append("[无文本内容]")

    if buttons:
        parts.append("")
        parts.append("按钮 / 链接:")
        parts.extend(f"- {item}" for item in buttons)

    content = "\n".join(parts).strip()
    return content or None


def build_message_type(message: object | None) -> str:
    if message is None:
        return "未知"
    has_text = bool(str(getattr(message, "raw_text", "") or "").strip())
    has_media = bool(getattr(message, "media", None))
    if has_text and has_media:
        return "图文/媒体"
    if has_media:
        return "媒体"
    if has_text:
        return "文本"
    return "未知"


def build_message_source(message: object | None, fallback: str = "-") -> str:
    if message is None:
        return fallback

    chat = getattr(message, "chat", None)
    username = getattr(chat, "username", None)
    if username:
        return f"@{username}"

    title = getattr(chat, "title", None)
    if title:
        return str(title)

    sender = getattr(message, "peer_id", None)
    channel_id = getattr(sender, "channel_id", None)
    if channel_id:
        return f"-100{channel_id}"

    chat_id = getattr(message, "chat_id", None)
    if chat_id not in (None, ""):
        return str(chat_id)

    return fallback


def build_targets_note(account_targets: list[Any], bot_targets: list[Any]) -> str:
    return f"账号目标={len(account_targets)} | Bot目标={len(bot_targets)}"
