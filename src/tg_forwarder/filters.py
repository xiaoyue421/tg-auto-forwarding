from __future__ import annotations

import re
from dataclasses import dataclass, field

from telethon.tl.custom.message import Message

from tg_forwarder.config import CONTENT_MATCH_MODE_ANY, FilterConfig
from tg_forwarder.hdhive_resource_resolve import (
    collect_hdhive_resource_urls_from_message,
    resolve_hdhive_resource_redirect_sync,
)
from tg_forwarder.message_index import extract_message_keyword_values, extract_message_text_values

MATCH_VIA_NONE = "none"
MATCH_VIA_MESSAGE = "message"


@dataclass(slots=True)
class KeywordEvaluationResult:
    matched: bool
    matched_any: list[str] = field(default_factory=list)
    matched_all: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    missing_all: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MessageMatchResult:
    matched: bool
    matched_via: str = MATCH_VIA_NONE
    matched_any: list[str] = field(default_factory=list)
    matched_all: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    missing_all: list[str] = field(default_factory=list)
    has_media: bool = False
    has_text: bool = False
    missing_content_parts: list[str] = field(default_factory=list)
    dispatch_text_override: str | None = None


async def message_matches(
    message: Message,
    filters: FilterConfig,
    *,
    env_values: dict[str, str] | None = None,
) -> bool:
    return (await explain_message_match(message, filters, env_values=env_values)).matched


def filter_requires_keyword_layer(filters: FilterConfig) -> bool:
    return bool(
        filters.keywords_any
        or filters.keywords_all
        or filters.block_keywords
        or filters.regex_block
        or filters.regex_any
        or filters.regex_all
    )


def has_positive_primary_filters(filters: FilterConfig) -> bool:
    return bool(
        filters.keywords_any
        or filters.keywords_all
        or filters.regex_any
        or filters.regex_all
    )


def collect_blocked_matches(
    haystacks: list[str],
    regex_haystacks: list[str],
    filters: FilterConfig,
) -> list[str]:
    matched_block = [
        raw
        for raw, needle in build_keyword_specs(filters.block_keywords, filters.case_sensitive)
        if any(needle in haystack for haystack in haystacks)
    ]
    matched_block.extend(
        raw
        for raw, pattern in build_regex_specs(filters.regex_block, filters.case_sensitive)
        if any(pattern.search(haystack) for haystack in regex_haystacks)
    )
    return dedupe_strings(matched_block)


