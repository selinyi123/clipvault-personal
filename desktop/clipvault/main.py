"""Process entry point.

  python -m clipvault.main --config config.toml          # run the service
  python -m clipvault.main --config config.toml --once   # ingest current clipboard once
"""

import argparse
import logging
import logging.handlers
import signal
import sys
import threading
import time
from pathlib import Path

from clipvault import config as config_mod
from clipvault import launcher
from clipvault.api import server as api_server
from clipvault.backup.github_backup import BackupWorker
from clipvault.instance_lock import AlreadyRunningError, InstanceLock
from clipvault.runtime.obsidian_worker import ObsidianWorker
from clipvault.service import ClipVaultService
from clipvault.store.outbox_repo import OutboxRepo
from clipvault.store.peers_repo import PeersRepo
from clipvault.store import db
from clipvault.watcher.win_clipboard import (
    PollingWatcher,
    get_clipboard_text,
    get_foreground_app,
)

_SWEEP_INTERVAL_S = 60


def _handle_clipboard_text_with_fresh_connection(
    cfg: config_mod.Config,
    text: str,
    source_app: str | None,
    obsidian_notify=None,
):
    """Handle a watcher event using a connection owned by the watcher thread."""
    conn = db.connect(cfg.db_path)
    try:
        return ClipVaultService(
            conn,
            cfg,
            obsidian_notify=obsidian_notify,
        ).handle_clipboard_text(text, source_app)
    finally:
        conn.close()


