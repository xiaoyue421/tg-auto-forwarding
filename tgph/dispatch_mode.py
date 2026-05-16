"""根据规则关键词推断 Telegraph 转发应包含的直链类型。"""

from __future__ import annotations

from typing import Literal

LinkDispatchMode = Literal["auto", "cdn", "ed2k", "magnet", "all"]


def _terms(*groups: list[str] | None) -> list[str]:
    out: list[str] = []
    for group in groups:
        if not group:
            continue
        for item in group:
            text = str(item or "").strip()
            if text:
                out.append(text)
    return out


def _classify_term(term: str) -> LinkDispatchMode | None:
    low = term.lower()
    if "ed2k" in low:
        return "ed2k"
    if "magnet" in low:
        return "magnet"
    if "115cdn" in low or "115.com" in low or "115网盘" in low:
        return "cdn"
    return None


def infer_link_dispatch_mode(
    filters: object | None = None,
    *,
    matched_any: list[str] | None = None,
    keywords_any: list[str] | None = None,
    keywords_all: list[str] | None = None,
    override: str | None = None,
) -> LinkDispatchMode:
    """
    推断转发正文应包含的链接类型。

    优先看**实际命中**的关键词（``matched_any``），再看规则配置里的关键词。
    """
    if override:
        mode = str(override).strip().lower()
        if mode in {"auto", "cdn", "ed2k", "magnet", "all"}:
            return mode  # type: ignore[return-value]

    rule_any = keywords_any if keywords_any is not None else _list_attr(filters, "keywords_any")
    rule_all = keywords_all if keywords_all is not None else _list_attr(filters, "keywords_all")

    for term in _terms(matched_any, rule_any, rule_all):
        kind = _classify_term(term)
        if kind:
            return kind
    return "auto"


def _list_attr(filters: object | None, name: str) -> list[str]:
    if filters is None:
        return []
    value = getattr(filters, name, None)
    if not value:
        return []
    if isinstance(value, list):
        return [str(x) for x in value if str(x or "").strip()]
    return []
