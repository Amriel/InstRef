"""Headless-режим: те саме, що GUI, але з командного рядка / планувальника."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from . import notify, status
from .config import (
    Config,
    DEVICE_PATH,
    LOG_DIR,
    STATE_PATH,
    load_session,
    save_session,
)
from .instagram import IGClient, InstagramError
from .session import find_sessionid, normalize_sessionid
from .state import State
from .sync import SyncEngine

NO_SESSION_MESSAGE = (
    "Синхронізація не відбулась: застосунок не має збереженої сесії Instagram.\n\n"
    "Те, що ти залогінений у браузері, тут не допомагає — Chrome і Edge на "
    "Windows 11 шифрують кукі так, що сторонні програми їх не читають.\n\n"
    "Відкрий InstRef → вкладка «Сесія» → встав sessionid і натисни "
    "«Перевірити». Це потрібно зробити один раз."
)


def _logger(quiet: bool):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"sync_{datetime.now():%Y-%m}.log"
    handle = open(log_file, "a", encoding="utf-8")

    def log(message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{stamp}] {message}"
        handle.write(line + "\n")
        handle.flush()
        if not quiet:
            print(line, flush=True)

    return log, handle


def resolve_sessionid(cfg: Config, log) -> str:
    """Спершу збережена сесія, потім — спроба дістати кукі з браузера."""
    stored = load_session().get("sessionid", "")
    if stored:
        return stored

    log("Збереженої сесії немає — пробую дістати кукі з браузера…")
    found = find_sessionid(cfg.browser)
    for note in found.notes:
        log("  " + note)
    if found.hint:
        log("  → " + found.hint)
    if found.ok:
        save_session({"sessionid": found.sessionid, "browser": found.browser})
        log("Сесію дістано з браузера й збережено.")
        return found.sessionid or ""
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="igsaved", description="Синхронізація збережених постів Instagram у папку та Eagle."
    )
    parser.add_argument("--sync", action="store_true", help="запустити синхронізацію")
    parser.add_argument("--list", action="store_true", help="показати підбірки й вийти")
    parser.add_argument("--check", action="store_true",
                        help="перевірити збережену сесію й вийти")
    parser.add_argument("--collection", action="append", default=[],
                        help="pk підбірки (можна кілька разів); за замовчуванням — з config.json")
    parser.add_argument("--session", default="", help="sessionid вручну (разово)")
    parser.add_argument("--dir", default="", help="перевизначити папку завантаження")
    parser.add_argument("--limit", type=int, default=None, help="максимум нових постів за запуск")
    parser.add_argument("--full", action="store_true", help="повний обхід замість «лише нове»")
    parser.add_argument("--no-eagle", action="store_true", help="не імпортувати в Eagle")
    parser.add_argument("--quiet", action="store_true", help="без виводу в консоль")
    parser.add_argument("--no-popup", action="store_true",
                        help="не показувати системне вікно при збої фонового запуску")
    args = parser.parse_args(argv)

    cfg = Config.load()
    if args.dir:
        cfg.download_dir = str(Path(args.dir).expanduser())
    if args.limit is not None:
        cfg.max_items_per_run = args.limit
    if args.full:
        cfg.incremental = False
    if args.no_eagle:
        cfg.eagle_enabled = False

    # Тихий запуск = це майже напевно планувальник, а не людина за клавіатурою.
    source = "scheduled" if args.quiet else "cli"
    log, handle = _logger(args.quiet)

    try:
        sessionid = normalize_sessionid(args.session) if args.session else resolve_sessionid(cfg, log)

        if not sessionid:
            log("✖ Сесію Instagram не підключено — синхронізацію скасовано.")
            log("  Відкрий застосунок → вкладка «Сесія» → встав sessionid → «Перевірити».")
            status.write(
                status.NO_SESSION,
                source=source,
                summary="Сесію Instagram не підключено",
                advice=status.NO_SESSION_ADVICE,
            )
            if source == "scheduled" and not args.no_popup:
                notify.popup("InstRef — потрібна сесія", NO_SESSION_MESSAGE)
            return 2

        state = State(STATE_PATH)
        try:
            if args.check:
                client = IGClient(device_path=DEVICE_PATH, log=log, proxy=cfg.proxy)
                username = client.connect(sessionid)
                log(f"✓ Сесія дійсна: @{username}")
                return 0

            if args.list:
                client = IGClient(device_path=DEVICE_PATH, log=log, proxy=cfg.proxy)
                client.connect(sessionid)
                for col in client.list_collections():
                    log(f"{col.pk:<28} {col.display}  ({col.media_count})")
                return 0

            engine = SyncEngine(cfg, state, sessionid, log=log)
            stats = engine.run(only_collections=args.collection or None)

            if stats.errors:
                status.write(
                    status.FAILED, source=source,
                    summary=stats.summary(), errors=stats.errors,
                )
                if source == "scheduled" and not args.no_popup:
                    notify.popup(
                        "InstRef — синхронізація з помилками",
                        f"{stats.summary()}\n\n{stats.errors[0]}",
                    )
                return 1

            status.write(status.OK, source=source, summary=stats.summary())
            return 0
        finally:
            state.close()

    except InstagramError as exc:
        log(f"✖ {exc}")
        status.write(status.FAILED, source=source, summary=str(exc), errors=[str(exc)])
        if source == "scheduled" and not args.no_popup:
            notify.popup("InstRef — помилка", str(exc))
        return 1
    finally:
        handle.close()


if __name__ == "__main__":
    sys.exit(main())
