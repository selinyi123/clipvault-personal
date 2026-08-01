"""Process entry point.

  python -m clipvault.main --config config.toml          # run the service
  python -m clipvault.main --config config.toml --once   # ingest current clipboard once
"""

import argparse
import logging
import logging.handlers
import signal
import sys
from pathlib import Path

from clipvault import config as config_mod
from clipvault import launcher
from clipvault.instance_lock import AlreadyRunningError, InstanceLock
from clipvault.runtime.app import ClipVaultRuntime, RuntimeStopRequested
from clipvault.service import ClipVaultService
from clipvault.store import db
from clipvault.store.clips_repo import ClipsRepo
from clipvault.v2_configure import configure_v2_ime_host
from clipvault.watcher.win_clipboard import (
    get_clipboard_text,
    get_foreground_app,
)


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
    parser.add_argument(
        "--self-test-tray",
        action="store_true",
        help="validate tray dependencies and icon construction, then exit",
    )
    parser.add_argument(
        "--self-test-tray-relink-marker",
        action="store_true",
        help="validate the recipient-modified pystray marker and tray, then exit",
    )
    parser.add_argument(
        "--third-party-notices",
        action="store_true",
        help="print bundled third-party notices, then exit",
    )
    parser.add_argument(
        "--configure-v2-ime-host",
        metavar="ABSOLUTE_PATH",
        help="atomically configure the installed v2 Windows IME Host and exit",
    )
    parser.add_argument(
        "--enable-otp-relay",
        action="store_true",
        help="explicitly enable OTP broker and pairing during v2 Host configuration",
    )
    args = parser.parse_args(argv)

    if args.enable_otp_relay and args.configure_v2_ime_host is None:
        parser.error("--enable-otp-relay requires --configure-v2-ime-host")
    if args.configure_v2_ime_host is not None and any(
        (
            args.config is not None,
            args.once,
            args.headless,
            args.no_open,
            args.self_test_tray,
            args.self_test_tray_relink_marker,
            args.third_party_notices,
        )
    ):
        parser.error("v2 Host configuration cannot be combined with run modes")

    if args.configure_v2_ime_host is not None:
        try:
            configure_v2_ime_host(
                args.configure_v2_ime_host,
                enable_otp_relay=args.enable_otp_relay,
            )
        except Exception as exc:
            # Paths and TOML bodies are private local state. Installer-facing
            # diagnostics expose only a stable operation and error class.
            print(
                f"v2 IME configuration failed err={exc.__class__.__name__}",
                file=sys.stderr,
            )
            return 2
        print("v2 IME configuration updated")
        return 0

    if args.self_test_tray or args.self_test_tray_relink_marker:
        try:
            if args.self_test_tray_relink_marker:
                launcher.self_test_tray(require_relink_marker=True)
            else:
                launcher.self_test_tray()
        except Exception as exc:
            # Dependency or backend messages can contain private installation
            # paths. The exception class is sufficient release-gate evidence.
            print(
                f"tray self-test failed err={exc.__class__.__name__}",
                file=sys.stderr,
            )
            return 1
        if args.self_test_tray_relink_marker:
            print("tray relink self-test ok")
        else:
            print("tray self-test ok")
        return 0

    if args.third_party_notices:
        try:
            notices = launcher.read_third_party_notices()
        except Exception as exc:
            print(
                f"third-party notices unavailable err={exc.__class__.__name__}",
                file=sys.stderr,
            )
            return 1
        print(notices, end="" if notices.endswith("\n") else "\n")
        return 0

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
            if ClipsRepo(conn).repair_search_index():
                logging.getLogger("clipvault.main").warning(
                    "repaired legacy search-index drift"
                )
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
            runtime = ClipVaultRuntime(cfg)

            def request_stop(*_args) -> None:
                runtime.request_stop()

            signal.signal(signal.SIGINT, request_stop)
            signal.signal(signal.SIGTERM, request_stop)

            runtime_error: str | None = None
            try:
                runtime.start()
                log.info(
                    "clipvault started device=%s poll=%dms panel=http://127.0.0.1:%d/",
                    cfg.device_name,
                    cfg.poll_ms,
                    cfg.port,
                )

                if args.headless:
                    runtime.wait()
                else:
                    if not args.no_open:
                        launcher.open_panel(cfg.port)
                    # Tray is the main-thread blocker; quitting requests a
                    # coordinated runtime shutdown.
                    if launcher.run_tray(
                        cfg.port,
                        config_path.parent,
                        runtime.request_stop,
                        runtime.stop_event,
                    ) is False:
                        if getattr(sys, "frozen", False):
                            # A packaged GUI launch must never silently degrade
                            # into an invisible, indefinitely running service.
                            runtime_error = "TrayUnavailable"
                            log.error(
                                "clipvault tray failed err=%s",
                                runtime_error,
                            )
                            runtime.request_stop()
                        else:
                            # Preserve the historical source/development
                            # fallback when optional tray packages are absent.
                            runtime.wait()
            except RuntimeStopRequested:
                pass
            except Exception as exc:
                runtime_error = exc.__class__.__name__
                log.error("clipvault runtime failed err=%s", runtime_error)
            finally:
                alive = runtime.close()
            if "backup-worker" in alive:
                log.info("waiting for backup critical section before exit")
                alive = runtime.drain_backup_before_exit()
            if alive:
                log.warning("shutdown incomplete workers=%s", ",".join(alive))
            else:
                log.info("clipvault stopped")
            return 1 if runtime_error or runtime.terminal_errors() or alive else 0
    except AlreadyRunningError:
        # Second launch: surface the already-running instance instead of erroring.
        launcher.open_panel(cfg.port)
        print("ClipVault is already running — opened the panel.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