def setup_logging(cfg: config_mod.Config) -> None:
    log_dir = Path(cfg.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    file_handler = logging.handlers.TimedRotatingFileHandler(
        log_dir / "clipvault.log", when="midnight",
        backupCount=cfg.log_retention_days, encoding="utf-8",
    )
    console = logging.StreamHandler()
    for handler in (file_handler, console):
        handler.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[file_handler, console])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="clipvault")
    parser.add_argument("--config", default=None,
                        help="config path (default: %%LOCALAPPDATA%%/ClipVault/config.toml)")
    parser.add_argument("--once", action="store_true",
                        help="ingest the current clipboard once and exit")
    parser.add_argument("--headless", action="store_true",
                        help="run the service without tray/browser (for autostart)")
    parser.add_argument("--no-open", action="store_true",
                        help="run with tray but do not auto-open the browser (login autostart)")
    args = parser.parse_args(argv)

    if args.once:
        try:
            cfg = config_mod.load(Path(args.config or launcher.default_config_path()))
        except (config_mod.ConfigMissing, config_mod.ConfigError) as exc:
            print(f"config error: {exc}")
            return 2
        setup_logging(cfg)
        conn = db.connect(cfg.db_path)
        try:
            db.migrate(conn)
            text = get_clipboard_text()
            if not text:
                print("clipboard has no text")
                return 0
            service = ClipVaultService(conn, cfg)
            outcome = service.handle_clipboard_text(text, get_foreground_app())
            # --once is an explicit synchronous CLI operation; drain its one
            # queued Vault intent before exiting while normal service paths stay
            # asynchronous.
            if outcome.needs_obsidian:
                service.write_obsidian_or_queue(outcome.clip)
            clip_id = outcome.clip.id if outcome.clip else "-"
            print(f"{outcome.status} id={clip_id}")
            return 0
        finally:
            conn.close()

    # Service mode (default). First run auto-creates a working config so the app
    # just works on a fresh machine — no manual editing required.
    config_path = launcher.ensure_config(args.config)
    cfg = config_mod.load(config_path)
    setup_logging(cfg)
    log = logging.getLogger("clipvault.main")

    try:
        with InstanceLock():
            conn = db.connect(cfg.db_path)
            try:
                db.migrate(conn)
            finally:
                conn.close()

            stop = threading.Event()
            obsidian_worker = ObsidianWorker(cfg, interval_s=_SWEEP_INTERVAL_S)

            def request_stop(*_args) -> None:
                stop.set()
                obsidian_worker.notify()

            signal.signal(signal.SIGINT, request_stop)
            signal.signal(signal.SIGTERM, request_stop)

            threads: list[threading.Thread] = []

            def maintenance_loop() -> None:
                sweep_conn = db.connect(cfg.db_path)
                peers = PeersRepo(sweep_conn)
                outbox = OutboxRepo(sweep_conn)
                try:
                    while not stop.wait(_SWEEP_INTERVAL_S):
                        try:
                        # Keep the sync outbox bounded: drop events every peer has acked.
                            min_acked = peers.min_my_acked()
                            if min_acked:
                                pruned = outbox.prune_acked(min_acked)
                                if pruned:
                                    log.info("outbox pruned %d acked events", pruned)
                        except Exception:  # maintenance must never kill the service
                            log.exception("maintenance sweep failed")
                finally:
                    sweep_conn.close()

            threads.append(threading.Thread(
                target=obsidian_worker.run,
                args=(stop,),
                daemon=True,
                name="obsidian-worker",
            ))
            threads.append(threading.Thread(
                target=maintenance_loop,
                daemon=True,
                name="maintenance",
            ))

            if cfg.backup_enabled and cfg.backup_repo_path:
                def backup_loop() -> None:
                    backup_conn = db.connect(cfg.db_path)
                    try:
                        worker = BackupWorker(backup_conn, cfg.backup_repo_path)
                        interval_s = max(60, cfg.backup_interval_minutes * 60)
                        while not stop.wait(interval_s):
                            try:
                                stats = worker.run_once(monotonic=time.monotonic())
                                if stats["written"] or stats["dropped"]:
                                    log.info("backup: wrote=%d dropped=%d pushed=%s",
                                             stats["written"], stats["dropped"], stats["pushed"])
                            except Exception:  # worker must never kill the service
                                log.exception("backup worker failed")
                    finally:
                        backup_conn.close()

                threads.append(threading.Thread(
                    target=backup_loop,
                    daemon=True,
                    name="backup-worker",
                ))
                log.info("backup worker enabled repo=%s interval=%dmin",
                         cfg.backup_repo_path, cfg.backup_interval_minutes)

            threads.append(threading.Thread(
                target=api_server.serve, args=(cfg, stop),
                kwargs={"obsidian_notify": obsidian_worker.notify},
                daemon=True, name="api",
            ))

            watcher = PollingWatcher(
                lambda text, app: _handle_clipboard_text_with_fresh_connection(
                    cfg, text, app, obsidian_worker.notify
                ),
                interval_ms=cfg.poll_ms,
            )
            threads.append(threading.Thread(
                target=watcher.run,
                args=(stop,),
                daemon=True,
                name="watcher",
            ))
            for thread in threads:
                thread.start()
            log.info("clipvault started device=%s poll=%dms panel=http://127.0.0.1:%d/",
                     cfg.device_name, cfg.poll_ms, cfg.port)

            if args.headless:
                stop.wait()
            else:
                if not args.no_open:
                    launcher.open_panel(cfg.port)
                # Tray is the main-thread blocker; quitting it stops the service.
                if launcher.run_tray(cfg.port, config_path.parent, request_stop) is False:
                    stop.wait()  # no pystray (e.g. dev without deps) -> just run
            request_stop()
            shutdown_deadline = time.monotonic() + 5.0
            for thread in threads:
                thread.join(timeout=max(0.0, shutdown_deadline - time.monotonic()))
            alive = [thread.name for thread in threads if thread.is_alive()]
            if alive:
                log.warning("shutdown incomplete workers=%s", ",".join(alive))
            else:
                log.info("clipvault stopped")
            return 0
    except AlreadyRunningError:
        # Second launch: surface the already-running instance instead of erroring.
        launcher.open_panel(cfg.port)
        print("ClipVault is already running — opened the panel.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
