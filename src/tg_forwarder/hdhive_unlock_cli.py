"""命令行：按 hdhive/auto_unlock.py 规则自动解锁 HDHive 资源。

使用 .env 中的 ``HDHIVE_API_KEY``、``HDHIVE_BASE_URL``（可选）、``TG_PROXY_*``（若已配置主机则走代理，SOCKS5 + RDNS 等价 PHP 的 SOCKS5_HOSTNAME）。

用法（在项目根目录、已 pip install -e . 或 PYTHONPATH=src）::

    python -m tg_forwarder.hdhive_unlock_cli "https://hdhive.com/resource/115/375f8bd3f37811f0a7c78e06b282dbd4"
    python -m tg_forwarder.hdhive_unlock_cli 375f8bd3f37811f0a7c78e06b282dbd4 --env-file /path/to/.env
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tg_forwarder.config import ConfigError, parse_proxy_from_env
from tg_forwarder.env_utils import read_env_file
from tg_forwarder.hdhive_resource_resolve import (
    effective_hdhive_openapi_base_url,
    extract_hdhive_resource_slug,
    unlock_hdhive_resource_via_cs_rule_sync,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HDHive 自动解锁（先查 share，再按规则解锁）")
    parser.add_argument(
        "url_or_slug",
        help="完整 https://hdhive.com/resource/115/… 链接，或仅 slug（32 位十六进制等）",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="读取 HDHIVE_API_KEY / 代理 的 .env 路径（默认 ./.env）",
    )
    parser.add_argument(
        "--access-token",
        default="",
        help="可选：用户 access token（未传时会尝试读取 .env 的 HDHIVE_ACCESS_TOKEN）",
    )
    parser.add_argument(
        "--max-points",
        type=int,
        default=0,
        help="付费解锁积分阈值（0 表示不限制）",
    )
    args = parser.parse_args(argv)

    env_path = args.env_file
    if not env_path.is_file():
        print(f"找不到环境文件: {env_path.resolve()}", file=sys.stderr)
        return 2

    values = read_env_file(env_path)
    api_key = (values.get("HDHIVE_API_KEY") or "").strip()
    if not api_key:
        print("未配置 HDHIVE_API_KEY，请在 .env 或站点设置中填写。", file=sys.stderr)
        return 2

    raw = str(args.url_or_slug or "").strip()
    low = raw.lower()
    if "hdhive.com" in low and "/resource/" in low:
        slug = extract_hdhive_resource_slug(raw)
    else:
        slug = raw

    try:
        proxy = parse_proxy_from_env(values)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    has_proxy = bool(proxy and str(proxy.host or "").strip())
    unlock_proxy = proxy if has_proxy else None
    if unlock_proxy:
        print(
            f"使用代理: {unlock_proxy.proxy_type} {unlock_proxy.host}:{unlock_proxy.port} "
            f"(rdns={unlock_proxy.rdns})",
            file=sys.stderr,
        )

    openapi_base = effective_hdhive_openapi_base_url(values)
    access_token = str(args.access_token or values.get("HDHIVE_ACCESS_TOKEN") or "").strip()
    paid_max_points = int(args.max_points) if int(args.max_points) > 0 else 2_147_483_647
    result = unlock_hdhive_resource_via_cs_rule_sync(
        slug=slug,
        api_key=api_key,
        access_token=access_token,
        proxy=unlock_proxy,
        timeout_seconds=30.0,
        openapi_base_url=openapi_base,
        allow_paid=True,
        max_points=paid_max_points,
    )
    if result.success and result.share_link.strip():
        print(result.share_link.strip())
        return 0
    if result.skipped_reason == "over_threshold":
        print("解锁已跳过：超过 max-points 阈值", file=sys.stderr)
        return 1
    if result.skipped_reason:
        print("解锁已跳过：资源不满足自动解锁规则", file=sys.stderr)
        return 1
    print(result.error_message or "解锁失败", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
