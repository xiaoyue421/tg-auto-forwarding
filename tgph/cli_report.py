"""CLI / 控制台共用的 Telegraph 页面检测报告结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tgph.content import extract_plain_text_from_telegra_html
from tgph.extract import TelegraphLinks, extract_telegraph_links_from_html
from tgph.dispatch_mode import LinkDispatchMode, infer_link_dispatch_mode
from tgph.match import PageMatchResult
from tgph.resolve import build_dispatch_text_from_html


@dataclass(slots=True)
class TgphCliReport:
    page_url: str
    fetch_ok: bool
    fetch_error: str = ""
    proxy_used: bool = False
    require_rule_match: bool = False
    page_matched: bool = False
    title_snippet: str = ""
    links: TelegraphLinks = field(default_factory=TelegraphLinks)
    match_detail: PageMatchResult | None = None
    dispatch_mode: LinkDispatchMode = "auto"
    dispatch_text: str = ""

    @property
    def success(self) -> bool:
        if not self.fetch_ok:
            return False
        if self.require_rule_match and not self.page_matched:
            return False
        return bool(self.dispatch_text) or self.links.total > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "page_url": self.page_url,
            "fetch_ok": self.fetch_ok,
            "fetch_error": self.fetch_error,
            "proxy_used": self.proxy_used,
            "require_rule_match": self.require_rule_match,
            "page_matched": self.page_matched,
            "title_snippet": self.title_snippet,
            "links": {
                "cdn115": self.links.cdn115,
                "ed2k": self.links.ed2k,
                "magnet": self.links.magnet,
                "cdn115_count": len(self.links.cdn115),
                "ed2k_count": len(self.links.ed2k),
                "magnet_count": len(self.links.magnet),
            },
            "dispatch_mode": self.dispatch_mode,
            "dispatch_text": self.dispatch_text,
            "match_detail": (
                {
                    "matched_any": self.match_detail.matched_any,
                    "matched_all": self.match_detail.matched_all,
                    "blocked": self.match_detail.blocked,
                    "missing_all": self.match_detail.missing_all,
                }
                if self.match_detail
                else None
            ),
        }

    def format_human(self, *, preview_ed2k: int = 5) -> str:
        lines: list[str] = [
            f"页面: {self.page_url}",
            f"拉取: {'成功' + ('（已走代理）' if self.proxy_used else '') if self.fetch_ok else '失败 — ' + self.fetch_error}",
        ]
        if self.title_snippet:
            lines.append(f"标题摘要: {self.title_snippet[:100]}")
        if self.require_rule_match:
            status = "命中" if self.page_matched else "未命中"
            lines.append(f"规则匹配: {status}")
            if self.match_detail and self.match_detail.matched_any:
                lines.append(f"  命中任一: {', '.join(self.match_detail.matched_any)}")
        lines.append(
            f"直链统计: 115cdn {len(self.links.cdn115)} 条 · ed2k {len(self.links.ed2k)} 条 · magnet {len(self.links.magnet)} 条"
        )
        if self.links.cdn115:
            lines.append("115cdn:")
            for i, url in enumerate(self.links.cdn115, 1):
                lines.append(f"  {i}. {url}")
        if self.links.ed2k:
            lines.append("ed2k:")
            show = self.links.ed2k[:preview_ed2k]
            for i, url in enumerate(show, 1):
                lines.append(f"  {i}. {url}")
            rest = len(self.links.ed2k) - len(show)
            if rest > 0:
                lines.append(f"  … 另有 {rest} 条未列出")
        if self.links.magnet:
            lines.append("magnet:")
            for i, url in enumerate(self.links.magnet, 1):
                lines.append(f"  {i}. {url}")
        lines.append(f"转发模式: {self.dispatch_mode}")
        if self.dispatch_text:
            lines.append("--- 转发预览正文 ---")
            lines.append(self.dispatch_text)
        return "\n".join(lines)


def build_cli_report(
    html: str,
    *,
    page_url: str,
    proxy_used: bool,
    require_rule_match: bool,
    match_detail: PageMatchResult | None,
    page_matched: bool,
    dispatch_mode_override: str | None = None,
    filters: object | None = None,
) -> TgphCliReport:
    links = extract_telegraph_links_from_html(html, max_per_kind=500)
    link_mode = infer_link_dispatch_mode(
        filters,
        matched_any=match_detail.matched_any if match_detail else None,
        override=dispatch_mode_override,
    )
    dispatch = (
        build_dispatch_text_from_html(html, link_mode=link_mode) if page_matched else ""
    )
    return TgphCliReport(
        page_url=page_url,
        fetch_ok=True,
        proxy_used=proxy_used,
        require_rule_match=require_rule_match,
        page_matched=page_matched,
        title_snippet=extract_plain_text_from_telegra_html(html)[:120].strip(),
        links=links,
        match_detail=match_detail,
        dispatch_mode=link_mode,
        dispatch_text=dispatch,
    )
