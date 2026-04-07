from __future__ import annotations

import re

from telethon.tl.custom.message import Message


HTTP_URL_PATTERN = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)
ED2K_URL_PATTERN = re.compile(r"ed2k://[^\s<>'\"]+", re.IGNORECASE)
MAGNET_URL_PATTERN = re.compile(r"magnet:\?[^\s<>'\"]+", re.IGNORECASE)
THUNDER_URL_PATTERN = re.compile(r"thunder://[^\s<>'\"]+", re.IGNORECASE)
SPECIAL_URL_PATTERNS = (
    HTTP_URL_PATTERN,
    ED2K_URL_PATTERN,
    MAGNET_URL_PATTERN,
    THUNDER_URL_PATTERN,
)


def extract_message_text_values(message: Message) -> list[str]:
    values = [
        getattr(message, "raw_text", ""),
        getattr(message, "message", ""),
        getattr(message, "text", ""),
    ]
    return dedupe_non_empty_strings(values)


def extract_message_button_values(message: Message) -> list[str]:
    raw_buttons = getattr(message, "buttons", None) or []
    values: list[str] = []
    for row in raw_buttons:
        buttons = row if isinstance(row, (list, tuple)) else [row]
        for button in buttons:
            values.extend(
                dedupe_non_empty_strings(
                    [
                        getattr(button, "text", ""),
                        getattr(button, "url", ""),
                        getattr(button, "inline_query", ""),
                        getattr(getattr(button, "button", None), "url", ""),
                    ]
                )
            )
    return dedupe_non_empty_strings(values)


def extract_message_keyword_values(message: Message) -> list[str]:
    values = list(extract_message_text_values(message))
    values.extend(extract_message_button_values(message))
    extracted_links: list[str] = []
    for item in values:
        extracted_links.extend(extract_urls_from_text(str(item or "")))
    values.extend(extracted_links)
    return dedupe_non_empty_strings(values)


def extract_urls_from_text(text: str) -> list[str]:
    if not text:
        return []
    raw_text = str(text)
    found: list[tuple[int, str]] = []
    seen: set[str] = set()
    for pattern in SPECIAL_URL_PATTERNS:
        for match in pattern.finditer(raw_text):
            value = match.group(0).strip()
            lowered = value.lower()
            if not value or lowered in seen:
                continue
            seen.add(lowered)
            found.append((match.start(), value))
    found.sort(key=lambda item: item[0])
    return [value for _, value in found]


def dedupe_non_empty_strings(values: list[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
