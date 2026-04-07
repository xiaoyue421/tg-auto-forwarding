"""Discover optional modules under ``<root>/<name>/module.json``; install from zip."""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any, Literal

from tg_forwarder.env_utils import read_env_file

_MODULE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")

MAX_MODULE_ZIP_BYTES = 8 * 1024 * 1024
MAX_MODULE_UNCOMPRESSED_BYTES = 32 * 1024 * 1024


def _package_modules_root() -> Path:
    return Path(__file__).resolve().parent


def resolve_modules_root(config_path: Path | None = None) -> Path:
    if config_path is not None:
        try:
            values = read_env_file(config_path)
        except OSError:
            values = {}
        raw = (values.get("TG_MODULES_PATH") or "").strip()
        if raw:
            p = Path(raw).expanduser().resolve()
            p.mkdir(parents=True, exist_ok=True)
            return p
    raw = (os.environ.get("TG_MODULES_PATH") or "").strip()
    if raw:
        p = Path(raw).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p
    return _package_modules_root()


def list_installed_modules(*, config_path: Path | None = None) -> list[dict[str, Any]]:
    root = resolve_modules_root(config_path)
    out: list[dict[str, Any]] = []
    if not root.is_dir():
        return out

    for path in sorted(root.iterdir()):
        if not path.is_dir():
            continue
        name = path.name
        if name.startswith(("_", ".")):
            continue
        manifest_path = path / "module.json"
        if not manifest_path.is_file():
            continue
        try:
            raw = manifest_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        entry = dict(data)
        entry.setdefault("id", name)
        entry.setdefault("name", name)
        entry["directory"] = name
        out.append(entry)

    return out


def get_installed_module_directory(module_id: str, *, config_path: Path | None = None) -> Path | None:
    """Return resolved module root if ``module_id`` is an installed module directory name."""
    mid = (module_id or "").strip()
    if not mid or not _MODULE_ID_RE.fullmatch(mid):
        return None
    names = {str(i.get("directory") or "") for i in list_installed_modules(config_path=config_path)}
    if mid not in names:
        return None
    root = resolve_modules_root(config_path)
    try:
        target = (root / mid).resolve()
        target.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    if not target.is_dir():
        return None
    return target


def _validate_module_dir_name(name: str) -> str:
    n = name.strip()
    if not _MODULE_ID_RE.fullmatch(n):
        raise ValueError(
            "模块目录名或 module.json 的 id 须为字母数字、下划线、短横线，长度 1–64，且不以符号开头。",
        )
    return n


def _zip_path_is_safe(name: str) -> bool:
    n = name.replace("\\", "/").strip("/")
    if not n or n.startswith("/"):
        return False
    return all(p not in ("", ".", "..") for p in n.split("/"))


def _normalized_member_names(infos: list[zipfile.ZipInfo]) -> list[str]:
    out: list[str] = []
    for zi in infos:
        if zi.is_dir():
            continue
        n = zi.filename.replace("\\", "/").strip("/")
        if not n or n.startswith("__MACOSX/") or "/__MACOSX/" in n:
            continue
        if not _zip_path_is_safe(n):
            raise ValueError("压缩包内含有非法路径（例如 ..）。")
        out.append(n)
    return out


def _analyze_zip_files(names: list[str]) -> tuple[Literal["flat", "folder"], str]:
    normalized = list(names)
    if not normalized:
        raise ValueError("压缩包为空。")

    has_subdir = any("/" in n for n in normalized)
    if not has_subdir:
        if "module.json" not in normalized:
            raise ValueError("根目录需包含 module.json。")
        return ("flat", "")

    prefixes = {n.split("/")[0] for n in normalized}
    if len(prefixes) != 1:
        raise ValueError("压缩包只能包含一个顶层目录（或根目录直接放 module.json 与代码文件）。")
    folder = _validate_module_dir_name(next(iter(prefixes)))
    if f"{folder}/module.json" not in normalized:
        raise ValueError(f"缺少 {folder}/module.json。")
    for n in normalized:
        if not n.startswith(f"{folder}/"):
            raise ValueError(f"多余条目：{n}")
    return ("folder", folder)


def _safe_dest_under(root: Path, dest: Path) -> Path:
    root_r = root.resolve()
    dest_r = dest.resolve()
    try:
        dest_r.relative_to(root_r)
    except ValueError as exc:
        raise ValueError("解压路径越界。") from exc
    return dest_r


