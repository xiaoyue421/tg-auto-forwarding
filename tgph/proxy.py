"""Telegraph HTTP 请求复用控制台「系统与连接」代理（TG_PROXY_*）。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def resolve_tgph_proxy(
    env_config_path: str | Path | None = None,
    env_values: dict[str, str] | None = None,
) -> Any | None:
    """从 .env 解析与 Telegram / HDHive 相同的单代理配置。"""
    values: dict[str, str] = dict(env_values or {})
    if env_config_path is not None:
        path = Path(env_config_path)
        if path.is_file():
            try:
                from tg_forwarder.env_utils import read_env_file

                values = read_env_file(path)
            except OSError:
                pass
    elif not values:
        values = {k: str(v) for k, v in os.environ.items() if v is not None}

    try:
        from tg_forwarder.config import parse_proxy_from_env

        proxy = parse_proxy_from_env(values)
    except Exception:
        return None
    if proxy is None:
        return None
    if not str(getattr(proxy, "host", "") or "").strip():
        return None
    return proxy
