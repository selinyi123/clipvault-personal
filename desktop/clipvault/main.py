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
from clipvault.api import server as api_server
from clipvault.backup.github_backup import BackupWorker
from clipvault.instance_lock import AlreadyRunningError, InstanceLock
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
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--once", action="store_true",
                        help="ingest the current clipboard once and exit")
    args = parser.parse_args(argv)

    try:
        cfg = config_mod.load(Path(args.config))
    except config_mod.ConfigMissing as exc:
        print(f"config template written, fill obsidian.vault_path and rerun: {exc.path}")
        return 2
    except config_mod.ConfigError as exc:
        print(f"config error -> {exc.field}: {exc.message}")
        return 2

    setup_logging(cfg)
    log = logging.getLogger("clipvault.main")

    if args.once:
        conn = db.connect(cfg.db_path)
        db.migrate(conn)
        text = get_clipboard_text()
        if not text:
            print("clipboard has no text")
            return 0
        outcome = ClipVaultService(conn, cfg).handle_clipboard_text(text, get_foreground_app())
        clip_id = outcome.clip.id if outcome.clip else "-"
        print(f"{outcome.status} id={clip_id}")
        return 0

    try:
        with InstanceLock():
            conn = db.connect(cfg.db_path)
            db.migrate(conn)
            service = ClipVaultService(conn, cfg)

            stop = threading.Event()
            signal.signal(signal.SIGINT, lambda *_: stop.set())
            signal.signal(signal.SIGTERM, lambda *_: stop.set())

            def sweep_loop() -> None:
                sweep_conn = db.connect(cfg.db_path)
                sweeper = ClipVaultService(sweep_conn, cfg)
                peers = PeersRepo(sweep_conn)
                outbox = OutboxRepo(sweep_conn)
                while not stop.wait(_SWEEP_INTERVAL_S):
                    try:
                        repaired = sweeper.retry_obsidian_sweep()
                        if repaired:
                            log.info("obsidian sweep repaired %d clips", repaired)
                        # Keep the sync outbox bounded: drop events every peer has acked.
                        min_acked = peers.min_my_acked()
                        if min_acked:
                            pruned = outbox.prune_acked(min_acked)
                            if pruned:
                                log.info("outbox pruned %d acked events", pruned)
                    except Exception:  # sweep must never kill the service
                        log.exception("maintenance sweep failed")

            threading.Thread(target=sweep_loop, daemon=True, name="obsidian-sweep").start()

            if cfg.backup_enabled and cfg.backup_repo_path:
                def backup_loop() -> None:
                    backup_conn = db.connect(cfg.db_path)
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

                threading.Thread(target=backup_loop, daemon=True, name="backup-worker").start()
                log.info("backup worker enabled repo=%s interval=%dmin",
                         cfg.backup_repo_path, cfg.backup_interval_minutes)

            threading.Thread(
                target=api_server.serve, args=(cfg, stop),
                daemon=True, name="api",
            ).start()

            watcher = PollingWatcher(service.handle_clipboard_text, interval_ms=cfg.poll_ms)
            log.info("clipvault started device=%s poll=%dms", cfg.device_name, cfg.poll_ms)
            watcher.run(stop)
            log.info("clipvault stopped")
            return 0
    except AlreadyRunningError:
        print("ClipVault is already running")
        return 1


if __name__ == "__main__":
    sys.exit(main())