def _extract_flat(zf: zipfile.ZipFile, target: Path) -> None:
    root = target
    for zi in zf.infolist():
        if zi.is_dir():
            continue
        name = zi.filename.replace("\\", "/").strip("/")
        if name.startswith("__MACOSX/") or not _zip_path_is_safe(name):
            continue
        if "/" in name:
            raise ValueError("扁平压缩包内不应包含子目录。")
        dest = root / name
        _safe_dest_under(root, dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(zi) as src, open(dest, "wb") as dst:
            shutil.copyfileobj(src, dst)


def _extract_folder(zf: zipfile.ZipFile, folder: str, target: Path) -> None:
    prefix = f"{folder}/"
    root = target
    for zi in zf.infolist():
        if zi.is_dir():
            continue
        name = zi.filename.replace("\\", "/")
        if name.startswith("__MACOSX/"):
            continue
        if not name.startswith(prefix):
            continue
        rel = name[len(prefix) :].strip("/")
        if not rel or not _zip_path_is_safe(rel):
            continue
        dest = root / rel.replace("/", os.sep)
        _safe_dest_under(root, dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(zi) as src, open(dest, "wb") as dst:
            shutil.copyfileobj(src, dst)


def install_module_from_zip(
    data: bytes,
    *,
    overwrite: bool = False,
    config_path: Path | None = None,
) -> dict[str, Any]:
    if len(data) > MAX_MODULE_ZIP_BYTES:
        raise ValueError(f"zip 超过 {MAX_MODULE_ZIP_BYTES // (1024 * 1024)} MB 上限。")

    root = resolve_modules_root(config_path)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(f"无法创建模块目录：{exc}") from exc

    buf = io.BytesIO(data)
    try:
        zf = zipfile.ZipFile(buf)
    except zipfile.BadZipFile as exc:
        raise ValueError("不是有效的 zip 文件。") from exc

    with zf:
        infos = [zi for zi in zf.infolist() if not zi.is_dir()]
        unc = sum(zi.file_size for zi in infos)
        if unc > MAX_MODULE_UNCOMPRESSED_BYTES:
            raise ValueError("解压后体积过大。")

        names = _normalized_member_names(infos)
        layout, folder = _analyze_zip_files(names)

        if layout == "flat":
            with zf.open("module.json") as f:
                manifest = json.load(f)
            if not isinstance(manifest, dict):
                raise ValueError("module.json 须为 JSON 对象。")
            mid = manifest.get("id")
            if not isinstance(mid, str) or not mid.strip():
                raise ValueError("扁平压缩包须在 module.json 中填写字符串字段 id（作为目录名）。")
            dir_name = _validate_module_dir_name(mid)
            target = root / dir_name
        else:
            dir_name = folder
            target = root / dir_name

        if target.exists():
            if not overwrite:
                raise ValueError(f"目录「{dir_name}」已存在。勾选「覆盖同名模块」后可替换。")
            try:
                shutil.rmtree(target)
            except OSError as exc:
                raise ValueError(f"无法删除旧目录：{exc}") from exc

        try:
            target.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            raise ValueError(f"无法创建模块目录：{exc}") from exc

        try:
            if layout == "flat":
                _extract_flat(zf, target)
            else:
                _extract_folder(zf, folder, target)
        except Exception:
            try:
                shutil.rmtree(target)
            except OSError:
                pass
            raise

    manifest_path = target / "module.json"
    if not manifest_path.is_file():
        try:
            shutil.rmtree(target)
        except OSError:
            pass
        raise ValueError("解压后缺少 module.json。")
    try:
        raw = manifest_path.read_text(encoding="utf-8")
        manifest = json.loads(raw)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        try:
            shutil.rmtree(target)
        except OSError:
            pass
        raise ValueError(f"module.json 无效：{exc}") from exc
    if not isinstance(manifest, dict):
        try:
            shutil.rmtree(target)
        except OSError:
            pass
        raise ValueError("module.json 须为 JSON 对象。")

    entry = dict(manifest)
    entry.setdefault("id", dir_name)
    entry.setdefault("name", dir_name)
    entry["directory"] = dir_name
    return {"directory": dir_name, "manifest": entry}
