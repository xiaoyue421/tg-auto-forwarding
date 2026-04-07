from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from dotenv import dotenv_values

logger = logging.getLogger("tg_forwarder.env")

# 合法变量名：字母/下划线开头，其余为字母数字下划线（与常见 .env 一致）
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _parse_env_line_fallback(line: str) -> tuple[str, str] | None:
    """Parse one KEY=VALUE line; return None if not a valid assignment."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if "=" not in stripped:
        return None
    key, _, rest = stripped.partition("=")
    key = key.strip()
    if not _ENV_KEY_RE.match(key):
        return None
    value = rest.strip()
    if not value:
        return key, ""
    if value.startswith('"') and value.endswith('"'):
        try:
            return key, str(json.loads(value))
        except json.JSONDecodeError:
            return key, value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return key, value[1:-1]
    return key, value


def _read_env_fallback(path: Path) -> dict[str, str]:
    """When python-dotenv rejects the file (e.g. stray lines without '='), parse line-by-line."""
    text = path.read_text(encoding="utf-8")
    out: dict[str, str] = {}
    for line in text.splitlines():
        parsed = _parse_env_line_fallback(line)
        if parsed is None:
            continue
        k, v = parsed
        out[k] = v
    return out


def read_env_file(path: str | Path) -> dict[str, str]:
    env_path = Path(path).resolve()
    if not env_path.exists():
        return {}
    try:
        values = dotenv_values(env_path)
    except Exception as exc:
        logger.warning(
            "dotenv parse failed for %s (%s); using fallback parser (invalid lines skipped)",
            env_path,
            exc.__class__.__name__,
        )
        values = _read_env_fallback(env_path)
        return {
            str(key): str(value)
            for key, value in values.items()
            if key is not None and value is not None
        }
    return {
        str(key): str(value)
        for key, value in values.items()
        if key is not None and value is not None
    }


def update_env_file(path: str | Path, values: dict[str, str | None]) -> None:
    env_path = Path(path).resolve()
    env_path.parent.mkdir(parents=True, exist_ok=True)

    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []

    pending = dict(values)
    updated_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            updated_lines.append(line)
            continue
        key, _sep, _rest = line.partition("=")
        clean_key = key.strip()
        if clean_key in pending:
            new_value = pending.pop(clean_key)
            if new_value is not None:
                updated_lines.append(f"{clean_key}={new_value}")
        else:
            updated_lines.append(line)

    if pending:
        if updated_lines and updated_lines[-1].strip():
            updated_lines.append("")
        for key, value in pending.items():
            if value is None:
                continue
            updated_lines.append(f"{key}={value}")

    env_path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")
