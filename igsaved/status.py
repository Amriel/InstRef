"""Підсумок останнього запуску — щоб фоновий збій не залишався непоміченим.

Кожен запуск (і з вікна, і з планувальника) пише сюди свій результат.
Наступний старт GUI читає файл і показує банер, якщо востаннє щось пішло не так.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .config import STATUS_PATH

OK = "ok"
FAILED = "failed"
NO_SESSION = "no_session"
STOPPED = "stopped"
SKIPPED = "skipped"   # прохід не відбувся свідомо: кулдаун або інший процес

# Що саме показати користувачу для кожного результату.
HEADLINES = {
    OK: "Останній запуск пройшов успішно",
    STOPPED: "Останній запуск було зупинено",
    SKIPPED: "Останній плановий запуск пропущено (запобіжник частоти)",
    FAILED: "Останній запуск завершився помилкою",
    NO_SESSION: "Фоновий запуск не відбувся: сесію Instagram не підключено",
}

NO_SESSION_ADVICE = (
    "Відкрий вкладку «Сесія» і встав sessionid — один раз. "
    "Після цього запуски за розкладом працюватимуть самі."
)


@dataclass
class RunStatus:
    result: str = OK
    when: str = ""
    source: str = "gui"  # gui | scheduled
    summary: str = ""
    advice: str = ""
    errors: List[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return self.result in (FAILED, NO_SESSION)

    @property
    def headline(self) -> str:
        return HEADLINES.get(self.result, "Останній запуск")

    @property
    def when_human(self) -> str:
        try:
            stamp = datetime.fromisoformat(self.when)
        except (TypeError, ValueError):
            return ""
        return stamp.strftime("%d.%m %H:%M")


def write(
    result: str,
    source: str = "gui",
    summary: str = "",
    advice: str = "",
    errors: Optional[List[str]] = None,
    path: Optional[Path] = None,
) -> None:
    payload = RunStatus(
        result=result,
        when=datetime.now().isoformat(timespec="seconds"),
        source=source,
        summary=summary,
        advice=advice,
        errors=(errors or [])[:5],
    )
    target = path or STATUS_PATH
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(payload), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        pass  # статус — приємний бонус, а не причина валити запуск


def read(path: Optional[Path] = None) -> Optional[RunStatus]:
    target = path or STATUS_PATH
    if not target.exists():
        return None
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    known = {f.name for f in fields(RunStatus)}
    return RunStatus(**{k: v for k, v in raw.items() if k in known})


def clear(path: Optional[Path] = None) -> None:
    try:
        (path or STATUS_PATH).unlink(missing_ok=True)
    except OSError:
        pass
