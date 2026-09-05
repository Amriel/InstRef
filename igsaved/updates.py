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
    assets = []
    for asset in data.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        assets.append({
            "name": str(asset.get("name") or ""),
            "url": str(asset.get("browser_download_url") or ""),
            "size": int(asset.get("size") or 0),
        })
    return {
        "version": str(data.get("tag_name") or "").lstrip("v"),
        "tag": str(data.get("tag_name") or ""),
        "url": str(data.get("html_url") or RELEASES_URL),
        "name": str(data.get("name") or ""),
        "notes": str(data.get("body") or ""),
        "zipball": str(data.get("zipball_url") or ""),
        "assets": assets,
    }


def installer_asset(latest: dict) -> Optional[dict]:
    """Інсталятор у релізі — те, що качає й запускає зібраний застосунок."""
    for asset in (latest or {}).get("assets") or []:
        name = asset.get("name", "").lower()
        if name.startswith("instref-setup") and name.endswith(".exe"):
            return asset
    return None


def plain_notes(markdown: str) -> str:
    """Нотатки релізу без розмітки: жирного, заголовків, <details>."""
    text = str(markdown or "")
    # Коміти під спойлером — для GitHub; людині в застосунку вони не потрібні.
    text = re.sub(r"<details>.*?</details>", "", text, flags=re.S)
    text = re.sub(r"^Full diff:.*$", "", text, flags=re.M)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
    text = text.replace("**", "").replace("`", "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def check(current: str, timeout: int = 6) -> Optional[dict]:
    """Нова версія, якщо є: {'version', 'url', 'name'}; інакше None."""
    latest = fetch_latest(timeout)
    if latest and is_newer(latest["version"], current):
        return latest
    return None
