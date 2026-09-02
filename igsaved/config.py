"""Налаштування застосунку: завантаження/збереження JSON + шляхи до робочих файлів."""

from __future__ import annotations

import base64
import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List


FROZEN = bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    """Де лежать файли, які поставляються разом із застосунком.

    У зібраному .exe PyInstaller кладе їх у тимчасову теку `_MEIPASS`; у
    вихідниках це просто корінь проєкту.
    """
    if FROZEN:
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def app_dir() -> Path:
    """Куди писати налаштування, базу й журнали.

    Тонкість, без якої інсталятор ламається: встановлений застосунок лежить у
    Program Files, куди звичайний користувач писати не може. Тому в зібраній
    версії робочі файли живуть у %APPDATA%\\InstRef, а в запуску з вихідників —
    поруч із кодом, як і було.
    """
    if FROZEN:
        base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME")
        if base:
            path = Path(base) / "InstRef"
            path.mkdir(parents=True, exist_ok=True)
            return path
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


CONFIG_PATH = app_dir() / "config.json"
SESSION_PATH = app_dir() / "session.json"
DEVICE_PATH = app_dir() / "device.json"
STATE_PATH = app_dir() / "state.db"
STATUS_PATH = app_dir() / "last_run.json"
LOCK_PATH = app_dir() / "sync.lock"
LOG_DIR = app_dir() / "logs"

# Псевдо-підбірка «всі збережені» — так її називає приватне API Instagram.
ALL_POSTS_PK = "ALL_MEDIA_AUTO_COLLECTION"
ALL_POSTS_NAME = "All Posts"

# Пролайкане — окремий ендпоінт приватного API, показуємо як псевдо-підбірку.
LIKED_PK = "liked"
LIKED_NAME = "Пролайкане"

STRUCTURE_FLAT = "flat"
STRUCTURE_PER_COLLECTION = "per_collection"

# Режими розкладу для Планувальника завдань Windows
SCHED_DAILY = "daily"
SCHED_HOURLY = "hourly"
SCHED_WEEKLY = "weekly"
SCHED_ONLOGON = "onlogon"

SCHEDULE_LABELS = {
    SCHED_DAILY: "Щодня",
    SCHED_HOURLY: "Кожні N годин",
    SCHED_WEEKLY: "Щотижня",
    SCHED_ONLOGON: "При вході в Windows",
}

WEEKDAYS = [
    ("MON", "Понеділок"), ("TUE", "Вівторок"), ("WED", "Середа"),
    ("THU", "Четвер"), ("FRI", "П'ятниця"), ("SAT", "Субота"), ("SUN", "Неділя"),
]

DEFAULT_TEMPLATE = "{date}_{user}_{title}"
# Шаблони, які були типовими в попередніх версіях. Якщо в конфізі стоїть саме
# такий — користувач його не змінював, і можна тихо підняти до нового.
LEGACY_TEMPLATES = {"{date}_{user}_{code}"}
# Назва завдання планувальника до перейменування застосунку на InstRef.
LEGACY_TASK_NAME = "IG Saved Sync"
# Типовий таймаут моделі до того, як вона стала дивитись кілька кадрів.
LEGACY_VISION_TIMEOUT = 60
# Паузи, що були типовими до попередження Instagram про автоматизацію.
LEGACY_DELAYS = (2.0, 5.0)
LEGACY_DOWNLOAD_DELAY = 0.4
TEMPLATE_TOKENS = {
    "{title}": "початок підпису без хештегів і емодзі (якщо тексту нема — код поста)",
    "{user}": "автор без @",
    "{date}": "дата поста, 2026-08-20",
    "{time}": "час поста, 14-05",
    "{code}": "код поста в Instagram",
    "{type}": "reel / video / photo / carousel",
    "{id}": "числовий id поста",
    "{collection}": "назва підбірки",
}


