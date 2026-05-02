"""HDHive OpenAPI 单文件 SDK（同目录 ``hdhive.py``）的包入口。"""

from __future__ import annotations

from .hdhive import HDHiveClient, HDHiveOpenAPIError

__all__ = ["HDHiveClient", "HDHiveOpenAPIError"]
