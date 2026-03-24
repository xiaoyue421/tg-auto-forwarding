from __future__ import annotations

from dataclasses import asdict, dataclass
import logging
import multiprocessing as mp
from pathlib import Path
import signal
import threading
import time

from tg_forwarder.config import (
    AppConfig,
    ConfigError,
    DEFAULT_RATE_LIMIT_DELAY_SECONDS,
    WorkerRuntimeConfig,
    resolve_forward_strategy,
    load_config,
    worker_config_digest,
)
from tg_forwarder.dispatch_queue import ensure_dispatch_queue, get_dispatch_queue_stats, resolve_queue_db_path
from tg_forwarder.dispatcher import run_dispatcher_process
from tg_forwarder.startup_notifier import send_startup_notifications
from tg_forwarder.worker import run_worker_process


@dataclass(slots=True)
class WorkerSnapshot:
    name: str
    source: str | int
    sources: list[str | int]
    targets: list[str | int]
    bot_targets: list[str | int]
    forward_strategy: str
    include_edits: bool
    forward_own_messages: bool
    pid: int | None
    is_alive: bool
    exit_code: int | None
    failure_count: int
    paused: bool
    pause_reason: str | None


@dataclass(slots=True)
class SupervisorSnapshot:
    config_path: str
    queue_db_path: str
    stop_requested: bool
    loaded: bool
    check_interval_seconds: int
    worker_max_restart_failures: int
    worker_failure_reset_seconds: int
    rate_limit_protection_enabled: bool
    rate_limit_delay_seconds: float
    global_queue_depth: int
    global_queue_failed: int
    global_queue_delivery_depth: int
    global_queue_delivery_failed: int
    dispatcher_pid: int | None
    dispatcher_alive: bool
    workers: list[WorkerSnapshot]

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class ManagedWorker:
    runtime: WorkerRuntimeConfig
    digest: str
    process: mp.Process
    stop_event: mp.Event
    failure_count: int = 0
    paused: bool = False
    pause_reason: str | None = None
    started_at_monotonic: float = 0.0
    last_exit_code: int | None = None


