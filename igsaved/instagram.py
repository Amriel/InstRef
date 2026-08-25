"""Клієнт Instagram поверх instagrapi: підбірки та посторінковий обхід збережених."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, List, Optional, Tuple

from .config import ALL_POSTS_NAME, ALL_POSTS_PK, LIKED_NAME, LIKED_PK

Logger = Callable[[str], None]


def _short(exc: Exception, limit: int = 80) -> str:
    text = str(exc).strip().replace("\n", " ") or exc.__class__.__name__
    return text[:limit] + ("…" if len(text) > limit else "")


@dataclass
class CollectionInfo:
    pk: str
    name: str
    media_count: int
    is_all_saved: bool = False
    is_liked: bool = False

    @property
    def display(self) -> str:
        if self.is_all_saved:
            return "Усі збережені"
        return self.name


class InstagramError(RuntimeError):
    """Помилка, яку варто показати користувачу як є."""


class IGClient:
    """Обгортка над instagrapi.Client із логуванням та ввічливими паузами."""

    def __init__(
        self,
        device_path: Optional[Path] = None,
        delay_range: Tuple[float, float] = (2.0, 5.0),
        log: Logger = lambda _msg: None,
        proxy: str = "",
    ):
        self.device_path = Path(device_path) if device_path else None
        self.delay_range = delay_range
        self.log = log
        self.proxy = (proxy or "").strip()
        self._client = None
        self.username: Optional[str] = None

    # ------------------------------------------------------------ підключення
    @property
    def client(self):
        if self._client is None:
            try:
                from instagrapi import Client
            except ImportError as exc:  # pragma: no cover
                raise InstagramError(
                    "Не встановлено instagrapi. Запусти install.bat або "
                    "`pip install -r requirements.txt`."
                ) from exc
            client = Client()
            client.delay_range = [float(self.delay_range[0]), float(self.delay_range[1])]
            if self.proxy:
                try:
                    client.set_proxy(self.proxy)
                    self.log("Проксі застосовано.")
                except Exception as exc:  # noqa: BLE001
                    self.log(f"Проксі проігноровано: {exc}")
            # Стабільний «пристрій» між запусками — Instagram менше нервує.
            if self.device_path and self.device_path.exists():
                try:
                    client.load_settings(self.device_path)
                    self.log("Профіль пристрою відновлено.")
                except Exception as exc:  # noqa: BLE001
                    self.log(f"Не вдалось прочитати device.json ({exc}); буде створено новий.")
            self._client = client
        return self._client

    def connect(self, sessionid: str) -> str:
        """Підключення за cookie sessionid. Повертає ім'я користувача.

        Спершу пробуємо відновлену сесію з device.json. `login_by_sessionid`
        робить кілька запитів і виглядає як свіжий вхід; десяток таких входів
        на добу — найпомітніший слід автоматизації, і саме за нього приходить
        попередження від Instagram. Якщо збережена сесія жива, вистачає одного
        дешевого запиту замість повного входу.
        """
        sessionid = (sessionid or "").strip()
        if not sessionid:
            raise InstagramError("Порожній sessionid — спершу підключи сесію на вкладці «Сесія».")

        reused = self._reuse_session(sessionid)
        if reused:
            return reused

        try:
            ok = self.client.login_by_sessionid(sessionid)
        except Exception as exc:  # noqa: BLE001
            raise InstagramError(f"Instagram відхилив сесію: {exc}") from exc
        if not ok:
            raise InstagramError("Instagram не прийняв sessionid. Онови його на вкладці «Сесія».")
        try:
            self.username = self.client.account_info().username
        except Exception:  # noqa: BLE001 — не критично
            self.username = self.client.username or "?"
        self._persist_device()
        self.log(f"Підключено як @{self.username}")
        return self.username

    def _reuse_session(self, sessionid: str) -> Optional[str]:
        """Пробує обійтись збереженою сесією. None — треба повний вхід."""
        try:
            settings = self.client.get_settings() or {}
        except Exception:  # noqa: BLE001
            return None
        saved = str((settings.get("cookies") or {}).get("sessionid") or "")
        if not saved or saved.split("%3A")[0] != sessionid.split("%3A")[0]:
            # Інший акаунт або порожній профіль — тільки повний вхід.
            return None
        if not settings.get("authorization_data"):
            return None
        try:
            info = self.client.account_info()
        except Exception as exc:  # noqa: BLE001 — сесія протухла, це не помилка
            self.log(f"Збережена сесія не підійшла ({_short(exc)}) — входжу заново.")
            return None
        self.username = getattr(info, "username", None) or self.client.username or "?"
        self.log(f"Сесію відновлено з профілю — вхід не потрібен. @{self.username}")
        return self.username

    def _persist_device(self) -> None:
        if not self.device_path:
            return
        try:
            self.device_path.parent.mkdir(parents=True, exist_ok=True)
            self.client.dump_settings(self.device_path)
        except Exception as exc:  # noqa: BLE001
            self.log(f"Не вдалось зберегти профіль пристрою: {exc}")

    # -------------------------------------------------------------- підбірки
    def list_collections(self) -> List[CollectionInfo]:
        """Усі підбірки + псевдо-підбірка «Усі збережені» першим елементом."""
        result: List[CollectionInfo] = [
            CollectionInfo(ALL_POSTS_PK, ALL_POSTS_NAME, 0, is_all_saved=True),
            CollectionInfo(LIKED_PK, LIKED_NAME, 0, is_liked=True),
        ]
        try:
            raw = self.client.collections()
        except Exception as exc:  # noqa: BLE001
            self.log(f"Не вдалось отримати список підбірок: {exc}")
            return result

        for item in raw or []:
            pk = str(getattr(item, "id", "") or "")
            name = getattr(item, "name", "") or ""
            count = int(getattr(item, "media_count", 0) or 0)
            col_type = (getattr(item, "type", "") or "").upper()
            if not pk:
                continue
            if pk == ALL_POSTS_PK or col_type == "ALL_MEDIA_AUTO_COLLECTION":
                result[0].media_count = count
                continue
            if pk == LIKED_PK:
                continue
            if col_type == "PRODUCT_AUTO_COLLECTION":
                continue  # збережені товари — не медіа
            result.append(CollectionInfo(pk, name, count))
        self.log(f"Знайдено підбірок: {len(result) - 2} (+ збережені й пролайкане)")
        return result

    # -------------------------------------------------------- обхід медіа
    def iter_media(
        self,
        collection_pk: str,
        should_stop: Callable[[], bool] = lambda: False,
        on_page: Callable[[int, int], None] = lambda page, count: None,
    ) -> Iterator:
        """Генератор об'єктів Media з посторінковим обходом підбірки."""
        cursor = ""
        page = 0
        empty_pages = 0
        while True:
            if should_stop():
                return
            try:
                items, cursor = self.client.collection_medias_v1_chunk(
                    str(collection_pk), max_id=cursor
                )
            except Exception as exc:  # noqa: BLE001
                raise InstagramError(f"Помилка запиту до Instagram: {exc}") from exc

            page += 1
            on_page(page, len(items))

            if not items:
                empty_pages += 1
                if empty_pages >= 2 or not cursor:
                    return
            else:
                empty_pages = 0
                for media in items:
                    if should_stop():
                        return
                    yield media

            if not cursor:
                return
            self.pause()

    def pause(self) -> None:
        low, high = self.delay_range
        time.sleep(random.uniform(float(low), float(high)))