async def explain_message_match(
    message: Message,
    filters: FilterConfig,
    *,
    env_values: dict[str, str] | None = None,
) -> MessageMatchResult:
    if getattr(message, "action", None) is not None:
        return MessageMatchResult(matched=False)

    has_media = bool(message.media)
    text_values = extract_message_text_values(message)
    has_text = bool(text_values)
    missing_content_parts = collect_missing_content_parts(
        filters=filters,
        has_media=has_media,
        has_text=has_text,
    )

    selected_conditions: list[bool] = []
    if filters.media_only:
        selected_conditions.append(has_media)
    if filters.text_only:
        selected_conditions.append(has_text)
    if selected_conditions:
        if filters.content_match_mode == CONTENT_MATCH_MODE_ANY:
            if not any(selected_conditions):
                return MessageMatchResult(
                    matched=False,
                    has_media=has_media,
                    has_text=has_text,
                    missing_content_parts=missing_content_parts,
                )
        elif not all(selected_conditions):
            return MessageMatchResult(
                matched=False,
                has_media=has_media,
                has_text=has_text,
                missing_content_parts=missing_content_parts,
            )

    if not filter_requires_keyword_layer(filters):
        return MessageMatchResult(
            matched=True,
            matched_via=MATCH_VIA_MESSAGE,
            has_media=has_media,
            has_text=has_text,
        )

    keyword_values = extract_message_keyword_values(message)
    haystacks = build_keyword_haystacks(keyword_values, filters.case_sensitive)
    regex_haystacks = build_regex_haystacks(keyword_values)

    blocked_list = collect_blocked_matches(haystacks, regex_haystacks, filters)
    if blocked_list:
        return MessageMatchResult(
            matched=False,
            blocked=blocked_list,
            has_media=has_media,
            has_text=has_text,
            missing_content_parts=missing_content_parts,
        )

    def _maybe_resolve_hdhive_override() -> str | None:
        """When enabled and resource URL present:
        - success: return redirect_url
        - failure: return '链接失效' or 'HDHive 链接无权获取' (do not fall back to forwarding original message)
        - no resource url present: return None (normal behavior)
        """
        if not bool(getattr(filters, "hdhive_resource_resolve_forward", False)):
            return None
        import os

        values = env_values if env_values is not None else dict(os.environ)
        cookie = (values.get("HDHIVE_COOKIE") or "").strip()
        urls = collect_hdhive_resource_urls_from_message(message, max_urls=3)
        if not urls:
            return None
        if not cookie:
            # No cookie means we cannot access redirect_url (usually requires logged-in permission).
            return "HDHive 链接无权获取"
        from tg_forwarder.config import parse_proxy_from_env

        proxy = None
        if str(values.get("HDHIVE_CHECKIN_USE_PROXY") or "").strip().lower() in {"1", "true", "yes", "y", "on"}:
            try:
                proxy = parse_proxy_from_env(values)
            except Exception:
                proxy = None

        for url in urls:
            ok, redirect_url, err = resolve_hdhive_resource_redirect_sync(
                url,
                cookie_header=cookie,
                proxy=proxy,
                timeout_seconds=30.0,
            )
            if ok and redirect_url.strip():
                return redirect_url.strip()
            err_text = (err or "").strip()
            # Permission-like errors should be surfaced clearly.
            if err_text.startswith("HTTP错误:"):
                code = err_text.replace("HTTP错误:", "").strip()
                if code in {"401", "403"}:
                    return "HDHive 链接无权获取"
        return "链接失效"

    has_pos = has_positive_primary_filters(filters)
    if not has_pos:
        result = MessageMatchResult(
            matched=True,
            matched_via=MATCH_VIA_MESSAGE,
            has_media=has_media,
            has_text=has_text,
            missing_content_parts=missing_content_parts,
        )
        result.dispatch_text_override = _maybe_resolve_hdhive_override()
        return result

    evaluation = evaluate_keyword_filters_detailed(
        haystacks=haystacks,
        regex_haystacks=regex_haystacks,
        keywords_any=build_keyword_specs(filters.keywords_any, filters.case_sensitive),
        keywords_all=build_keyword_specs(filters.keywords_all, filters.case_sensitive),
        block_keywords=[],
        regex_any=build_regex_specs(filters.regex_any, filters.case_sensitive),
        regex_all=build_regex_specs(filters.regex_all, filters.case_sensitive),
        block_regex=[],
    )
    if evaluation.matched:
        result = build_message_match_result(
            matched=True,
            source=MATCH_VIA_MESSAGE,
            evaluation=evaluation,
            has_media=has_media,
            has_text=has_text,
            missing_content_parts=missing_content_parts,
        )
        result.dispatch_text_override = _maybe_resolve_hdhive_override()
        return result

    return build_message_match_result(
        matched=False,
        source=MATCH_VIA_NONE,
        evaluation=evaluation,
        has_media=has_media,
        has_text=has_text,
        missing_content_parts=missing_content_parts,
    )


def build_message_match_result(
    *,
    matched: bool,
    source: str,
    evaluation: KeywordEvaluationResult,
    has_media: bool = False,
    has_text: bool = False,
    missing_content_parts: list[str] | None = None,
) -> MessageMatchResult:
    return MessageMatchResult(
        matched=matched,
        matched_via=source,
        matched_any=list(evaluation.matched_any),
        matched_all=list(evaluation.matched_all),
        blocked=list(evaluation.blocked),
        missing_all=list(evaluation.missing_all),
        has_media=has_media,
        has_text=has_text,
        missing_content_parts=list(missing_content_parts or []),
    )


