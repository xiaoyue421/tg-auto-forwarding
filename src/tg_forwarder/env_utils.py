from __future__ import annotations

from pathlib import Path

from dotenv import dotenv_values


def read_env_file(path: str | Path) -> dict[str, str]:
    env_path = Path(path).resolve()
    if not env_path.exists():
        return {}
    values = dotenv_values(env_path)
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
