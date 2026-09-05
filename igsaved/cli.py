"""Headless-режим: те саме, що GUI, але з командного рядка / планувальника."""

from __future__ import annotations

import argparse
import random
import sys
import time
from datetime import datetime, timedelta
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


LOG_KEEP_MONTHS = 3


def rotate_logs(log_dir: Path = LOG_DIR, keep_months: int = LOG_KEEP_MONTHS) -> int:
    """Прибирає місячні журнали, старші за keep_months. Повертає, скільки стерто.

    Журнал sync_YYYY-MM.log ріс без обмеження — за рік це десятки мегабайт
    тексту, який ніхто не прочитає.
    """
    cutoff = datetime.now() - timedelta(days=31 * keep_months)
    removed = 0
    for path in list(log_dir.glob("sync_*.log")) + list(log_dir.glob("gui_*.log")):
        try:
            stamp = datetime.strptime(path.stem.split("_", 1)[1], "%Y-%m")
        except ValueError:
            continue
        if stamp < cutoff:
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def _logger(quiet: bool):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    rotate_logs()
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


def _jitter(cfg: Config, log) -> None:
    """Випадкова пауза перед плановим проходом.

    Рівно о 09:00:00 щодня — підпис планувальника; людина так не заходить.
    Кілька хвилин розкиду коштують нічого, а рівний ритм прибирають.
    """
    minutes = int(cfg.schedule_jitter_minutes or 0)
    if minutes <= 0:
        return
    seconds = random.uniform(0, minutes * 60)
    log(f"Плановий прохід: чекаю {int(seconds // 60)} хв {int(seconds % 60)} с (випадковий зсув).")
    time.sleep(seconds)


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
    parser.add_argument("--no-jitter", action="store_true",
                        help="без випадкової затримки перед плановим проходом")
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
            if source == "scheduled" and not args.no_jitter and engine.cooldown_left() <= 0:
                _jitter(cfg, log)
            stats = engine.run(only_collections=args.collection or None)

            if stats.skipped_run:
                # Свідомий пропуск — не привід для вікна «з помилками».
                status.write(status.SKIPPED, source=source, summary=stats.summary())
                return 0

            if stats.errors:
                status.write(
                    status.FAILED if stats.reason != "session_dead" else status.NO_SESSION,
                    source=source,
                    summary=stats.summary(), errors=stats.errors,
                    advice=status.NO_SESSION_ADVICE if stats.reason == "session_dead" else "",
                )
                if source == "scheduled" and not args.no_popup:
                    notify.popup(
                        "InstRef — синхронізація з помилками",
                        f"{stats.summary()}\n\n{stats.errors[0]}",
                    )
                return 1

            status.write(status.OK, source=source, summary=stats.summary())
            if source == "scheduled" and not args.no_popup and cfg.notify_on_finish \
                    and (stats.downloaded or stats.to_review):
                # Безголовий прохід — єдиний спосіб дізнатись, що є на що глянути.
                notify.popup("InstRef — синхронізацію завершено", stats.summary(), seconds=15)
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
