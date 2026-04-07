"""Load optional ``hooks.py`` from installed extension modules (worker process)."""

from __future__ import annotations

import importlib.util
import inspect
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tg_forwarder.modules.registry import list_installed_modules, resolve_modules_root

LOG = logging.getLogger("tg_forwarder.modules.loader")


@dataclass
class MessageHookSet:
    """Callables loaded from ``<module_dir>/hooks.py``."""

    after_match: list[Any] = field(default_factory=list)


def load_hooks_module_file(hooks_path: Path, logical_name: str) -> Any | None:
    """Load a ``hooks.py`` file from disk (same mechanism as worker hook discovery)."""

    spec = importlib.util.spec_from_file_location(
        f"tg_forwarder_user_hooks_{logical_name}",
        hooks_path,
    )
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        LOG.exception("failed to load hooks module %s", hooks_path)
        return None
    return mod


def load_message_hooks(env_config_path: Path | None) -> MessageHookSet:
    """
    Discover ``hooks.py`` next to each ``module.json`` under the modules root.

    ``env_config_path`` is the dashboard / worker ``.env`` file path (same as webapp).
    """
    out = MessageHookSet()
    if env_config_path is None:
        return out
    path = Path(env_config_path).resolve()
    if not path.is_file():
        return out
    try:
        items = list_installed_modules(config_path=path)
    except Exception:
        LOG.exception("list_installed_modules failed for hooks")
        return out
    root = resolve_modules_root(path)
    for entry in items:
        directory = str(entry.get("directory") or entry.get("id") or "").strip()
        if not directory:
            continue
        hooks_path = root / directory / "hooks.py"
        if not hooks_path.is_file():
            continue
        safe_name = directory.replace(".", "_")
        mod = load_hooks_module_file(hooks_path, safe_name)
        if mod is None:
            continue
        fn = getattr(mod, "after_match", None)
        if fn is not None and inspect.iscoroutinefunction(fn):
            out.after_match.append(fn)
        elif fn is not None:
            LOG.warning("ignore non-async after_match in %s", hooks_path)
    return out
