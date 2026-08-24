"""Завантаження файлів із CDN Instagram: потокове, з ретраями та .part-файлами."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


@dataclass
class DownloadResult:
    path: Path
    size: int
    skipped: bool = False


class DownloadError(RuntimeError):
    pass


class TooLarge(DownloadError):
    """Файл більший за встановлений користувачем ліміт — пропускаємо без ретраїв."""


class Downloader:
    def __init__(self, timeout: int = 60, retries: int = 3, pause: float = 0.4,
                 proxy: str = "", max_bytes: int = 0):
        self.timeout = timeout
        self.retries = max(1, int(retries))
        self.pause = pause
        self.max_bytes = max(0, int(max_bytes or 0))
        self.session = requests.Session()
        if proxy:
            self.session.proxies.update({"http": proxy, "https": proxy})
        self.session.headers.update(
            {
                "User-Agent": UA,
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.instagram.com/",
            }
        )

    def close(self) -> None:
        self.session.close()

    def fetch(
        self,
        url: str,
        dest: Path,
        on_progress: Optional[Callable[[int, int], None]] = None,
        overwrite: bool = False,
    ) -> DownloadResult:
        dest = Path(dest)
        if dest.exists() and dest.stat().st_size > 0 and not overwrite:
            return DownloadResult(dest, dest.stat().st_size, skipped=True)

        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(dest.suffix + ".part")
        last_error: Optional[Exception] = None

        for attempt in range(1, self.retries + 1):
            try:
                with self.session.get(url, stream=True, timeout=self.timeout) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("Content-Length") or 0)
                    if self.max_bytes and total and total > self.max_bytes:
                        raise TooLarge(
                            f"{human_size(total)} > ліміту {human_size(self.max_bytes)}"
                        )
                    written = 0
                    with open(part, "wb") as handle:
                        for chunk in resp.iter_content(chunk_size=256 * 1024):
                            if not chunk:
                                continue
                            handle.write(chunk)
                            written += len(chunk)
                            if on_progress:
                                on_progress(written, total)
                if written == 0:
                    raise DownloadError("Порожня відповідь від CDN")
                part.replace(dest)
                if self.pause:
                    time.sleep(self.pause)
                return DownloadResult(dest, written)
            except TooLarge:
                part.unlink(missing_ok=True)
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                part.unlink(missing_ok=True)
                if attempt < self.retries:
                    time.sleep(min(2 ** attempt, 8))

        raise DownloadError(f"{url[:80]}… → {last_error}")


def human_size(num: float) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if abs(num) < 1024 or unit == "ГБ":
            return f"{num:,.0f} {unit}" if unit == "Б" else f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} ГБ"
