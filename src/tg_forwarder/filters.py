from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from telethon.tl.custom.message import Message

from tg_forwarder.config import CONTENT_MATCH_MODE_ANY, FilterConfig
from tg_forwarder.env_utils import read_env_file
from tg_forwarder.hdhive_resource_resolve import (
    collect_hdhive_resource_urls_from_message,
    extract_hdhive_resource_slug,
    effective_hdhive_openapi_base_url,
    unlock_hdhive_resource_via_cs_rule_sync,
)
from tg_forwarder.message_index import extract_message_keyword_values, extract_message_text_values

MATCH_VIA_NONE = "none"
MATCH_VIA_MESSAGE = "message"
_HDHIVE_UNLOCK_MISSING_KEY_WARNED = False


def _parse_hdhive_unlock_max_points(values: dict[str, str]) -> int:
    raw = (values.get("HDHIVE_RESOURCE_UNLOCK_MAX_POINTS") or "0").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


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
    env_config_path: str | Path | None = None,
) -> bool:
    return (
        await explain_message_match(
            message,
            filters,
            env_values=env_values,
            env_config_path=env_config_path,
        )
    ).matched


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
    env_config_path: str | Path | None = None,
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
        """HDHive ``/resource/115/{slug}`` 等链接的转发正文替换逻辑（需规则开启「HDHive 专用直链转发」）。

        当前为 API-only 流程：仅按 ``auto_unlock.py`` 规则执行自动解锁（先 share，再解锁）。
        不再使用 Cookie 页面解析/NEXT_REDIRECT。

        失败时返回固定中文提示字符串（不回落为原始消息）。无 resource URL 时返回 ``None``。
        """
        if not bool(getattr(filters, "hdhive_resource_resolve_forward", False)):
            return None

        values = env_values if env_values is not None else dict(os.environ)
        if env_config_path is not None:
            cfg = Path(env_config_path)
            if cfg.is_file():
                try:
                    values = read_env_file(cfg)
                except OSError:
                    pass
        api_key = (values.get("HDHIVE_API_KEY") or "").strip()
        unlock_enabled = _env_bool(values, "HDHIVE_RESOURCE_UNLOCK_ENABLED")
        unlock_max_points = _parse_hdhive_unlock_max_points(values)
        unlock_access_token = (values.get("HDHIVE_ACCESS_TOKEN") or "").strip()
        if unlock_enabled and not api_key:
            global _HDHIVE_UNLOCK_MISSING_KEY_WARNED
            if not _HDHIVE_UNLOCK_MISSING_KEY_WARNED:
                logging.getLogger("tg_forwarder.hdhive").warning(
                    "已开启 HDHIVE_RESOURCE_UNLOCK_ENABLED，但未配置 HDHIVE_API_KEY；"
                    "将跳过自动解锁。"
                )
                _HDHIVE_UNLOCK_MISSING_KEY_WARNED = True
        urls = collect_hdhive_resource_urls_from_message(message, max_urls=3)
        if not urls:
            return None
        from tg_forwarder.config import parse_proxy_from_env

        try:
            system_proxy = parse_proxy_from_env(values)
        except Exception:
            system_proxy = None
        has_system_proxy = bool(system_proxy and str(system_proxy.host or "").strip())
        unlock_proxy = system_proxy if has_system_proxy else None

        any_permission_error = False
        any_invalid_error = False
        any_unlock_error = False
        any_unlock_skipped_over_points = False
        any_unlock_skipped_unknown_points = False
        any_unlock_skipped_disabled = False
        openapi_base = effective_hdhive_openapi_base_url(values)
        for url in urls:
            # --- API-only：按 hdhive/auto_unlock.py 规则（先 share 再自动解锁） ---
            if unlock_enabled and api_key:
                slug = extract_hdhive_resource_slug(url)
                if not slug:
                    continue
                # 配置兼容：max_points=0 视作不限制（历史行为），因此给一个很大的阈值。
                paid_max_points = unlock_max_points if unlock_max_points > 0 else 2_147_483_647
                unlock_result = unlock_hdhive_resource_via_cs_rule_sync(
                    slug=slug,
                    api_key=api_key,
                    access_token=unlock_access_token,
                    proxy=unlock_proxy,
                    timeout_seconds=30.0,
                    openapi_base_url=openapi_base,
                    allow_paid=True,
                    max_points=paid_max_points,
                )
                if unlock_result.success and unlock_result.share_link.strip():
                    return unlock_result.share_link.strip()
                if unlock_result.skipped_reason == "over_threshold":
                    any_unlock_skipped_over_points = True
                    continue
                if unlock_result.skipped_reason == "unknown_or_not_free":
                    any_unlock_skipped_unknown_points = True
                    continue
                if unlock_result.skipped_reason == "paid_unlock_disabled":
                    any_unlock_skipped_disabled = True
                    continue
                if unlock_result.error_message:
                    err_text = (unlock_result.error_message or "").strip()
                    if err_text.startswith("HTTP错误:"):
                        code = err_text.replace("HTTP错误:", "").strip().split(" ", 1)[0]
                        if code in {"401", "403"}:
                            any_permission_error = True
                        if code in {"404"}:
                            any_invalid_error = True
                    any_unlock_error = True

        if not (unlock_enabled and api_key):
            return "HDHive 链接无权获取"
        if any_permission_error:
            return "HDHive 链接无权获取"
        if any_invalid_error:
            return "链接失效"
        if any_unlock_skipped_over_points:
            return "HDHive 积分解锁已跳过（超过设定上限）"
        if any_unlock_skipped_unknown_points:
            return "HDHive 积分解锁已跳过（当前资源不满足自动解锁规则）"
        if any_unlock_skipped_disabled:
            return "HDHive 积分解锁已跳过（当前资源不满足自动解锁规则）"
        if any_unlock_error:
            return "HDHive 积分解锁失败"
        return "链接失效"

    # HDHive /resource 直链模式（hdhive_resource_resolve_forward）：
    # - 未勾选「仅命中规则时才转发 HDHive」：出现 resource 链接即尝试转发（仍先经过黑名单等）。
    # - 勾选「仅命中规则」：须与下方「命中任一关键词 / 必须全部命中 / 正则」等主规则一起判定；
    #   若已勾选但未配置任一关键词或正则，则含 HDHive 链接的消息不匹配（避免误走「无正向条件则全放行」）。
    if bool(getattr(filters, "hdhive_resource_resolve_forward", False)):
        urls = collect_hdhive_resource_urls_from_message(message, max_urls=3)
        require_rule_match = bool(getattr(filters, "hdhive_require_rule_match", False))
        if urls:
            if not require_rule_match:
                result = MessageMatchResult(
                    matched=True,
                    matched_via=MATCH_VIA_MESSAGE,
                    has_media=has_media,
                    has_text=has_text,
                    missing_content_parts=missing_content_parts,
                )
                result.dispatch_text_override = _maybe_resolve_hdhive_override()
                return result
            if not has_positive_primary_filters(filters):
                return MessageMatchResult(
                    matched=False,
                    has_media=has_media,
                    has_text=has_text,
                    missing_content_parts=missing_content_parts,
                )

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


def _env_bool(values: dict[str, str], key: str, *, default: bool = False) -> bool:
    raw = values.get(key)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}