# ---------------------------------------------------------------------------
# Розбір Media -> перелік ассетів для завантаження
# ---------------------------------------------------------------------------
@dataclass
class Asset:
    url: str
    kind: str  # video | photo | thumb
    index: Optional[int]  # None для одиночного поста, 1..N для каруселі


def media_url(code: str) -> str:
    return f"https://www.instagram.com/p/{code}/" if code else ""


def collect_assets(media, want_videos: bool, want_photos: bool, want_thumbs: bool) -> List[Asset]:
    """Перетворює Media в список файлів, які треба скачати."""
    assets: List[Asset] = []
    mtype = int(getattr(media, "media_type", 0) or 0)

    def add(url, kind, index):
        if url:
            assets.append(Asset(str(url), kind, index))

    if mtype == 8:  # карусель
        resources = list(getattr(media, "resources", []) or [])
        for position, res in enumerate(resources, start=1):
            rtype = int(getattr(res, "media_type", 1) or 1)
            if rtype == 2 and want_videos:
                add(getattr(res, "video_url", None), "video", position)
                if want_thumbs:
                    add(getattr(res, "thumbnail_url", None), "thumb", position)
            elif rtype == 1 and want_photos:
                add(getattr(res, "thumbnail_url", None), "photo", position)
    elif mtype == 2:  # відео / Reels
        if want_videos:
            add(getattr(media, "video_url", None), "video", None)
            if want_thumbs:
                add(getattr(media, "thumbnail_url", None), "thumb", None)
    elif mtype == 1:  # фото
        if want_photos:
            add(getattr(media, "thumbnail_url", None), "photo", None)
    return assets


def media_to_dict(media, collections: List[str]) -> dict:
    """Плоский словник для .json поруч із файлом."""
    taken = getattr(media, "taken_at", None)
    user = getattr(media, "user", None)
    music = getattr(media, "clips_metadata", None)
    track = None
    if music is not None:
        info = getattr(music, "music_info", None) or (music.get("music_info") if isinstance(music, dict) else None)
        asset = getattr(info, "music_asset_info", None) if info is not None else None
        if asset is not None:
            track = {
                "title": getattr(asset, "title", None),
                "artist": getattr(asset, "display_artist", None),
            }
    return {
        "pk": str(getattr(media, "pk", "")),
        "code": getattr(media, "code", ""),
        "url": media_url(getattr(media, "code", "")),
        "media_type": int(getattr(media, "media_type", 0) or 0),
        "product_type": getattr(media, "product_type", "") or "",
        "taken_at": taken.isoformat() if taken else None,
        "author": {
            "username": getattr(user, "username", None) if user else None,
            "full_name": getattr(user, "full_name", None) if user else None,
            "pk": str(getattr(user, "pk", "")) if user else None,
        },
        "caption": getattr(media, "caption_text", "") or "",
        "like_count": getattr(media, "like_count", None),
        "view_count": getattr(media, "view_count", None),
        "play_count": getattr(media, "play_count", None),
        "comment_count": getattr(media, "comment_count", None),
        "video_duration": getattr(media, "video_duration", None),
        "music": track,
        "collections": collections,
    }


def label_for(media) -> str:
    """Короткий тип для тегів Eagle: reel / video / photo / carousel."""
    mtype = int(getattr(media, "media_type", 0) or 0)
    product = (getattr(media, "product_type", "") or "").lower()
    if mtype == 8:
        return "carousel"
    if mtype == 2:
        return "reel" if product in ("clips", "reel") else "video"
    return "photo"
