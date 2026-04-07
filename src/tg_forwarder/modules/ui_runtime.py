"""Optional static UI under ``<module>/<ui.root>/<ui.entry>`` (default ``web/index.html``)."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.responses import FileResponse

from tg_forwarder.modules.registry import list_installed_modules, resolve_modules_root

_KNOWN_CAPABILITIES = {
    "config_edit",
    "preview",
    "rule_edit",
}


def _normalized_capabilities(entry: dict[str, Any]) -> list[str]:
    raw = entry.get("capabilities")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        token = str(item or "").strip().lower()
        if token in _KNOWN_CAPABILITIES and token not in out:
            out.append(token)
    return out


def _parse_module_ui_spec(entry: dict[str, Any]) -> tuple[str, str]:
    web_sub = "web"
    entry_name = "index.html"
    ui = entry.get("ui")
    if isinstance(ui, dict):
        w = str(ui.get("root") or "web").strip()
        if w and "/" not in w and "\\" not in w and w not in (".", "..") and not w.startswith("."):
            web_sub = w
        e = str(ui.get("entry") or "index.html").strip().replace("\\", "/")
        if e and ".." not in e.split("/") and not e.startswith("/"):
            entry_name = e
    return web_sub, entry_name


def enrich_modules_ui_metadata(
    items: list[dict[str, Any]],
    *,
    config_path: Path | None,
) -> list[dict[str, Any]]:
    root = resolve_modules_root(config_path)
    root_r = root.resolve()
    out: list[dict[str, Any]] = []
    for raw in items:
        item = dict(raw)
        item["capabilities"] = _normalized_capabilities(item)
        d = str(item.get("directory") or "").strip()
        if not d:
            out.append(item)
            continue
        web_sub, entry_name = _parse_module_ui_spec(item)
        module_base = (root / d).resolve()
        if not module_base.is_dir():
            out.append(item)
            continue
        try:
            module_base.relative_to(root_r)
        except ValueError:
            out.append(item)
            continue
        web_dir = (module_base / web_sub).resolve()
        try:
            web_dir.relative_to(module_base)
        except ValueError:
            out.append(item)
            continue
        entry_rel = Path(entry_name)
        if entry_rel.is_absolute() or ".." in entry_rel.parts:
            out.append(item)
            continue
        entry_path = (web_dir / entry_rel).resolve()
        try:
            entry_path.relative_to(web_dir)
        except ValueError:
            out.append(item)
            continue
        if entry_path.is_file():
            item["has_ui"] = True
            item["ui_root"] = web_sub
            item["ui_entry"] = entry_name.replace("\\", "/")
        out.append(item)
    return out


def build_module_ui_file_response(
    *,
    module_id: str,
    file_path: str,
    config_path: Path | None,
) -> FileResponse:
    items = enrich_modules_ui_metadata(
        list_installed_modules(config_path=config_path),
        config_path=config_path,
    )
    meta = next((x for x in items if x.get("directory") == module_id), None)
    if not meta or not meta.get("has_ui"):
        raise HTTPException(status_code=404, detail="模块无界面或不存在。")
    web_sub = str(meta.get("ui_root") or "web")
    root = resolve_modules_root(config_path)
    module_base = (root / module_id).resolve()
    base = (module_base / web_sub).resolve()
    try:
        base.relative_to(module_base.resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail="无效路径。") from None
    rel = (file_path or "").strip().lstrip("/").replace("\\", "/")
    if not rel:
        rel = str(meta.get("ui_entry") or "index.html")
    if ".." in rel.split("/"):
        raise HTTPException(status_code=404, detail="无效路径。")
    candidate = (base / rel).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=404, detail="无效路径。") from None
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="文件不存在。")
    media_type = mimetypes.guess_type(str(candidate))[0]
    return FileResponse(candidate, media_type=media_type or "application/octet-stream")
