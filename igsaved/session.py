"""Отримання сесії Instagram із кукі браузера (або вручну / з cookies.txt)."""

from __future__ import annotations

import http.cookiejar
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

# Порядок важливий: Firefox читається надійно, Chrome/Edge на Windows 11
# заблоковані App-Bound Encryption — тому вони наприкінці.
BROWSERS: List[str] = [
    "firefox",
    "librewolf",
    "brave",
    "vivaldi",
    "opera",
    "opera_gx",
    "chromium",
    "chrome",
    "edge",
]

BROWSER_LABELS = {
    "auto": "Автоматично (усі браузери)",
    "firefox": "Firefox",
    "librewolf": "LibreWolf",
    "brave": "Brave",
    "vivaldi": "Vivaldi",
    "opera": "Opera",
    "opera_gx": "Opera GX",
    "chromium": "Chromium",
    "chrome": "Google Chrome",
    "edge": "Microsoft Edge",
}


@dataclass
class CookieResult:
    sessionid: Optional[str]
    browser: Optional[str]
    notes: List[str]
    hint: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.sessionid)


# Повідомлення browser_cookie3 англійською й малозрозумілі — перекладаємо
# їх у щось, з чим користувач може щось зробити.
LOCKED_HINT = (
    "Chrome і Edge на Windows 11 шифрують кукі прив'язкою до застосунку "
    "(App-Bound Encryption). Це не обходиться запуском від адміністратора — "
    "sessionid доведеться скопіювати вручну, один раз."
)
NOT_INSTALLED_HINT = "Браузер не знайдено на цьому комп'ютері."


def explain(error_text: str) -> tuple[str, str]:
    """Повертає (короткий статус, довга підказка) за текстом помилки."""
    lowered = (error_text or "").lower()
    if "admin" in lowered or "decrypt" in lowered or "key for cookie" in lowered:
        return "кукі зашифровані системою", LOCKED_HINT
    if "could not find" in lowered or "failed to find" in lowered or "no such" in lowered:
        return "не встановлений", NOT_INSTALLED_HINT
    if "permission" in lowered or "denied" in lowered:
        return "немає доступу до файлу кукі", (
            "Закрий браузер і спробуй ще раз — файл кукі може бути зайнятий."
        )
    if "locked" in lowered or "database is locked" in lowered:
        return "база кукі зайнята", "Закрий браузер і спробуй ще раз."
    return error_text, ""


def _extract(browser: str) -> Optional[str]:
    import browser_cookie3 as bc3

    getter: Callable = getattr(bc3, browser)
    jar = getter(domain_name="instagram.com")
    for cookie in jar:
        if cookie.name == "sessionid" and "instagram.com" in (cookie.domain or ""):
            return cookie.value
    return None


def find_sessionid(browser: str = "auto") -> CookieResult:
    """Шукає cookie `sessionid` для instagram.com у встановлених браузерах."""
    notes: List[str] = []
    hints: List[str] = []
    try:
        import browser_cookie3  # noqa: F401
    except ImportError:
        return CookieResult(
            None, None,
            ["Не встановлено пакет browser-cookie3."],
            "Перевстанови залежності: запусти install.bat.",
        )

    candidates = BROWSERS if browser in ("", "auto", None) else [browser]
    for name in candidates:
        label = BROWSER_LABELS.get(name, name)
        try:
            value = _extract(name)
        except Exception as exc:  # noqa: BLE001 — діагностика для користувача
            status, hint = explain(_short(exc))
            notes.append(f"{label}: {status}")
            if hint and hint not in hints:
                hints.append(hint)
            continue
        if value:
            notes.append(f"{label}: знайдено sessionid ✓")
            return CookieResult(value, name, notes)
        notes.append(f"{label}: кукі є, але ти не залогінений в Instagram")

    if not hints:
        hints.append(
            "Жоден браузер не віддав сесію — скопіюй sessionid вручну "
            "за інструкцією нижче."
        )
    return CookieResult(None, None, notes, " ".join(hints))


def sessionid_from_cookies_txt(path: str | Path) -> Optional[str]:
    """Читає sessionid із файлу cookies.txt у форматі Netscape."""
    jar = http.cookiejar.MozillaCookieJar()
    jar.load(str(path), ignore_discard=True, ignore_expires=True)
    for cookie in jar:
        if cookie.name == "sessionid" and "instagram" in (cookie.domain or ""):
            return cookie.value
    return None


def normalize_sessionid(raw: str) -> str:
    """Приймає як голий sessionid, так і рядок виду `sessionid=...; other=...`."""
    value = (raw or "").strip().strip('"').strip("'")
    if "sessionid=" in value:
        part = value.split("sessionid=", 1)[1]
        value = part.split(";", 1)[0]
    return value.strip()


def _short(exc: Exception, limit: int = 120) -> str:
    text = str(exc).strip().replace("\n", " ") or exc.__class__.__name__
    return text[:limit] + ("…" if len(text) > limit else "")


MANUAL_HELP = """Chrome і Edge на Windows 11 шифрують сховище кукі ключем, прив'язаним
до самого браузера (App-Bound Encryption), тож автоматично прочитати сесію не вийде —
і запуск від адміністратора тут не допомагає. Скопіюй sessionid вручну, це разова дія:

1. Відкрий instagram.com у браузері, де ти залогінений.
2. F12 → вкладка Application (Chrome, Edge) або Storage (Firefox).
3. Ліворуч: Cookies → https://www.instagram.com
4. Знайди рядок sessionid і скопіюй значення з колонки Value.
5. Встав його в поле нижче й натисни «Перевірити».

Значення довге й виглядає приблизно так: 12345678%3AAbCdEf…%3A17%3AAYc…
Можна вставляти і цілим рядком «sessionid=…;» — зайве буде відрізано.
Сесія діє, доки ти не вийдеш з акаунта в тому браузері; після цього фонові
запуски за розкладом працюють самі."""
