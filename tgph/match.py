"""按规则关键词 / 正则对 Telegraph 文章 HTML 内容做匹配（与 Telegram 消息正文分离）。"""

from __future__ import annotations

from dataclasses import dataclass

from tgph.content import build_page_search_text


@dataclass(slots=True)
class PageMatchResult:
    matched: bool
    matched_any: list[str]
    matched_all: list[str]
    blocked: list[str]
    missing_all: list[str]


def page_has_positive_filters(filters: object) -> bool:
    return bool(
        _list(getattr(filters, "keywords_any", None))
        or _list(getattr(filters, "keywords_all", None))
        or _list(getattr(filters, "regex_any", None))
        or _list(getattr(filters, "regex_all", None))
    )


def evaluate_page_against_filters(html: str, filters: object) -> PageMatchResult:
    """
    在 Telegraph 页面 HTML 上执行与主规则相同的黑名单 / 任一 / 全部 / 正则逻辑。

    ``filters`` 为 ``tg_forwarder.config.FilterConfig`` 或具备同名字段的简单对象。
    """
    from tg_forwarder.filters import (
        build_keyword_haystacks,
        build_keyword_specs,
        build_regex_haystacks,
        build_regex_specs,
        evaluate_keyword_filters_detailed,
    )

    search_text = build_page_search_text(html)
    if not search_text:
        return PageMatchResult(matched=False)

    case_sensitive = bool(getattr(filters, "case_sensitive", False))
    haystacks = build_keyword_haystacks([search_text], case_sensitive)
    regex_haystacks = build_regex_haystacks([search_text])

    evaluation = evaluate_keyword_filters_detailed(
        haystacks=haystacks,
        regex_haystacks=regex_haystacks,
        keywords_any=build_keyword_specs(_list(getattr(filters, "keywords_any", None)), case_sensitive),
        keywords_all=build_keyword_specs(_list(getattr(filters, "keywords_all", None)), case_sensitive),
        block_keywords=build_keyword_specs(_list(getattr(filters, "block_keywords", None)), case_sensitive),
        regex_any=build_regex_specs(_list(getattr(filters, "regex_any", None)), case_sensitive),
        regex_all=build_regex_specs(_list(getattr(filters, "regex_all", None)), case_sensitive),
        block_regex=build_regex_specs(_list(getattr(filters, "regex_block", None)), case_sensitive),
    )
    return PageMatchResult(
        matched=evaluation.matched,
        matched_any=list(evaluation.matched_any),
        matched_all=list(evaluation.matched_all),
        blocked=list(evaluation.blocked),
        missing_all=list(evaluation.missing_all),
    )


def page_should_forward(html: str, filters: object, *, require_rule_match: bool) -> PageMatchResult:
    """
    - ``require_rule_match=False``：只要页面能解析出正文即视为可继续（具体是否有直链由 resolve 判断）。
    - ``require_rule_match=True``：须用规则中的正向关键词 / 正则命中页面 HTML；且至少配置一类正向条件。
    """
    if not require_rule_match:
        search_text = build_page_search_text(html)
        return PageMatchResult(
            matched=bool(search_text),
            matched_any=[],
            matched_all=[],
            blocked=[],
            missing_all=[],
        )

    if not page_has_positive_filters(filters):
        return PageMatchResult(
            matched=False,
            matched_any=[],
            matched_all=[],
            blocked=[],
            missing_all=[],
        )

    return evaluate_page_against_filters(html, filters)


def _list(value: object) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item or "").strip()]
    return []
