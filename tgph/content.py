"""从 Telegraph 文章 HTML 提取可检索正文（供关键词 / 正则匹配）。"""

from __future__ import annotations

import html as html_lib
import re

from tgph.extract import extract_urls_from_html

_STRIP_TAGS_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def extract_plain_text_from_telegra_html(html: str) -> str:
    """去掉标签后的可见文本（含 article 区域）。"""
    if not (html or "").strip():
        return ""
    chunk = _STRIP_TAGS_RE.sub(" ", html)
    chunk = _TAG_RE.sub(" ", chunk)
    chunk = html_lib.unescape(chunk)
    return _WS_RE.sub(" ", chunk).strip()


def build_page_search_text(html: str) -> str:
    """
    用于规则匹配的检索串：正文 + 文内 URL（便于匹配 ``115cdn``、``4K`` 等出现在链接里的条件）。
    """
    plain = extract_plain_text_from_telegra_html(html)
    urls = extract_urls_from_html(html)
    parts = [plain] if plain else []
    if urls:
        parts.append("\n".join(urls))
    return "\n".join(parts).strip()