def build_match_note(match_result: MessageMatchResult) -> str | None:
    parts: list[str] = []
    details: list[str] = []
    if match_result.matched_via == MATCH_VIA_MESSAGE:
        parts.append("命中来源=主规则")
        details.append("主规则通过")
    if match_result.matched_all:
        parts.append(f"全部条件={','.join(match_result.matched_all)}")
        details.append(f"all:{','.join(match_result.matched_all)}")
    if match_result.matched_any:
        parts.append(f"任一条件={','.join(match_result.matched_any)}")
        details.append(f"any:{','.join(match_result.matched_any)}")
    if match_result.dispatch_text_override:
        parts.append("队列发送=直链")
        details.append("text_override")
    if details:
        parts.append(f"命中详情={';'.join(details)}")
    return " | ".join(parts) or None


def build_mismatch_note(match_result: MessageMatchResult, filters: FilterConfig) -> str | None:
    parts: list[str] = []
    details: list[str] = []
    any_labels = build_positive_primary_any_labels(filters)

    if match_result.blocked:
        parts.append(f"未命中原因=命中黑名单条件:{','.join(match_result.blocked)}")
        details.append(f"blocked:{','.join(match_result.blocked)}")
        parts.append(f"未命中详情={';'.join(details)}")
        return " | ".join(parts)

    content_reason = build_content_mismatch_reason(
        filters=filters,
        missing_parts=match_result.missing_content_parts,
        has_media=match_result.has_media,
        has_text=match_result.has_text,
    )
    if content_reason:
        parts.append(f"未命中原因={content_reason}")
        details.append(f"content:{content_reason}")

    if match_result.missing_all:
        parts.append(f"未命中原因=缺少全部条件:{','.join(match_result.missing_all)}")
        details.append(f"missing_all:{','.join(match_result.missing_all)}")

    if any_labels and not match_result.matched_any:
        parts.append(f"未命中原因=未命中主规则任一条件:{','.join(any_labels)}")
        details.append(f"missing_any:{','.join(any_labels)}")

    if not parts:
        parts.append("未命中原因=未通过当前规则")
        details.append("generic_mismatch")
    if details:
        parts.append(f"未命中详情={';'.join(details)}")
    return " | ".join(parts)


def collect_missing_content_parts(
    *,
    filters: FilterConfig,
    has_media: bool,
    has_text: bool,
) -> list[str]:
    missing_parts: list[str] = []
    if filters.media_only and not has_media:
        missing_parts.append("media")
    if filters.text_only and not has_text:
        missing_parts.append("text")
    return missing_parts


def build_content_mismatch_reason(
    *,
    filters: FilterConfig,
    missing_parts: list[str],
    has_media: bool,
    has_text: bool,
) -> str | None:
    if not missing_parts:
        return None

    if filters.content_match_mode == CONTENT_MATCH_MODE_ANY and filters.media_only and filters.text_only:
        if not has_media and not has_text:
            return "需要媒体或文字其一，但当前既无媒体也无文字"

    labels = {
        "media": "媒体",
        "text": "文字",
    }
    missing_labels = [labels[item] for item in missing_parts if item in labels]
    if not missing_labels:
        return None
    if len(missing_labels) == 1:
        return f"缺少{missing_labels[0]}条件"
    return f"缺少{'和'.join(missing_labels)}条件"


def build_keyword_haystacks(raw_values: list[str], case_sensitive: bool) -> list[str]:
    if case_sensitive:
        return [str(value or "") for value in raw_values]
    return [str(value or "").lower() for value in raw_values]


def build_keyword_specs(raw_values: list[str], case_sensitive: bool) -> list[tuple[str, str]]:
    if case_sensitive:
        return [(value, value) for value in raw_values]
    return [(value, value.lower()) for value in raw_values]