@dataclass
class Config:
    # --- куди качати ---
    download_dir: str = str(app_dir() / "downloads")
    structure: str = STRUCTURE_FLAT  # flat | per_collection

    filename_template: str = DEFAULT_TEMPLATE

    # --- що качати ---
    download_videos: bool = True
    download_photos: bool = True
    download_thumbnails: bool = True
    embed_metadata: bool = True   # опис, автор і посилання — всередину самого файлу
    write_metadata: bool = False  # додатково окремим .json (вимкнено типово)
    skip_larger_than_mb: int = 0  # 0 = без обмеження

    # --- які підбірки ---
    sync_all_saved: bool = True
    sync_liked: bool = False
    enabled_collections: List[str] = field(default_factory=list)  # pk обраних підбірок
    sync_all_collections: bool = True  # True = усі підбірки, ігноруючи список вище

    # --- режим сканування ---
    incremental: bool = True
    # Скільки найсвіжіших постів переглядати у збережених і підбірках.
    # 0 = усі: збережене скінченне й куроване, тож типово обмеження не треба.
    scan_limit: int = 0
    stop_after_known: int = 40  # стоп після N поспіль уже завантажених
    max_items_per_run: int = 0  # 0 = без ліміту
    # Паузи між сторінками — головний захист акаунта. Instagram позначає як
    # автоматизацію не обсяг, а темп: рівні короткі інтервали виглядають як
    # скрипт, бо людина так не гортає.
    page_delay_min: float = 8.0
    page_delay_max: float = 15.0
    download_delay: float = 1.0
    request_timeout: int = 60
    max_retries: int = 3
    # Мінімум годин між проходами. Кожен прохід — це логін і десятки запитів;
    # кілька проходів поспіль з різницею у хвилини — найпомітніший слід
    # автоматизації. 0 = без обмеження (не радимо).
    min_hours_between_runs: float = 6.0
    # Instagram відповів «зачекай кілька хвилин» / 429: зупиняємо ВЕСЬ прохід
    # і не повертаємось стільки годин. Продовжувати після такої відповіді —
    # найкоротший шлях до блокування.
    rate_limit_cooldown_hours: float = 24.0
    # Після стількох невдалих спроб пост більше не чіпаємо (gave_up).
    max_post_attempts: int = 3
    proxy: str = ""  # напр. http://user:pass@host:port

    # --- Eagle ---
    eagle_enabled: bool = True
    eagle_url: str = "http://localhost:41595"
    eagle_token: str = ""
    eagle_root_folder: str = "Instagram Saved"
    eagle_folder_per_collection: bool = True
    # Один пост — один елемент у Eagle. Пост часто лежить і в збережених, і в
    # лайках, і в іменованій підбірці; Eagle на кожен імпорт КОПІЮЄ файл, тож
    # без цього той самий ролик з'являється в бібліотеці двічі-тричі.
    eagle_one_item_per_post: bool = True
    # Прибирати локальні копії після ПІДТВЕРДЖЕНОГО імпорту. Eagle зберігає
    # власну копію кожного файлу, тож папка завантажень — лише перевалка.
    # «Підтвердженого» — ключове слово: видаляється тільки те, що реально
    # видно в бібліотеці, а не те, що ми лише відправили (копіювання на боці
    # Eagle асинхронне). Типово вимкнено: видалення вмикають свідомо.
    eagle_delete_local_after_import: bool = False
    eagle_import_thumbnails: bool = False
    eagle_tags_from_hashtags: bool = True
    eagle_tag_collection: bool = True
    eagle_tag_author: bool = True
    eagle_extra_tags: List[str] = field(default_factory=lambda: ["instagram"])

    # --- розклад / автозапуск ---
    schedule_enabled: bool = False
    schedule_mode: str = SCHED_DAILY
    schedule_time: str = "09:00"
    schedule_interval_hours: int = 6
    schedule_weekday: str = "MON"
    schedule_task_name: str = "InstRef Sync"
    # Випадковий зсув перед плановим проходом: щодня рівно о 09:00:00 — це
    # підпис скрипта, людина так не заходить.
    schedule_jitter_minutes: int = 20
    run_on_windows_start: bool = False
    sync_on_launch: bool = False
    minimize_to_tray: bool = True
    start_minimized: bool = False
    notify_on_finish: bool = True

    # --- фільтр пролайканого ---
    # Працює ТІЛЬКИ для пролайканого: збережене користувач курує сам.
    classify_liked: bool = True
    # Скільки НАЙСВІЖІШИХ пролайканих переглядати за запуск. Саме переглядати,
    # а не завантажувати: інакше стрічка з самих мемів крутиться без кінця,
    # бо відсіяне не збільшує ліміт завантажень.
    liked_scan_limit: int = 50
    uncertain_action: str = "review"      # review | download | skip
    review_subfolder: str = "_review"
    block_accounts: List[str] = field(default_factory=list)
    allow_accounts: List[str] = field(default_factory=list)
    extra_meme_tags: List[str] = field(default_factory=list)
    extra_art_tags: List[str] = field(default_factory=list)
    max_meme_seconds: float = 0.0

    # --- візуальна модель (LM Studio) ---
    # Дивиться на кадри лише тих постів, які правила визнали сумнівними.
    vision_enabled: bool = False
    vision_url: str = "http://localhost:1234/v1"
    vision_model: str = ""            # порожньо = перша завантажена в LM Studio
    vision_timeout: int = 120
    vision_min_confidence: float = 0.55
    vision_skip_categories: List[str] = field(
        default_factory=lambda: ["meme", "game"]
    )
    # Скільки кадрів дістати з ролика. 1 = стара поведінка (тільки обкладинка),
    # а обкладинка reels — це часто чорний кадр або титр, за яким про ролик
    # нічого не скажеш.
    vision_frames: int = 6
    # Порожньо = вбудована інструкція (vision.DEFAULT_PROMPT). Зберігаємо саме
    # порожній рядок, щоб оновлення застосунку могло покращити типову
    # інструкцію тим, хто її не редагував.
    vision_prompt: str = ""
    # Показувати моделі КОЖЕН новий пост заради опису й тегів. Збережене
    # модель не судить — лише описує, і воно одразу йде в Eagle.
    vision_describe_downloads: bool = True
    # Рішення моделі по пролайканому спершу потрапляють в окрему вкладку:
    # файл лягає на диск, але в Eagle чекає, доки ти на нього глянеш.
    model_needs_glance: bool = True
    # Теги беруться зі словника (taxonomy.json), а все стороннє відкидається.
    # Без цього бібліотека за місяць перетворюється на купу синонімів.
    taxonomy_enabled: bool = True
    # Скільки разів модель має запропонувати тег поза словником, щоб застосунок
    # запропонував додати його свідомо.
    taxonomy_suggest_after: int = 5

    # --- сесія ---
    browser: str = "auto"

    # ------------------------------------------------------------------ IO
    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or CONFIG_PATH
        cfg = cls()
        if path.exists():
            try:
                raw: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return cfg
            known = {f.name for f in fields(cls)}
            for key, value in raw.items():
                if key in known:
                    setattr(cfg, key, value)
        cfg._migrate()
        return cfg

    def _migrate(self) -> None:
        """Плавно піднімає конфіг зі старих версій, не чіпаючи ручних змін."""
        if self.filename_template in LEGACY_TEMPLATES:
            self.filename_template = DEFAULT_TEMPLATE
        # 60 с вистачало на одну обкладинку; кілька кадрів плюс опис і теги
        # модель обробляє довше, і старий ліміт почав різати відповіді.
        if int(self.vision_timeout or 0) == LEGACY_VISION_TIMEOUT:
            self.vision_timeout = 120
        # Застосунок перейменовано; завдання з типовою старою назвою підхоплюємо
        # мовчки, а свою назву користувача не чіпаємо.
        if self.schedule_task_name == LEGACY_TASK_NAME:
            self.schedule_task_name = "InstRef Sync"
        # Старі паузи виявились надто жвавими — Instagram надіслав попередження
        # про автоматизацію. Хто їх не чіпав, отримує спокійніші.
        if (self.page_delay_min, self.page_delay_max) == LEGACY_DELAYS:
            self.page_delay_min, self.page_delay_max = 8.0, 15.0
        if abs(float(self.download_delay) - LEGACY_DOWNLOAD_DELAY) < 1e-9:
            self.download_delay = 1.0

    def save(self, path: Path | None = None) -> None:
        path = path or CONFIG_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    # -------------------------------------------------------------- helpers
    @property
    def root(self) -> Path:
        return Path(self.download_dir).expanduser()

    @property
    def thumbs_dir(self) -> Path:
        return self.root / "_thumbnails"

    @property
    def meta_dir(self) -> Path:
        return self.root / "_metadata"

    @property
    def review_dir(self) -> Path:
        from .naming import safe_component

        return self.root / safe_component(self.review_subfolder or "_review")

    @property
    def cache_dir(self) -> Path:
        """Тимчасові ролики, які модель ще тільки дивиться.

        Свідомо всередині папки завантажень: тоді схвалене відео переїжджає
        на місце перейменуванням, без повторного завантаження й без копіювання
        між дисками.
        """
        return self.root / "_cache"

    def target_dir(self, collection_name: str) -> Path:
        """Куди класти файл для конкретної підбірки."""
        if self.structure == STRUCTURE_PER_COLLECTION:
            from .naming import safe_component

            return self.root / safe_component(collection_name or ALL_POSTS_NAME)
        return self.root

    def wants(self, media_type: int) -> bool:
        """media_type: 1=фото, 2=відео, 8=карусель."""
        if media_type == 2:
            return self.download_videos
        if media_type == 1:
            return self.download_photos
        return self.download_videos or self.download_photos


