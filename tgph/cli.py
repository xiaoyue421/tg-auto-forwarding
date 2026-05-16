"""命令行：测试 telegra.ph 页面拉取、HTML 规则匹配与直链提取。

用法::

    python -m tgph.cli "https://telegra.ph/..."
    python -m tgph.cli --require-match --keyword 115cdn "https://telegra.ph/..."
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from tgph.cli_report import build_cli_report
from tgph.extract import normalize_telegra_ph_url
from tgph.fetch import load_telegra_page_html_sync
from tgph.match import evaluate_page_against_filters, page_should_forward


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Telegraph 页面 HTML 解析与规则匹配测试")
    parser.add_argument("url", help="telegra.ph 文章 URL")
    parser.add_argument(
        "--keyword",
        action="append",
        default=[],
        dest="keywords_any",
        help="模拟规则「命中任一关键词」（对页面 HTML 匹配，可多次指定）",
    )
    parser.add_argument(
        "--keyword-all",
        action="append",
        default=[],
        dest="keywords_all",
        help="模拟规则「必须全部命中」",
    )
    parser.add_argument(
        "--require-match",
        action="store_true",
        help="启用「仅当页面 HTML 命中关键词时才输出直链」（等同 tgph_require_rule_match）",
    )
    parser.add_argument("--pretty", action="store_true", help="JSON 格式化输出")
    parser.add_argument(
        "--human",
        action="store_true",
        help="仅输出人类可读摘要（不打印 JSON）",
    )
    parser.add_argument(
        "--dispatch-mode",
        choices=("auto", "cdn", "ed2k", "magnet", "all"),
        default=None,
        help="强制转发类型（默认按命中关键词推断：ed2k→ed2k，115cdn→网盘）",
    )
    parser.add_argument(
        "--preview-ed2k",
        type=int,
        default=5,
        metavar="N",
        help="人类可读输出中最多展示几条 ed2k（默认 5）",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="读取 TG_PROXY_* 的 .env 路径（默认当前目录 .env）",
    )
    args = parser.parse_args(argv)

    proxy = None
    if args.env_file.is_file():
        from tgph.proxy import resolve_tgph_proxy

        proxy = resolve_tgph_proxy(args.env_file)

    page_url = normalize_telegra_ph_url(args.url)
    html, err = load_telegra_page_html_sync(page_url, proxy=proxy)
    if err:
        print(err, file=sys.stderr)
        return 1

    filters = SimpleNamespace(
        keywords_any=list(args.keywords_any or []),
        keywords_all=list(args.keywords_all or []),
        block_keywords=[],
        regex_any=[],
        regex_all=[],
        regex_block=[],
        case_sensitive=False,
        tgph_require_rule_match=bool(args.require_match),
    )

    require = bool(args.require_match)
    page_match = page_should_forward(html, filters, require_rule_match=require)
    detail = (
        evaluate_page_against_filters(html, filters)
        if require or args.keywords_any or args.keywords_all
        else None
    )

    report = build_cli_report(
        html,
        page_url=page_url,
        proxy_used=proxy is not None,
        require_rule_match=require,
        match_detail=detail,
        page_matched=page_match.matched,
        filters=filters,
        dispatch_mode_override=args.dispatch_mode,
    )

    if args.human:
        print(report.format_human(preview_ed2k=max(1, args.preview_ed2k)))
    else:
        if args.pretty:
            print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        else:
            print(json.dumps(report.as_dict(), ensure_ascii=False))
        print()
        print(report.format_human(preview_ed2k=max(1, args.preview_ed2k)))

    if report.success:
        return 0
    msg = report.fetch_error or "页面未命中条件或文内无直链"
    print(msg, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