def build_regex_haystacks(raw_values: list[str]) -> list[str]:
    return [str(value or "") for value in raw_values]


def build_regex_specs(raw_values: list[str], case_sensitive: bool) -> list[tuple[str, re.Pattern[str]]]:
    flags = 0 if case_sensitive else re.IGNORECASE
    return [(format_regex_label(value), re.compile(value, flags)) for value in raw_values]


def format_regex_label(pattern: str) -> str:
    return f"正则:{pattern}"


def build_positive_primary_any_labels(filters: FilterConfig) -> list[str]:
    return dedupe_strings(
        list(filters.keywords_any) + [format_regex_label(item) for item in filters.regex_any]
    )


def evaluate_keyword_filters(
    *,
    haystacks: list[str],
    regex_haystacks: list[str],
    keywords_any: list[tuple[str, str]],
    keywords_all: list[tuple[str, str]],
    block_keywords: list[tuple[str, str]],
    regex_any: list[tuple[str, re.Pattern[str]]],
    regex_all: list[tuple[str, re.Pattern[str]]],
    block_regex: list[tuple[str, re.Pattern[str]]],
) -> bool:
    return evaluate_keyword_filters_detailed(
        haystacks=haystacks,
        regex_haystacks=regex_haystacks,
        keywords_any=keywords_any,
        keywords_all=keywords_all,
        block_keywords=block_keywords,
        regex_any=regex_any,
        regex_all=regex_all,
        block_regex=block_regex,
    ).matched


def evaluate_keyword_filters_detailed(
    *,
    haystacks: list[str],
    regex_haystacks: list[str],
    keywords_any: list[tuple[str, str]],
    keywords_all: list[tuple[str, str]],
    block_keywords: list[tuple[str, str]],
    regex_any: list[tuple[str, re.Pattern[str]]],
    regex_all: list[tuple[str, re.Pattern[str]]],
    block_regex: list[tuple[str, re.Pattern[str]]],
) -> KeywordEvaluationResult:
    matched_block = [raw for raw, needle in block_keywords if any(needle in haystack for haystack in haystacks)]
    matched_block.extend(
        raw for raw, pattern in block_regex if any(pattern.search(haystack) for haystack in regex_haystacks)
    )
    if matched_block:
        return KeywordEvaluationResult(matched=False, blocked=dedupe_strings(matched_block))

    matched_all = [raw for raw, needle in keywords_all if any(needle in haystack for haystack in haystacks)]
    matched_all.extend(
        raw for raw, pattern in regex_all if any(pattern.search(haystack) for haystack in regex_haystacks)
    )
    missing_all = [raw for raw, needle in keywords_all if not any(needle in haystack for haystack in haystacks)]
    missing_all.extend(
        raw for raw, pattern in regex_all if not any(pattern.search(haystack) for haystack in regex_haystacks)
    )
    total_all_count = len(keywords_all) + len(regex_all)
    if total_all_count and len(dedupe_strings(matched_all)) == total_all_count:
        return KeywordEvaluationResult(
            matched=True,
            matched_all=dedupe_strings(matched_all),
            missing_all=dedupe_strings(missing_all),
        )

    matched_any = [raw for raw, needle in keywords_any if any(needle in haystack for haystack in haystacks)]
    matched_any.extend(
        raw for raw, pattern in regex_any if any(pattern.search(haystack) for haystack in regex_haystacks)
    )
    if matched_any:
        return KeywordEvaluationResult(
            matched=True,
            matched_any=dedupe_strings(matched_any),
            matched_all=dedupe_strings(matched_all),
            missing_all=dedupe_strings(missing_all),
        )

    if keywords_all or keywords_any or regex_all or regex_any:
        return KeywordEvaluationResult(
            matched=False,
            matched_all=dedupe_strings(matched_all),
            missing_all=dedupe_strings(missing_all),
        )
    return KeywordEvaluationResult(matched=True)


def dedupe_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
