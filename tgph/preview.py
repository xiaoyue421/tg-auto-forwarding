"""Telegraph 页面预览（控制台检测用，不写入队列）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tgph.content import build_page_search_text, extract_plain_text_from_telegra_html
from tgph.extract import extract_telegraph_links_from_html, normalize_telegra_ph_url
from tgph.fetch import load_telegra_page_html_sync
from tgph.match import evaluate_page_against_filters, page_should_forward
from tgph.proxy import resolve_tgph_proxy
from tgph.dispatch_mode import infer_link_dispatch_mode
from tgph.resolve import build_dispatch_text_from_html


@dataclass(slots=True)
class TgphPagePreview:
    success: bool
    page_url: str = ""
    fetch_error: str = ""
    proxy_used: bool = False
    title_snippet: str = ""
    search_text_chars: int = 0
    direct_urls: list[str] = field(default_factory=list)
    ed2k_count: int = 0
    magnet_count: int = 0
    cdn115_count: int = 0
    dispatch_preview: str = ""
    page_matched: bool | None = None
    matched_any: list[str] = field(default_factory=list)
    matched_all: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    rule_name: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "page_url": self.page_url,
            "fetch_error": self.fetch_error,
            "proxy_used": self.proxy_used,
            "title_snippet": self.title_snippet,
            "search_text_chars": self.search_text_chars,
            "direct_urls": self.direct_urls,
            "ed2k_count": self.ed2k_count,
            "magnet_count": self.magnet_count,
            "cdn115_count": self.cdn115_count,
            "dispatch_preview": self.dispatch_preview,
            "page_matched": self.page_matched,
            "matched_any": self.matched_any,
            "matched_all": self.matched_all,
            "blocked": self.blocked,
            "rule_name": self.rule_name,
        }


def preview_telegra_ph_url(
    url: str,
    *,
    env_config_path: str | Path | None = None,
    filters: object | None = None,
    require_rule_match: bool | None = None,
    rule_name: str = "",
    max_list_urls: int = 15,
) -> TgphPagePreview:
    page_url = normalize_telegra_ph_url(url)
    proxy = resolve_tgph_proxy(env_config_path)
    out = TgphPagePreview(
        success=False,
        page_url=page_url,
        proxy_used=proxy is not None,
        rule_name=rule_name,
    )
    if not page_url:
        out.fetch_error = "请填写有效的 telegra.ph 链接"
        return out

    html, err = load_telegra_page_html_sync(page_url, proxy=proxy)
    if err:
        out.fetch_error = err
        return out

    plain = extract_plain_text_from_telegra_html(html)
    out.title_snippet = plain[:120].strip()
    search_text = build_page_search_text(html)
    out.search_text_chars = len(search_text)

    grouped = extract_telegraph_links_from_html(html, max_per_kind=max(50, max_list_urls))
    out.ed2k_count = len(grouped.ed2k)
    out.magnet_count = len(grouped.magnet)
    out.cdn115_count = len(grouped.cdn115)
    out.direct_urls = grouped.flat(max_per_kind=max_list_urls)[:max_list_urls]

    if filters is not None:
        req = (
            bool(require_rule_match)
            if require_rule_match is not None
            else bool(getattr(filters, "tgph_require_rule_match", False))
        )
        page_ok = page_should_forward(html, filters, require_rule_match=req)
        out.page_matched = page_ok.matched
        detail = evaluate_page_against_filters(html, filters)
        out.matched_any = list(detail.matched_any)
        out.matched_all = list(detail.matched_all)
        out.blocked = list(detail.blocked)
        if req and not page_ok.matched:
            out.fetch_error = "页面 HTML 未命中当前规则条件"
            return out

    link_mode = infer_link_dispatch_mode(
        filters,
        matched_any=out.matched_any if filters is not None else None,
    )
    dispatch = build_dispatch_text_from_html(html, link_mode=link_mode)
    out.dispatch_preview = dispatch
    out.success = bool(dispatch) or bool(out.direct_urls)
    if not out.success:
        out.fetch_error = out.fetch_error or "页面内未解析到 ed2k / magnet / 115cdn 等可转发直链"
    return out