class ProcessSupervisor:
    def __init__(self, config_path: str | Path, install_signal_handlers: bool = True):
        self.config_path = Path(config_path).resolve()
        self.queue_db_path = resolve_queue_db_path(self.config_path)
        self.install_signal_handlers = install_signal_handlers
        self.logger = logging.getLogger("tg_forwarder.supervisor")
        self.current_config: AppConfig | None = None
        self.workers: dict[str, ManagedWorker] = {}
        self.stop_requested = False
        self.last_mtime: float | None = None
        self._lock = threading.RLock()
        self._dispatcher_process: mp.Process | None = None
        self._dispatcher_stop_event: mp.Event | None = None

    def initialize(self) -> None:
        if self.install_signal_handlers:
            self._install_signal_handlers()
        ensure_dispatch_queue(self.queue_db_path)
        self._start_dispatcher()
        self._reload_config(initial=True)
        threading.Thread(
            target=self._send_startup_notifications_safe,
            name="tg-forwarder-startup-notify",
            daemon=True,
        ).start()

    def run_forever(self) -> None:
        self.initialize()
        while not self.is_stop_requested():
            self.run_once()
            time.sleep(self.get_check_interval())
        self.shutdown()

    def run_once(self) -> None:
        self._maybe_reload_on_change()
        self._monitor_dispatcher()
        self._monitor_workers()

    def request_stop(self) -> None:
        with self._lock:
            self.stop_requested = True

    def shutdown(self) -> None:
        self._shutdown_all()

    def is_stop_requested(self) -> bool:
        with self._lock:
            return self.stop_requested

    def get_check_interval(self) -> int:
        with self._lock:
            if self.current_config:
                return self.current_config.supervisor.check_interval_seconds
            return 5

    def get_snapshot(self) -> SupervisorSnapshot:
        with self._lock:
            workers = [
                WorkerSnapshot(
                    name=managed.runtime.name,
                    source=managed.runtime.source,
                    sources=list(managed.runtime.sources),
                    targets=[target.chat for target in managed.runtime.targets],
                    bot_targets=[target.chat for target in managed.runtime.bot_targets],
                    forward_strategy=resolve_forward_strategy(
                        managed.runtime.forward_strategy,
                        managed.runtime.telegram.forward_strategy,
                        f"worker `{managed.runtime.name}`.forward_strategy",
                    ),
                    include_edits=managed.runtime.include_edits,
                    forward_own_messages=managed.runtime.forward_own_messages,
                    pid=managed.process.pid,
                    is_alive=managed.process.is_alive(),
                    exit_code=(
                        None
                        if managed.process.is_alive()
                        else (
                            managed.process.exitcode
                            if managed.process.exitcode is not None
                            else managed.last_exit_code
                        )
                    ),
                    failure_count=managed.failure_count,
                    paused=managed.paused,
                    pause_reason=managed.pause_reason,
                )
                for managed in self.workers.values()
            ]
            dispatcher_process = self._dispatcher_process
            current_config = self.current_config

        queue_stats = get_dispatch_queue_stats(self.queue_db_path)
        return SupervisorSnapshot(
            config_path=str(self.config_path),
            queue_db_path=str(self.queue_db_path),
            stop_requested=self.stop_requested,
            loaded=current_config is not None,
            check_interval_seconds=self.get_check_interval(),
            worker_max_restart_failures=(
                current_config.supervisor.worker_max_restart_failures if current_config else 5
            ),
            worker_failure_reset_seconds=(
                current_config.supervisor.worker_failure_reset_seconds if current_config else 60
            ),
            rate_limit_protection_enabled=bool(
                current_config and current_config.telegram.rate_limit_protection
            ),
            rate_limit_delay_seconds=(
                current_config.telegram.rate_limit_delay_seconds
                if current_config
                else DEFAULT_RATE_LIMIT_DELAY_SECONDS
            ),
            global_queue_depth=queue_stats.active_count,
            global_queue_failed=queue_stats.failed_count,
            global_queue_delivery_depth=queue_stats.active_delivery_count,
            global_queue_delivery_failed=queue_stats.failed_delivery_count,
            dispatcher_pid=dispatcher_process.pid if dispatcher_process else None,
            dispatcher_alive=bool(dispatcher_process and dispatcher_process.is_alive()),
            workers=workers,
        )

    def _install_signal_handlers(self) -> None:
        def request_stop(signum: int, _frame: object) -> None:
            self.logger.info("received signal %s, shutting down workers", signum)
            self.request_stop()

        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, request_stop)

    def _maybe_reload_on_change(self) -> None:
        with self._lock:
            current_config = self.current_config
        if not current_config:
            return
        if not current_config.supervisor.reload_on_change:
            return
        if not self.config_path.exists():
            return
        current_mtime = self.config_path.stat().st_mtime
        with self._lock:
            last_mtime = self.last_mtime
        if last_mtime is None or current_mtime > last_mtime:
            self._reload_config(initial=False)

    def _reload_config(self, initial: bool) -> None:
        try:
            config = load_config(self.config_path)
            runtime_workers = config.build_runtime_workers()
        except ConfigError as exc:
            if initial:
                raise
            self.logger.error("config reload failed, keeping current workers: %s", exc)
            return

        with self._lock:
            self.current_config = config
            self.last_mtime = self.config_path.stat().st_mtime if self.config_path.exists() else None
        self._sync_workers(runtime_workers)
        self.logger.info("config loaded, active workers=%s", len(runtime_workers))

    def _send_startup_notifications_safe(self) -> None:
        try:
            send_startup_notifications(self.config_path, logger=self.logger)
        except Exception:
            self.logger.exception("failed to send startup notifications")

    def _sync_workers(self, desired_workers: list[WorkerRuntimeConfig]) -> None:
        desired_map = {worker.name: worker for worker in desired_workers}
        with self._lock:
            current_names = list(self.workers)

        for name in current_names:
            if name not in desired_map:
                self.logger.info("worker `%s` removed from config, stopping", name)
                self._stop_worker(name)

        for name, runtime in desired_map.items():
            digest = worker_config_digest(runtime)
            with self._lock:
                current = self.workers.get(name)
            if current is None:
                self._start_worker(runtime, digest)
                continue
            if current.digest != digest:
                self.logger.info("worker `%s` config changed, restarting", name)
                self._stop_worker(name)
                self._start_worker(runtime, digest)

    def _monitor_dispatcher(self) -> None:
        with self._lock:
            dispatcher_process = self._dispatcher_process
            current_config = self.current_config
        if dispatcher_process is None or dispatcher_process.is_alive():
            return

        restart_delay = (
            current_config.supervisor.restart_delay_seconds if current_config is not None else 3
        )
        self.logger.warning(
            "dispatcher exited, exit_code=%s, restarting in %s seconds",
            dispatcher_process.exitcode,
            restart_delay,
        )
        time.sleep(restart_delay)
        if self.is_stop_requested():
            return
        self._start_dispatcher()

    def _monitor_workers(self) -> None:
        with self._lock:
            current_config = self.current_config
            worker_items = list(self.workers.items())
        if not current_config:
            return
        for name, managed in worker_items:
            if managed.paused:
                continue
            if managed.process.is_alive():
                if managed.failure_count > 0:
                    runtime_seconds = max(0.0, time.monotonic() - managed.started_at_monotonic)
                    if runtime_seconds >= current_config.supervisor.worker_failure_reset_seconds:
                        managed.failure_count = 0
                        managed.last_exit_code = None
                continue
            exit_code = managed.process.exitcode
            managed.last_exit_code = exit_code
            failure_count = self._calculate_worker_failure_count(
                managed=managed,
                exit_code=exit_code,
                current_config=current_config,
            )
            managed.failure_count = failure_count
            if exit_code not in (None, 0) and (
                failure_count >= current_config.supervisor.worker_max_restart_failures
            ):
                managed.paused = True
                managed.pause_reason = (
                    f"连续失败 {failure_count} 次，已自动暂停。"
                    " 请检查 session、代理或网络后点击“重启后端”。"
                )
                self.logger.error(
                    "worker `%s` paused after %s consecutive failures, last exit_code=%s",
                    name,
                    failure_count,
                    exit_code,
                )
                continue
            self.logger.warning(
                "worker `%s` exited, exit_code=%s, failure_count=%s, restarting in %s seconds",
                name,
                exit_code,
                failure_count,
                current_config.supervisor.restart_delay_seconds,
            )
            time.sleep(current_config.supervisor.restart_delay_seconds)
            if self.is_stop_requested():
                return
            refreshed_runtime = self._find_runtime_by_name(name)
            if refreshed_runtime is None:
                with self._lock:
                    self.workers.pop(name, None)
                continue
            self._start_worker(
                refreshed_runtime,
                worker_config_digest(refreshed_runtime),
                failure_count=failure_count,
                last_exit_code=exit_code,
            )

    def _calculate_worker_failure_count(
        self,
        *,
        managed: ManagedWorker,
        exit_code: int | None,
        current_config: AppConfig,
    ) -> int:
        if exit_code in (None, 0):
            return 0
        runtime_seconds = max(0.0, time.monotonic() - managed.started_at_monotonic)
        if runtime_seconds >= current_config.supervisor.worker_failure_reset_seconds:
            return 1
        return managed.failure_count + 1

    def _find_runtime_by_name(self, worker_name: str) -> WorkerRuntimeConfig | None:
        with self._lock:
            current_config = self.current_config
        if not current_config:
            return None
        for runtime in current_config.build_runtime_workers():
            if runtime.name == worker_name:
                return runtime
        return None

    def _start_dispatcher(self) -> None:
        self._stop_dispatcher()
        stop_event = mp.Event()
        process = mp.Process(
            target=run_dispatcher_process,
            name="tgf-dispatcher",
            args=(str(self.config_path), str(self.queue_db_path), stop_event),
        )
        process.start()
        with self._lock:
            self._dispatcher_process = process
            self._dispatcher_stop_event = stop_event
        self.logger.info("dispatcher started, pid=%s, queue_db_path=%s", process.pid, self.queue_db_path)

    def _stop_dispatcher(self) -> None:
        with self._lock:
            process = self._dispatcher_process
            stop_event = self._dispatcher_stop_event
            current_config = self.current_config
            self._dispatcher_process = None
            self._dispatcher_stop_event = None
        if process is None:
            return
        stop_timeout = current_config.supervisor.stop_timeout_seconds if current_config else 10
        if stop_event is not None:
            stop_event.set()
        process.join(timeout=stop_timeout)
        if process.is_alive():
            self.logger.warning("dispatcher did not stop in time, terminating")
            process.terminate()
            process.join(timeout=5)
        self.logger.info("dispatcher stopped")

    def _start_worker(
        self,
        runtime: WorkerRuntimeConfig,
        digest: str,
        *,
        failure_count: int = 0,
        last_exit_code: int | None = None,
    ) -> None:
        stop_event = mp.Event()
        process = mp.Process(
            target=run_worker_process,
            name=f"tgf-{runtime.name}",
            args=(runtime.as_payload(), stop_event, str(self.queue_db_path)),
        )
        process.start()
        with self._lock:
            self.workers[runtime.name] = ManagedWorker(
                runtime=runtime,
                digest=digest,
                process=process,
                stop_event=stop_event,
                failure_count=failure_count,
                paused=False,
                pause_reason=None,
                started_at_monotonic=time.monotonic(),
                last_exit_code=last_exit_code,
            )
        self.logger.info("worker `%s` started, pid=%s", runtime.name, process.pid)

    def _stop_worker(self, worker_name: str) -> None:
        with self._lock:
            managed = self.workers.pop(worker_name, None)
            current_config = self.current_config
        if managed is None:
            return
        stop_timeout = current_config.supervisor.stop_timeout_seconds if current_config else 10
        managed.stop_event.set()
        managed.process.join(timeout=stop_timeout)
        if managed.process.is_alive():
            self.logger.warning("worker `%s` did not stop in time, terminating", worker_name)
            managed.process.terminate()
            managed.process.join(timeout=5)
        self.logger.info("worker `%s` stopped", worker_name)

    def _shutdown_all(self) -> None:
        with self._lock:
            names = list(self.workers)
        for name in names:
            self._stop_worker(name)
        self._stop_dispatcher()
        self.logger.info("all workers stopped")
