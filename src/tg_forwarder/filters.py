from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from telethon.tl.custom.message import Message

from tg_forwarder.config import CONTENT_MATCH_MODE_ANY, FilterConfig
from tg_forwarder.hdhive_resource_resolve import (
    collect_hdhive_resource_urls_from_message,
    extract_hdhive_resource_slug,
    extract_unlock_points_from_hdhive_resource_html,
    effective_hdhive_openapi_base_url,
    load_hdhive_resource_page_text_sync,
    resolve_hdhive_resource_redirect_sync,
    should_attempt_hdhive_openapi_unlock,
    unlock_hdhive_resource_via_open_api_sync,
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
        """HDHive ``/resource/115/{slug}`` 等链接的转发正文替换逻辑（需规则开启「HDHive 专用直链转发」）。

        流程与「先判免费 / 再解锁 / 再取直连」一致：

        1. **判免费**：用 ``HDHIVE_COOKIE`` GET 资源页，解析 ``NEXT_REDIRECT`` → 得到 115cdn 等**直连**则
           **立即返回**（不消耗积分，不调用 OpenAPI）。
        2. **判积分解锁**：页内解析 ``unlock_points`` / ``points_required`` / 中文「需要使用 N 积分解锁」等；
           与站点「自动解锁积分上限」比较；未开启解锁、超上限、未知积分且保守策略 → 跳过解锁分支。
        3. **需解锁时**：``POST`` OpenAPI ``/api/open/resources/unlock``（body: ``{"slug":...}``，头 ``X-API-Key``）。
           成功后 **再次** 用同一 Cookie GET 同一资源 URL，解析 **NEXT_REDIRECT 直连**；若仍无则回退
           接口返回的分享链接。

        失败时返回固定中文提示字符串（不回落为原始消息）。无 resource URL 时返回 ``None``。
        """
        if not bool(getattr(filters, "hdhive_resource_resolve_forward", False)):
            return None
        import os

        values = env_values if env_values is not None else dict(os.environ)
        cookie = (values.get("HDHIVE_COOKIE") or "").strip()
        api_key = (values.get("HDHIVE_API_KEY") or "").strip()
        unlock_enabled = _env_bool(values, "HDHIVE_RESOURCE_UNLOCK_ENABLED")
        unlock_max_points = _parse_hdhive_unlock_max_points(values)
        unlock_inclusive = _env_bool(values, "HDHIVE_RESOURCE_UNLOCK_THRESHOLD_INCLUSIVE", default=True)
        unlock_skip_unknown = _env_bool(values, "HDHIVE_RESOURCE_UNLOCK_SKIP_UNKNOWN_POINTS", default=False)
        if unlock_enabled and not api_key:
            global _HDHIVE_UNLOCK_MISSING_KEY_WARNED
            if not _HDHIVE_UNLOCK_MISSING_KEY_WARNED:
                logging.getLogger("tg_forwarder.hdhive").warning(
                    "已开启 HDHIVE_RESOURCE_UNLOCK_ENABLED，但未配置 HDHIVE_API_KEY；"
                    "将跳过积分解锁，仅尝试 Cookie 解析直链。"
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
        # 资源页 GET：仅当勾选「通过代理访问 HDHive」时走代理
        resolve_proxy = system_proxy if (_env_bool(values, "HDHIVE_CHECKIN_USE_PROXY") and has_system_proxy) else None
        # OpenAPI 解锁：与常见 PHP 脚本一致，只要配置了 TG_PROXY_* 即走系统代理（不必同时勾选 HDHive 走代理）
        unlock_proxy = system_proxy if has_system_proxy else None

        any_permission_error = False
        any_invalid_error = False
        any_unlock_error = False
        any_unlock_skipped_over_points = False
        any_unlock_skipped_unknown_points = False
        openapi_base = effective_hdhive_openapi_base_url(values)
        for url in urls:
            page_text = ""
            # --- ① 免费：Cookie 拉页 → 有 NEXT_REDIRECT 即直连转发 ---
            if cookie:
                ok, redirect_url, err, page_text = resolve_hdhive_resource_redirect_sync(
                    url,
                    cookie_header=cookie,
                    proxy=resolve_proxy,
                    timeout_seconds=30.0,
                )
                if ok and redirect_url.strip():
                    return redirect_url.strip()
                err_text = (err or "").strip()
                if err_text.startswith("HTTP错误:"):
                    code = err_text.replace("HTTP错误:", "").strip().split(" ", 1)[0]
                    if code in {"401", "403"}:
                        any_permission_error = True
                if "未找到重定向URL" in err_text or "HTTP错误: 404" in err_text:
                    any_invalid_error = True

            # --- ② 非直连：解析所需积分，对照上限后决定是否 OpenAPI 解锁 ---
            if unlock_enabled and api_key:
                slug = extract_hdhive_resource_slug(url)
                if not slug:
                    continue
                unlock_points = extract_unlock_points_from_hdhive_resource_html(page_text)
                if unlock_points is None and not (page_text or "").strip():
                    probe_text, _probe_err = load_hdhive_resource_page_text_sync(
                        url,
                        cookie_header=cookie,
                        proxy=resolve_proxy,
                        timeout_seconds=30.0,
                    )
                    unlock_points = extract_unlock_points_from_hdhive_resource_html(probe_text)
                allow_unlock, skip_reason = should_attempt_hdhive_openapi_unlock(
                    unlock_points,
                    max_points_per_item=unlock_max_points,
                    threshold_inclusive=unlock_inclusive,
                    skip_when_points_unknown=unlock_skip_unknown,
                )
                if not allow_unlock:
                    if skip_reason == "over_threshold":
                        any_unlock_skipped_over_points = True
                    elif skip_reason == "unknown_points":
                        any_unlock_skipped_unknown_points = True
                    continue
                # --- ③ POST 解锁 slug；成功后再次 GET 取 NEXT_REDIRECT 直连（优先于接口里的分享 URL）---
                unlock_ok, share_link, unlock_err = unlock_hdhive_resource_via_open_api_sync(
                    slug=slug,
                    api_key=api_key,
                    proxy=unlock_proxy,
                    timeout_seconds=30.0,
                    openapi_base_url=openapi_base,
                )
                if unlock_ok and share_link.strip():
                    if cookie:
                        ok_after, redirect_after, _err_after, _pt_after = (
                            resolve_hdhive_resource_redirect_sync(
                                url,
                                cookie_header=cookie,
                                proxy=resolve_proxy,
                                timeout_seconds=30.0,
                            )
                        )
                        if ok_after and redirect_after.strip():
                            return redirect_after.strip()
                    return share_link.strip()
                if unlock_err:
                    any_unlock_error = True

        if not cookie and not (unlock_enabled and api_key):
            return "HDHive 链接无权获取"
        if any_permission_error:
            return "HDHive 链接无权获取"
        if any_invalid_error:
            return "链接失效"
        if any_unlock_skipped_over_points:
            return "HDHive 积分解锁已跳过（超过设定上限）"
        if any_unlock_skipped_unknown_points:
            return "HDHive 积分解锁已跳过（无法从页面解析所需积分）"
        if any_unlock_error:
            return "HDHive 积分解锁失败"
        return "链接失效"

    # Dedicated HDHive mode:
    # - default: /resource/ links can trigger forwarding without keyword match
    # - optional: require normal rule match (keywords/regex) before triggering
    if bool(getattr(filters, "hdhive_resource_resolve_forward", False)):
        urls = collect_hdhive_resource_urls_from_message(message, max_urls=3)
        require_rule_match = bool(getattr(filters, "hdhive_require_rule_match", False))
        if urls and not require_rule_match:
            result = MessageMatchResult(
                matched=True,
                matched_via=MATCH_VIA_MESSAGE,
                has_media=has_media,
                has_text=has_text,
                missing_content_parts=missing_content_parts,
            )
            result.dispatch_text_override = _maybe_resolve_hdhive_override()
            return result

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
