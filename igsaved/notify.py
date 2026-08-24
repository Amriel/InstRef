"""Ненав'язливе сповіщення з фонового запуску.

Задача з планувальника працює без вікна, тож про збій інакше можна дізнатись
лише з логу. Показуємо системне вікно, яке саме закривається, щоб задача
не «зависла» в очікуванні кліку.
"""

from __future__ import annotations

import sys

MB_OK = 0x0
MB_ICONWARNING = 0x30
MB_SETFOREGROUND = 0x10000
MB_TOPMOST = 0x40000


def popup(title: str, message: str, seconds: int = 25) -> bool:
    """Показує вікно, що зникає само. Повертає True, якщо вдалося показати."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        flags = MB_OK | MB_ICONWARNING | MB_SETFOREGROUND | MB_TOPMOST
        timed = getattr(user32, "MessageBoxTimeoutW", None)
        if timed is not None:
            timed(None, str(message), str(title), flags, 0, int(seconds) * 1000)
            return True
        # Запасний варіант — без автозакриття не показуємо взагалі,
        # щоб задача планувальника не висіла до ранку.
        return False
    except Exception:  # noqa: BLE001 — сповіщення ніколи не має ламати запуск
        return False
