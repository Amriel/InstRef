"""Автозапуск: задача у Планувальнику Windows + запуск застосунку разом із системою."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from .config import (
    FROZEN, SCHED_DAILY, SCHED_HOURLY, SCHED_ONLOGON, SCHED_WEEKLY, app_dir,
    resource_dir,
)

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "InstRef"
# Значення реєстру до перейменування — прибираємо, щоб не було двох автостартів.
LEGACY_RUN_VALUE = "IG Saved Sync"


@dataclass
class TaskResult:
    ok: bool
    message: str


def _run(args: List[str]) -> Tuple[int, str]:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="cp866" if sys.platform == "win32" else "utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
        )
    except FileNotFoundError:
        return 1, "schtasks не знайдено — автозапуск доступний лише у Windows."
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def _pythonw() -> Path:
    candidate = Path(sys.executable).with_name("pythonw.exe")
    return candidate if candidate.exists() else Path(sys.executable)


def sync_command() -> str:
    """Команда для планувальника: тихий запуск без вікна консолі."""
    if FROZEN:
        # У зібраній версії той самий .exe вміє і GUI, і тиху синхронізацію.
        return f'"{Path(sys.executable)}" --sync --quiet'
    script = resource_dir() / "sync.pyw"
    if script.exists():
        return f'"{_pythonw()}" "{script}"'
    bat = resource_dir() / "sync.bat"
    if bat.exists():
        return f'"{bat}"'
    return f'"{_pythonw()}" -m igsaved.cli --sync --quiet'


def app_command() -> str:
    """Команда для запуску самого застосунку (автостарт із Windows)."""
    if FROZEN:
        return f'"{Path(sys.executable)}"'
    script = resource_dir() / "app.pyw"
    if script.exists():
        return f'"{_pythonw()}" "{script}"'
    return f'"{resource_dir() / "InstRef.bat"}"'


def _drop_legacy_task(new_name: str) -> None:
    """Прибирає завдання, створене під старою назвою застосунку.

    Без цього після перейменування в планувальнику лишалося б два завдання, і
    синхронізація ходила б двічі. Мовчки: якщо його немає — і добре.
    """
    from .config import LEGACY_TASK_NAME

    if new_name == LEGACY_TASK_NAME:
        return
    _run(["schtasks", "/Delete", "/F", "/TN", LEGACY_TASK_NAME])


# ---------------------------------------------------------------- планувальник
def create_task(
    task_name: str,
    mode: str = SCHED_DAILY,
    time_hhmm: str = "09:00",
    interval_hours: int = 6,
    weekday: str = "MON",
) -> TaskResult:
    """Створює або переписує задачу синхронізації."""
    if sys.platform != "win32":
        return TaskResult(False, "Планувальник завдань доступний лише у Windows.")

    _drop_legacy_task(task_name)
    args = ["schtasks", "/Create", "/F", "/TN", task_name, "/TR", sync_command(), "/RL", "LIMITED"]

    if mode == SCHED_ONLOGON:
        args += ["/SC", "ONLOGON"]
        human = "при вході в Windows"
    elif mode == SCHED_HOURLY:
        hours = max(1, min(int(interval_hours or 1), 23))
        if not _valid_time(time_hhmm):
            time_hhmm = "09:00"
        args += ["/SC", "HOURLY", "/MO", str(hours), "/ST", time_hhmm]
        human = f"кожні {hours} год (перший запуск о {time_hhmm})"
    elif mode == SCHED_WEEKLY:
        if not _valid_time(time_hhmm):
            return TaskResult(False, "Час має бути у форматі ГГ:ХХ, напр. 09:30.")
        args += ["/SC", "WEEKLY", "/D", (weekday or "MON").upper(), "/ST", time_hhmm]
        human = f"щотижня ({weekday}) о {time_hhmm}"
    else:
        if not _valid_time(time_hhmm):
            return TaskResult(False, "Час має бути у форматі ГГ:ХХ, напр. 09:30.")
        args += ["/SC", "DAILY", "/ST", time_hhmm]
        human = f"щодня о {time_hhmm}"

    code, out = _run(args)
    if code == 0:
        return TaskResult(True, f"Задачу «{task_name}» увімкнено: {human}.")
    return TaskResult(False, out or "schtasks повернув помилку.")


def create_daily(task_name: str, time_hhmm: str) -> TaskResult:
    """Сумісність зі старим викликом."""
    return create_task(task_name, SCHED_DAILY, time_hhmm)


def delete(task_name: str) -> TaskResult:
    if sys.platform != "win32":
        return TaskResult(False, "Планувальник завдань доступний лише у Windows.")
    code, out = _run(["schtasks", "/Delete", "/F", "/TN", task_name])
    if code == 0:
        return TaskResult(True, f"Задачу «{task_name}» вимкнено.")
    if "cannot find" in out.lower() or "не найден" in out.lower() or "не знайд" in out.lower():
        return TaskResult(True, "Задачі не було — вимикати нічого.")
    return TaskResult(False, out or "schtasks повернув помилку.")


def run_now(task_name: str) -> TaskResult:
    if sys.platform != "win32":
        return TaskResult(False, "Доступно лише у Windows.")
    code, out = _run(["schtasks", "/Run", "/TN", task_name])
    return TaskResult(code == 0, "Задачу запущено." if code == 0 else (out or "Помилка запуску."))


def status(task_name: str) -> Optional[str]:
    """Короткий стан задачі або None, якщо її немає."""
    if sys.platform != "win32":
        return None
    code, out = _run(["schtasks", "/Query", "/TN", task_name, "/FO", "LIST"])
    if code != 0:
        return None
    wanted = ("Next Run Time", "Наступний запуск", "Следующий запуск", "Status", "Стан", "Состояние")
    picked = []
    for line in out.splitlines():
        line = line.strip()
        if ":" in line and line.split(":")[0].strip() in wanted:
            picked.append(line)
    return " · ".join(picked) if picked else "зареєстровано"


def _valid_time(value: str) -> bool:
    parts = (value or "").split(":")
    if len(parts) != 2:
        return False
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    return 0 <= hour <= 23 and 0 <= minute <= 59


# ------------------------------------------------------- автостарт застосунку
def set_run_at_login(enabled: bool) -> TaskResult:
    """Додає/прибирає застосунок з автозапуску Windows (гілка HKCU\\...\\Run)."""
    if sys.platform != "win32":
        return TaskResult(False, "Доступно лише у Windows.")
    try:
        import winreg
    except ImportError:  # pragma: no cover
        return TaskResult(False, "Немає доступу до реєстру.")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_ALL_ACCESS) as key:
            if enabled:
                winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, app_command())
                try:      # старий запис під колишньою назвою більше не потрібен
                    winreg.DeleteValue(key, LEGACY_RUN_VALUE)
                except FileNotFoundError:
                    pass
                return TaskResult(True, "Застосунок запускатиметься разом із Windows.")
            for value in (RUN_VALUE, LEGACY_RUN_VALUE):
                try:
                    winreg.DeleteValue(key, value)
                except FileNotFoundError:
                    pass
            return TaskResult(True, "Автозапуск застосунку вимкнено.")
    except OSError as exc:
        return TaskResult(False, f"Не вдалось змінити реєстр: {exc}")


def create_shortcut(on_desktop: bool = True) -> TaskResult:
    """Робить ярлик із нормальною іконкою — щоб не запускати через .bat."""
    if sys.platform != "win32":
        return TaskResult(False, "Ярлики доступні лише у Windows.")

    root = resource_dir()
    icon = root / "assets" / "icon.ico"
    if FROZEN:
        # Встановлена версія має власний .exe — ані Python, ані скрипта не треба.
        target = Path(sys.executable)
        script = None
    else:
        target = _pythonw()
        script = root / "app.pyw"
        if not script.exists():
            return TaskResult(False, "Не знайдено app.pyw — онови застосунок.")
        if not target.exists():
            return TaskResult(
                False, "Не знайдено pythonw.exe — спершу запусти install.bat.")

    arguments = "" if script is None else f'"{script}"'
    where = "[Environment]::GetFolderPath('Desktop')" if on_desktop else \
        "[Environment]::GetFolderPath('StartMenu')"
    script_ps = (
        f"$dir = {where}; "
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut("
        f"(Join-Path $dir 'InstRef.lnk')); "
        f"$s.TargetPath = '{target}'; "
        f"$s.Arguments = '{arguments}'; "
        f"$s.WorkingDirectory = '{root}'; "
        f"$s.IconLocation = '{icon}'; "
        f"$s.Description = 'InstRef'; "
        f"$s.Save()"
    )
    code, out = _run([
        "powershell", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-Command", script_ps,
    ])
    if code == 0:
        return TaskResult(True, "Ярлик створено на робочому столі.")
    return TaskResult(False, out or "PowerShell не зміг створити ярлик.")


def is_run_at_login() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, RUN_VALUE)
            return bool(value)
    except (ImportError, OSError):
        return False
