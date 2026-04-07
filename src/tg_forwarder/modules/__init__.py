"""
Optional extension modules (module.json + optional hooks.py + optional web/ UI).

Worker loads ``after_match`` from ``hooks.py`` (``tg_forwarder.modules.loader``).
Static pages under ``web/`` are served at ``/api/modules/ui/<directory>/…`` when logged in
(``tg_forwarder.modules.ui_runtime``).
"""

from tg_forwarder.modules.registry import list_installed_modules

__all__ = ["list_installed_modules"]
