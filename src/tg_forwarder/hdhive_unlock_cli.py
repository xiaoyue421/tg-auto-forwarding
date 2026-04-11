"""命令行：调用 HDHive OpenAPI ``POST /api/open/resources/unlock``（与常见 PHP curl 脚本一致）。

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
    unlock_hdhive_resource_via_open_api_sync,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="HDHive OpenAPI 积分解锁（单次请求，会消耗积分）",
    )
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
    ok, share_link, err = unlock_hdhive_resource_via_open_api_sync(
        slug=slug,
        api_key=api_key,
        proxy=unlock_proxy,
        timeout_seconds=30.0,
        openapi_base_url=openapi_base,
    )
    if ok and share_link.strip():
        print(share_link.strip())
        return 0
    print(err or "解锁失败", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
