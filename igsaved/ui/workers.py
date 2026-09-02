"""Фонові потоки, щоб інтерфейс не підвисав під час запитів до Instagram."""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QThread, Signal

from ..config import Config, DEVICE_PATH
from ..instagram import IGClient, InstagramError
from ..state import State
from ..sync import SyncEngine, Stats


class ConnectWorker(QThread):
    """Перевірка sessionid: підключитись і повернути ім'я користувача."""

    done = Signal(str)      # username
    failed = Signal(str)    # текст помилки
    line = Signal(str)

    def __init__(self, sessionid: str, parent=None):
        super().__init__(parent)
        self.sessionid = sessionid

    def run(self) -> None:
        try:
            client = IGClient(device_path=DEVICE_PATH, log=self.line.emit)
            username = client.connect(self.sessionid)
            self.done.emit(username)
        except InstagramError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{exc.__class__.__name__}: {exc}")


class CookieWorker(QThread):
    """Пошук sessionid у кукі браузерів (може бути повільним)."""

    done = Signal(object)  # CookieResult

    def __init__(self, browser: str, parent=None):
        super().__init__(parent)
        self.browser = browser

    def run(self) -> None:
        from ..session import find_sessionid

        self.done.emit(find_sessionid(self.browser))


class CollectionsWorker(QThread):
    """Отримати список підбірок."""

    done = Signal(object)   # List[CollectionInfo]
    failed = Signal(str)
    line = Signal(str)

    def __init__(self, sessionid: str, parent=None):
        super().__init__(parent)
        self.sessionid = sessionid

    def run(self) -> None:
        try:
            client = IGClient(device_path=DEVICE_PATH, log=self.line.emit)
            client.connect(self.sessionid)
            self.done.emit(client.list_collections())
        except InstagramError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{exc.__class__.__name__}: {exc}")


class RefreshWorker(QThread):
    """Перейменування вже завантажених файлів і дозапис тегів."""

    line = Signal(str)
    done = Signal(object)  # RefreshStats

    def __init__(self, cfg: Config, state: State, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.state = state
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        from ..maintenance import refresh_library

        stats = refresh_library(
            self.cfg, self.state,
            log=self.line.emit,
            should_stop=lambda: self._stop,
        )
        self.done.emit(stats)


class PushWorker(QThread):
    """Дозалив уже завантаженої бібліотеки в Eagle."""

    line = Signal(str)
    done = Signal(object)  # PushStats

    def __init__(self, cfg: Config, state: State, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.state = state
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        from ..maintenance import push_to_eagle

        self.done.emit(push_to_eagle(
            self.cfg, self.state, log=self.line.emit, should_stop=lambda: self._stop))


class DescribeWorker(QThread):
    """Описи й теги для того, що вже лежить у бібліотеці Eagle."""

    line = Signal(str)
    done = Signal(object)  # DescribeStats

    def __init__(self, cfg: Config, state: State, limit: int = 0,
                 redo: bool = False, only_stale: bool = False,
                 model_override: str = "", parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.state = state
        self.limit = limit
        self.redo = redo
        self.only_stale = only_stale
        self.model_override = model_override
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        from ..maintenance import describe_library

        self.done.emit(describe_library(
            self.cfg, self.state, log=self.line.emit,
            should_stop=lambda: self._stop, limit=self.limit, redo=self.redo,
            only_stale=self.only_stale, model_override=self.model_override))


class NormalizeWorker(QThread):
    """Ретро-нормалізація тегів за словником — база й Eagle, без моделі."""

    line = Signal(str)
    done = Signal(object)  # NormalizeStats

    def __init__(self, cfg: Config, state: State, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.state = state
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        from ..maintenance import normalize_library

        self.done.emit(normalize_library(
            self.cfg, self.state, log=self.line.emit, should_stop=lambda: self._stop))


class DupeWorker(QThread):
    """Пошук (і за вказівкою — прибирання) дублікатів у бібліотеці Eagle."""

    line = Signal(str)
    done = Signal(object)  # DupeStats

    def __init__(self, cfg: Config, state: State, remove: bool = False, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.state = state
        self.remove = remove
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        from ..maintenance import find_eagle_duplicates

        self.done.emit(find_eagle_duplicates(
            self.cfg, self.state, log=self.line.emit, remove=self.remove,
            should_stop=lambda: self._stop))


class CleanupWorker(QThread):
    """Чистка папки завантажень зі збереженням памʼяті про завантажене."""

    line = Signal(str)
    done = Signal(object)  # CleanupStats

    def __init__(self, cfg: Config, state: State, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.state = state
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        from ..maintenance import clear_downloads

        self.done.emit(clear_downloads(
            self.cfg, self.state, log=self.line.emit, should_stop=lambda: self._stop))


class SyncWorker(QThread):
    """Повний прохід синхронізації."""

    line = Signal(str)
    progress = Signal(str, int, int)
    done = Signal(object)  # Stats

    def __init__(self, cfg: Config, state: State, sessionid: str,
                 collections: Optional[List[str]] = None, force: bool = False,
                 parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.state = state
        self.sessionid = sessionid
        self.collections = collections
        self.force = force
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        engine = SyncEngine(
            self.cfg,
            self.state,
            self.sessionid,
            log=self.line.emit,
            progress=lambda msg, cur, total: self.progress.emit(msg, cur, total),
            should_stop=lambda: self._stop,
        )
        stats: Stats = engine.run(only_collections=self.collections, force=self.force)
        self.done.emit(stats)
