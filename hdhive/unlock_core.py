"""与 ``tg_forwarder.hdhive_unlock_core`` 同逻辑；供仓库根目录下 ``hdhive`` CLI 通过 ``import hdhive.unlock_core`` 使用。"""

from __future__ import annotations

import sys
from pathlib import Path

_src = Path(__file__).resolve().parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from tg_forwarder.hdhive_unlock_core import (  # noqa: E402
    can_unlock_by_points,
    classify_share_for_auto_unlock,
    extract_share_link_from_unlock_response,
    is_free_resource,
    preview_decision_from_share_data,
)

__all__ = [
    "can_unlock_by_points",
    "classify_share_for_auto_unlock",
    "extract_share_link_from_unlock_response",
    "is_free_resource",
    "preview_decision_from_share_data",
]
