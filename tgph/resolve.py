"""Telegra.ph 独立解析：拉取 HTML → 按页面内容匹配规则 → 提取直链作为转发正文。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from telethon.tl.custom.message import Message

from pathlib import Path

from tgph.dispatch_mode import LinkDispatchMode, infer_link_dispatch_mode
from tgph.extract import collect_telegra_ph_urls_from_message, extract_telegraph_links_from_html
from tgph.fetch import load_telegra_page_html_sync
from tgph.match import evaluate_page_against_filters, page_should_forward
from tgph.proxy import resolve_tgph_proxy

LOG = logging.getLogger("tg_forwarder.tgph")


@dataclass(slots=True)
class TgphResolveResult:
    matched: bool
    dispatch_text: str = ""
    error_message: str = ""
    telegra_urls: list[str] = field(default_factory=list)
    direct_share_urls: list[str] = field(default_factory=list)
    pages_fetched: int = 0
    page_matched: bool = False


def _append_ed2k_lines(lines: list[str], ed2k: list[str], *, max_ed2k: int) -> None:
    total = len(ed2k)
    if total <= max_ed2k:
        lines.extend(ed2k)
        return
    if total > 0:
        lines.extend(ed2k[:max_ed2k])
        lines.append(f"（文内另有 {total - max_ed2k} 条 ed2k，请打开 Telegraph 源文查看）")


def build_dispatch_text_from_html(
    html: str,
    *,
    link_mode: LinkDispatchMode = "auto",
    max_cdn: int = 5,
    max_ed2k: int = 8,
    max_magnet: int = 3,
) -> str:
    """
    从文章 HTML 拼转发正文。

    ``link_mode``:
    - ``cdn`` / ``ed2k`` / ``magnet``：只转发对应类型
    - ``auto``：有 115cdn 则只发网盘，否则发 ed2k/magnet
    - ``all``：网盘 + magnet + ed2k（ed2k 过多时截断）
    """
    links = extract_telegraph_links_from_html(html, max_per_kind=500)
    lines: list[str] = []

    if link_mode == "cdn":
        lines.extend(links.cdn115[:max_cdn])
        return "\n".join(lines).strip()
    if link_mode == "ed2k":
        _append_ed2k_lines(lines, links.ed2k, max_ed2k=max_ed2k)
        return "\n".join(lines).strip()
    if link_mode == "magnet":
        lines.extend(links.magnet[:max_magnet])
        return "\n".join(lines).strip()

    if link_mode == "auto" and links.cdn115:
        lines.extend(links.cdn115[:max_cdn])
        return "\n".join(lines).strip()

    lines.extend(links.cdn115[:max_cdn])
    lines.extend(links.magnet[:max_magnet])
    _append_ed2k_lines(lines, links.ed2k, max_ed2k=max_ed2k)
    return "\n".join(lines).strip()


def resolve_tgph_dispatch_text(
    message: Message,
    filters: object,
    *,
    max_pages: int = 2,
    env_config_path: str | Path | None = None,
    env_values: dict[str, str] | None = None,
    proxy: object | None = None,
) -> TgphResolveResult:
    """
    消息中含 telegra.ph 时：抓取文章 HTML，按 ``tgph_require_rule_match`` 与规则关键词/正则
    对**页面内容**判断；命中后提取文内直链（如 115cdn）作为 ``dispatch_text``。
    """
    telegra_urls = collect_telegra_ph_urls_from_message(message, max_urls=max_pages)
    base = TgphResolveResult(matched=False, telegra_urls=list(telegra_urls))
    if not telegra_urls:
        base.error_message = "消息中无 telegra.ph 链接"
        return base

    require_rule_match = bool(getattr(filters, "tgph_require_rule_match", False))
    pages_fetched = 0
    effective_proxy = proxy if proxy is not None else resolve_tgph_proxy(env_config_path, env_values)

    for page_url in telegra_urls:
        html, err = load_telegra_page_html_sync(page_url, proxy=effective_proxy)
        if err:
            if effective_proxy is None and "unreachable" in err.lower():
                LOG.warning(
                    "telegra.ph 拉取失败 %s: %s（未使用代理；请在基础配置填写 TG_PROXY_*）",
                    page_url,
                    err,
                )
            else:
                LOG.warning("telegra.ph 拉取失败 %s: %s", page_url, err)
            continue
        pages_fetched += 1

        page_match = page_should_forward(html, filters, require_rule_match=require_rule_match)
        if not page_match.matched:
            continue

        match_detail = (
            evaluate_page_against_filters(html, filters)
            if require_rule_match
            else None
        )
        link_mode = infer_link_dispatch_mode(
            filters,
            matched_any=match_detail.matched_any if match_detail else page_match.matched_any,
        )
        dispatch = build_dispatch_text_from_html(html, link_mode=link_mode)
        if not dispatch:
            continue

        base.matched = True
        base.page_matched = True
        base.dispatch_text = dispatch
        grouped = extract_telegraph_links_from_html(html, max_per_kind=20)
        base.direct_share_urls = grouped.flat(max_per_kind=20)
        base.pages_fetched = pages_fetched
        return base

    base.pages_fetched = pages_fetched
    if pages_fetched == 0:
        base.error_message = "Telegraph 页面拉取失败"
    elif require_rule_match:
        base.error_message = "Telegraph 页面内容未命中规则条件，或文内无可转发直链"
    else:
        base.error_message = "Telegraph 文章内未找到可转发的网盘直链"
    return base