# --------------------------------------------------------------------------
# Сесія Instagram зберігається окремо від config.json (це секрет).
#
# На Windows вміст шифрується DPAPI (CryptProtectData) — ключ привʼязаний до
# облікового запису Windows, тож скопійований session.json на іншій машині
# або під іншим користувачем не читається. Без сторонніх залежностей: ctypes.
# На інших системах файл лишається відкритим JSON із правами 0600.
# --------------------------------------------------------------------------
DPAPI_KEY = "dpapi"
DPAPI_DESCRIPTION = "InstRef session"


def _dpapi(data: bytes, protect: bool) -> bytes | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class DataBlob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        buffer = ctypes.create_string_buffer(data, len(data))
        blob_in = DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
        blob_out = DataBlob()
        flags = 0x01  # CRYPTPROTECT_UI_FORBIDDEN — без діалогів у фоновому запуску
        if protect:
            ok = crypt32.CryptProtectData(
                ctypes.byref(blob_in), DPAPI_DESCRIPTION, None, None, None, flags,
                ctypes.byref(blob_out))
        else:
            ok = crypt32.CryptUnprotectData(
                ctypes.byref(blob_in), None, None, None, None, flags, ctypes.byref(blob_out))
        if not ok:
            return None
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)
    except Exception:  # noqa: BLE001 — шифрування не має ламати вхід
        return None


