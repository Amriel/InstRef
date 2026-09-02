"""Міжпроцесний замок: один прохід синхронізації за раз.

Вікно застосунку і завдання планувальника — два різні процеси з однією базою
й одним акаунтом Instagram. Два проходи одночасно — це подвійні запити до
Instagram (саме те, за що приходить попередження про автоматизацію) і два
записи в state.db наввипередки. Замок — файл із pid у теці застосунку;
живість власника перевіряється, тож після краху файл не блокує назавжди.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional


class LockHeld(RuntimeError):
    """Інший процес уже виконує прохід."""


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            SYNCHRONIZE = 0x00100000
            handle = kernel32.OpenProcess(SYNCHRONIZE, False, int(pid))
            if not handle:
                return False
            kernel32.CloseHandle(handle)
            return True
        except Exception:  # noqa: BLE001 — краще вважати живим, ніж затерти чужий замок
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class RunLock:
    """`with RunLock(path): ...` — або LockHeld, якщо прохід уже йде."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._held = False

    def owner(self) -> Optional[int]:
        """pid процесу, що тримає замок, або None, якщо замок вільний/мертвий."""
        try:
            raw = self.path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        try:
            pid = int(raw.split()[0])
        except (ValueError, IndexError):
            return None
        return pid if _pid_alive(pid) else None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _attempt in range(2):
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                owner = self.owner()
                if owner is not None:
                    raise LockHeld(
                        f"Інший прохід уже виконується (процес {owner}). "
                        "Дочекайся його завершення."
                    ) from None
                # Залишок після краху — прибираємо і пробуємо ще раз.
                try:
                    self.path.unlink()
                except OSError:
                    pass
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(f"{os.getpid()}\n")
            self._held = True
            return
        raise LockHeld("Не вдалось зайняти замок синхронізації.")

    def release(self) -> None:
        if not self._held:
            return
        self._held = False
        try:
            self.path.unlink()
        except OSError:
            pass

    def __enter__(self) -> "RunLock":
        self.acquire()
        return self

    def __exit__(self, *_exc) -> None:
        self.release()
