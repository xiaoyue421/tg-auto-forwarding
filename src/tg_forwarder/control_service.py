from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from tg_forwarder.config import ConfigError, load_config, resolve_forward_strategy
from tg_forwarder.supervisor import ProcessSupervisor
from tg_forwarder.user_messages import translate_error


@dataclass(slots=True)
class ServiceState:
    status: str
    config_path: str
    last_error: str | None
    snapshot: dict | None

    def as_dict(self) -> dict:
        return asdict(self)


class SupervisorService:
    def __init__(self, config_path: str | Path, *, child_log_queue: object | None = None):
        self.config_path = Path(config_path).resolve()
        self._child_log_queue = child_log_queue
        self.logger = logging.getLogger("tg_forwarder.control")
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._supervisor: ProcessSupervisor | None = None
        self._status = "stopped"
        self._last_error: str | None = None

    def start(self) -> ServiceState:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self.get_state()
            self._status = "starting"
            self._last_error = None
            self._thread = threading.Thread(
                target=self._run_supervisor,
                name="tg-forwarder-web-supervisor",
                daemon=True,
            )
            self._thread.start()
            return self.get_state()

    def stop(self) -> ServiceState:
        thread: threading.Thread | None = None
        with self._lock:
            supervisor = self._supervisor
            if supervisor is None:
                self._status = "stopped"
                return self.get_state()
            self._status = "stopping"
            supervisor.request_stop()
            thread = self._thread
        if thread:
            thread.join(timeout=15)
        with self._lock:
            if self._thread and not self._thread.is_alive():
                self._status = "stopped"
                self._thread = None
                self._supervisor = None
        return self.get_state()

    def restart(self) -> ServiceState:
        self.stop()
        return self.start()

    def validate(self) -> dict:
        config = load_config(self.config_path)
        runtime_workers = config.build_runtime_workers()
        return {
            "config_path": str(config.config_path),
            "workers": [
                {
                    "name": worker.name,
                    "source": worker.source,
                    "sources": list(worker.sources),
                    "targets": [target.chat for target in worker.targets],
                    "bot_targets": [target.chat for target in worker.bot_targets],
                    "forward_strategy": resolve_forward_strategy(
                        worker.forward_strategy,
                        worker.telegram.forward_strategy,
                        f"worker `{worker.name}`.forward_strategy",
                    ),
                    "include_edits": worker.include_edits,
                    "forward_own_messages": worker.forward_own_messages,
                }
                for worker in runtime_workers
            ],
        }

    def get_state(self) -> ServiceState:
        with self._lock:
            snapshot = self._supervisor.get_snapshot().as_dict() if self._supervisor else None
            if self._thread and not self._thread.is_alive() and self._status not in {"error", "stopped"}:
                self._status = "stopped"
                self._thread = None
                self._supervisor = None
            return ServiceState(
                status=self._status,
                config_path=str(self.config_path),
                last_error=self._last_error,
                snapshot=snapshot,
            )

    def _run_supervisor(self) -> None:
        supervisor = ProcessSupervisor(
            self.config_path,
            install_signal_handlers=False,
            child_log_queue=self._child_log_queue,
        )
        with self._lock:
            self._supervisor = supervisor
        try:
            supervisor.initialize()
            with self._lock:
                self._status = "running"
            while not supervisor.is_stop_requested():
                supervisor.run_once()
                threading.Event().wait(supervisor.get_check_interval())
            supervisor.shutdown()
            with self._lock:
                self._status = "stopped"
                self._thread = None
                self._supervisor = None
        except ConfigError as exc:
            self.logger.error("supervisor failed to start: %s", exc)
            with self._lock:
                self._status = "error"
                self._last_error = translate_error(str(exc))
                self._thread = None
                self._supervisor = None
        except Exception as exc:
            self.logger.exception("supervisor crashed")
            try:
                supervisor.shutdown()
            except Exception:
                self.logger.exception("failed to shut down workers after crash")
            with self._lock:
                self._status = "error"
                self._last_error = translate_error(str(exc))
                self._thread = None
                self._supervisor = None
