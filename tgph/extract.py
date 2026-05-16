"""从 Telegram 消息或 Telegraph HTML 中提取 telegra.ph / HDHive / 直链 URL。"""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit, urlunsplit

from telethon.tl.custom.message import Message

# telegra.ph 文章路径（含中文 slug）
_TELEGRA_PH_URL_RE = re.compile(
    r"https?://(?:www\.)?telegra\.ph/[^\s<>\"']+",
    re.IGNORECASE,
)
_HDHIVE_RESOURCE_IN_HTML_RE = re.compile(
    r"https?://(?:www\.)?hdhive\.com/resource/[^\s<>\"']+",
    re.IGNORECASE,
)
# Telegraph 文章内网盘直链（ed2k 须匹配到 |/ 结尾，避免多条粘连成一条）
_CDN115_RE = re.compile(r"https?://(?:www\.)?115cdn\.com/s/[^\s<>\"']+", re.IGNORECASE)
_CDN115_ALT_RE = re.compile(r"https?://(?:www\.)?115\.com/s/[^\s<>\"']+", re.IGNORECASE)
# 文件名|大小|32位哈希|/ — 避免匹配 meta 里被 … 截断的假 ed2k
_ED2K_LINK_RE = re.compile(
    r"ed2k://\|file\|[^|\n<>\r\"'…]+?\|\d+\|[0-9A-Fa-f]{32}\|/",
    re.IGNORECASE,
)
_MAGNET_RE = re.compile(r"magnet:\?[^\s<>\"']+", re.IGNORECASE)
_ARTICLE_BODY_RE = re.compile(
    r'<article[^>]*class="tl_article_content"[^>]*>(.*?)</article>',
    re.IGNORECASE | re.DOTALL,
)
_PRE_BLOCK_RE = re.compile(r"<pre[^>]*>(.*?)</pre>", re.IGNORECASE | re.DOTALL)
_DIRECT_SHARE_PATTERNS: tuple[re.Pattern[str], ...] = (
    _CDN115_RE,
    _CDN115_ALT_RE,
    _ED2K_LINK_RE,
    _MAGNET_RE,
)
_HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.IGNORECASE)


def is_telegra_ph_url(url: str) -> bool:
    try:
        host = (urlsplit(str(url or "").strip()).hostname or "").lower()
    except ValueError:
        return False
    return host in {"telegra.ph", "www.telegra.ph"}


def normalize_telegra_ph_url(url: str) -> str:
    text = html_lib.unescape(str(url or "").strip())
    text = text.rstrip(").,;]}>\"'")
    if not text:
        return ""
    parts = urlsplit(text)
    if not parts.scheme:
        text = f"https://{text.lstrip('/')}"
        parts = urlsplit(text)
    if (parts.hostname or "").lower() not in {"telegra.ph", "www.telegra.ph"}:
        return text
    path = unquote(parts.path or "")
    return urlunsplit((parts.scheme or "https", "telegra.ph", path, "", ""))


def _clean_extracted_url(raw: str) -> str:
    text = html_lib.unescape(str(raw or "").strip())
    text = text.replace("\\u0026", "&").rstrip(").,;]}>\"'")
    if text.endswith("#") and "password=" in text:
        text = text[:-1]
    return text


@dataclass(slots=True)
class TelegraphLinks:
    """按类型拆分的 Telegraph 文章内直链。"""

    cdn115: list[str] = field(default_factory=list)
    ed2k: list[str] = field(default_factory=list)
    magnet: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.cdn115) + len(self.ed2k) + len(self.magnet)

    def flat(self, *, max_per_kind: int = 50) -> list[str]:
        out: list[str] = []
        out.extend(self.cdn115[:max_per_kind])
        out.extend(self.ed2k[:max_per_kind])
        out.extend(self.magnet[:max_per_kind])
        return out


def _dedupe_append(bucket: list[str], url: str, seen: set[str]) -> None:
    key = url.lower()
    if key in seen:
        return
    seen.add(key)
    bucket.append(url)


def _is_valid_ed2k(url: str) -> bool:
    return bool(_ED2K_LINK_RE.fullmatch(url))