def load_session(path: Path | None = None) -> Dict[str, Any]:
    path = path or SESSION_PATH
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if isinstance(raw, dict) and DPAPI_KEY in raw:
        try:
            plain = _dpapi(base64.b64decode(raw[DPAPI_KEY]), protect=False)
        except (ValueError, TypeError):
            plain = None
        if plain is None:
            return {}
        try:
            return json.loads(plain.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
    if isinstance(raw, dict) and raw.get("sessionid") and sys.platform == "win32":
        # Старий відкритий файл — перезаписуємо зашифрованим за першої нагоди.
        save_session(raw, path)
    return raw if isinstance(raw, dict) else {}


def save_session(data: Dict[str, Any], path: Path | None = None) -> None:
    path = path or SESSION_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    plain = json.dumps(data, ensure_ascii=False, indent=2)
    sealed = _dpapi(plain.encode("utf-8"), protect=True)
    if sealed is not None:
        plain = json.dumps({DPAPI_KEY: base64.b64encode(sealed).decode("ascii")}, indent=2)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(plain, encoding="utf-8")
    os.replace(tmp, path)
    try:  # прибрати файл із загального доступу, наскільки дозволяє ОС
        os.chmod(path, 0o600)
    except OSError:
        pass


def clear_session(path: Path | None = None) -> None:
    path = path or SESSION_PATH
    try:
        (path).unlink()
    except OSError:
        pass
