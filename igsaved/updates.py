"""Перевірка нової версії через GitHub Releases.

Інсталятор є — а от дізнатись про новий реліз інакше нізвідки. Раз на добу
один GET до api.github.com; відповідь — версія й адреса релізу, або None.
Мережеві збої мовчазні: перевірка оновлень не має ламати запуск.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

import requests

REPO = "Amriel/InstRef"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases"


def parse_version(text: str) -> Tuple[int, ...]:
    """«v2.3.0» → (2, 3, 0). Усе нечислове ігнорується."""
    numbers = re.findall(r"\d+", str(text or ""))
    return tuple(int(n) for n in numbers[:4]) or (0,)


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def fetch_latest(timeout: int = 6) -> Optional[dict]:
    try:
        resp = requests.get(API, timeout=timeout,
                            headers={"Accept": "application/vnd.github+json",
                                     "User-Agent": "InstRef"})
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("tag_name"):
        return None
    return {
        "version": str(data.get("tag_name") or "").lstrip("v"),
        "url": str(data.get("html_url") or RELEASES_URL),
        "name": str(data.get("name") or ""),
    }


def check(current: str, timeout: int = 6) -> Optional[dict]:
    """Нова версія, якщо є: {'version', 'url', 'name'}; інакше None."""
    latest = fetch_latest(timeout)
    if latest and is_newer(latest["version"], current):
        return latest
    return None