def _ed2k_search_regions(html: str) -> list[str]:
    """仅在正文 article / pre 块内搜 ed2k，跳过 head/meta 里的截断片段。"""
    regions: list[str] = []
    article = _ARTICLE_BODY_RE.search(html)
    if article:
        regions.append(article.group(1))
    for pre in _PRE_BLOCK_RE.finditer(html):
        block = pre.group(1)
        if block not in regions:
            regions.append(block)
    return regions


def extract_telegraph_links_from_html(
    html: str,
    *,
    max_per_kind: int = 200,
) -> TelegraphLinks:
    """从 HTML 按类型提取直链（ed2k 每条以 |/ 结束，不会粘连）。"""
    out = TelegraphLinks()
    if not (html or "").strip():
        return out
    seen: set[str] = set()

    for match in _CDN115_RE.finditer(html):
        _dedupe_append(out.cdn115, _clean_extracted_url(match.group(0)), seen)
    for match in _CDN115_ALT_RE.finditer(html):
        _dedupe_append(out.cdn115, _clean_extracted_url(match.group(0)), seen)
    ed2k_regions = _ed2k_search_regions(html)
    for region in ed2k_regions:
        for match in _ED2K_LINK_RE.finditer(region):
            url = _clean_extracted_url(match.group(0))
            if _is_valid_ed2k(url):
                _dedupe_append(out.ed2k, url, seen)
    for match in _MAGNET_RE.finditer(html):
        _dedupe_append(out.magnet, _clean_extracted_url(match.group(0)), seen)

    if max_per_kind > 0:
        out.cdn115 = out.cdn115[:max_per_kind]
        out.ed2k = out.ed2k[:max_per_kind]
        out.magnet = out.magnet[:max_per_kind]
    return out


def extract_urls_from_html(html: str) -> list[str]:
    """从 Telegraph HTML 提取 href 与正文中的 URL。"""
    if not (html or "").strip():
        return []
    seen: set[str] = set()
    ordered: list[str] = []

    def _add(raw: str) -> None:
        url = _clean_extracted_url(raw)
        if not url:
            return
        key = url.lower()
        if key in seen:
            return
        seen.add(key)
        ordered.append(url)

    for match in _HREF_RE.finditer(html):
        _add(match.group(1))
    for pattern in (
        _TELEGRA_PH_URL_RE,
        _HDHIVE_RESOURCE_IN_HTML_RE,
        *_DIRECT_SHARE_PATTERNS,
    ):
        for match in pattern.finditer(html):
            _add(match.group(0))
    return ordered


def extract_hdhive_resource_urls_from_html(html: str, *, max_urls: int = 5) -> list[str]:
    if max_urls <= 0:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for url in extract_urls_from_html(html):
        low = url.lower()
        if "hdhive.com" not in low or "/resource/" not in low:
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= max_urls:
            break
    return out


def extract_direct_share_urls_from_html(html: str, *, max_urls: int = 5) -> list[str]:
    """提取文章内嵌的网盘直链（非 hdhive.com/resource），优先 115cdn 再 ed2k。"""
    if max_urls <= 0:
        return []
    grouped = extract_telegraph_links_from_html(html, max_per_kind=max_urls)
    flat = grouped.flat(max_per_kind=max_urls)
    return flat[:max_urls]


def collect_telegra_ph_urls_from_texts(texts: list[str], *, max_urls: int = 3) -> list[str]:
    if max_urls <= 0:
        return []
    seen: set[str] = set()
    ordered: list[str] = []
    for text in texts:
        for match in _TELEGRA_PH_URL_RE.finditer(str(text or "")):
            url = normalize_telegra_ph_url(match.group(0))
            if not url or url.lower() in seen:
                continue
            seen.add(url.lower())
            ordered.append(url)
            if len(ordered) >= max_urls:
                return ordered
    return ordered


def collect_telegra_ph_urls_from_message(message: Message, *, max_urls: int = 3) -> list[str]:
    from tg_forwarder.message_index import extract_message_keyword_values

    return collect_telegra_ph_urls_from_texts(extract_message_keyword_values(message), max_urls=max_urls)
