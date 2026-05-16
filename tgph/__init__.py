"""Telegra.ph (Telegraph) 独立解析：基于文章 HTML 内容匹配规则并提取直链。"""

from __future__ import annotations

from tgph.content import build_page_search_text, extract_plain_text_from_telegra_html
from tgph.extract import (
    collect_telegra_ph_urls_from_message,
    collect_telegra_ph_urls_from_texts,
    extract_direct_share_urls_from_html,
    is_telegra_ph_url,
    normalize_telegra_ph_url,
)
from tgph.fetch import load_telegra_page_html_sync
from tgph.match import PageMatchResult, evaluate_page_against_filters, page_should_forward
from tgph.resolve import TgphResolveResult, build_dispatch_text_from_html, resolve_tgph_dispatch_text

__all__ = [
    "PageMatchResult",
    "TgphResolveResult",
    "build_dispatch_text_from_html",
    "build_page_search_text",
    "collect_telegra_ph_urls_from_message",
    "collect_telegra_ph_urls_from_texts",
    "evaluate_page_against_filters",
    "extract_direct_share_urls_from_html",
    "extract_plain_text_from_telegra_html",
    "is_telegra_ph_url",
    "load_telegra_page_html_sync",
    "normalize_telegra_ph_url",
    "page_should_forward",
    "resolve_tgph_dispatch_text",
]
