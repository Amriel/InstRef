"""Головне вікно застосунку."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, QTime, QTimer
from PySide6.QtGui import QAction, QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QMainWindow, QMenu,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
    QSpinBox, QSplitter, QStackedWidget, QStyle, QSystemTrayIcon, QTableWidget,
    QTableWidgetItem, QTabWidget, QTimeEdit, QVBoxLayout, QWidget,
)

EAGLE_DEFAULT_URL = "http://localhost:41595"
LABEL_WIDTH = 168  # однакова колонка підписів на всіх сторінках налаштувань

from .. import APP_NAME, __version__, status
from ..config import (
    ALL_POSTS_PK, Config, DEFAULT_TEMPLATE, LIKED_PK, LOG_DIR, SCHED_DAILY, SCHED_HOURLY,
    SCHED_ONLOGON, SCHED_WEEKLY, SCHEDULE_LABELS, STATE_PATH, STRUCTURE_FLAT,
    STRUCTURE_PER_COLLECTION, TEMPLATE_TOKENS, WEEKDAYS, app_dir, clear_session,
    load_session, resource_dir, save_session,
)
from ..downloader import human_size
from ..eagle import EagleClient, EagleError
from ..instagram import CollectionInfo
from ..session import BROWSER_LABELS, MANUAL_HELP, normalize_sessionid, sessionid_from_cookies_txt
from ..state import State
from ..vision import (
    DEFAULT_PROMPT as VISION_PROMPT,
    MAX_FRAMES as VISION_MAX_FRAMES,
    PLACEHOLDERS as VISION_PLACEHOLDERS,
    SAFE_FRAMES as VISION_SAFE_FRAMES,
)
from . import theme
from .review_tab import ReviewTab
from .workers import (
    CleanupWorker, CollectionsWorker, ConnectWorker, CookieWorker, DescribeWorker,
    DupeWorker, PushWorker, RefreshWorker, SyncWorker,
)

DESCRIBE_LABEL = "Описати бібліотеку моделлю"


def _label(text: str, role: str = "") -> QLabel:
    lbl = QLabel(text)
    if role:
        lbl.setProperty("role", role)
    lbl.setWordWrap(True)
    return lbl


def _row(layout) -> QWidget:
    """Прозора обгортка, щоб горизонтальний ряд можна було класти у QFormLayout."""
    wrapper = QWidget()
    wrapper.setProperty("role", "row")
    layout.setContentsMargins(0, 0, 0, 0)
    wrapper.setLayout(layout)
    return wrapper


def _left(*widgets) -> QHBoxLayout:
    """Ряд, притиснутий вліво, щоб вузькі поля не розтягувались на всю ширину."""
    layout = QHBoxLayout()
    for widget in widgets:
        layout.addWidget(widget)
    layout.addStretch(1)
    return layout


def _flabel(text: str) -> QLabel:
    """Підпис поля фіксованої ширини — щоб колонки не «стрибали» між розділами."""
    lbl = QLabel(text)
    lbl.setFixedWidth(LABEL_WIDTH)
    lbl.setAlignment(Qt.AlignRight | Qt.AlignTop)
    lbl.setWordWrap(True)
    lbl.setContentsMargins(0, 7, 0, 0)
    return lbl


HINT_ASSUMED_WIDTH = 330  # свідомо вузько: краще зайвий піксель, ніж обрізаний рядок


class _Hint(QLabel):
    """Пояснення під полем.

    QFormLayout не питає обгорнутий QLabel про heightForWidth, тому текст
    обрізався знизу. Рахуємо потрібну висоту самі — і перераховуємо щоразу,
    коли текст або ширина змінюються.
    """

    def __init__(self, text: str = ""):
        super().__init__(text)
        self.setProperty("role", "hint")
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.MinimumExpanding)
        self._recalc()

    def setText(self, text: str) -> None:  # noqa: N802 — Qt API
        super().setText(text)
        self._recalc()

    def resizeEvent(self, event) -> None:  # noqa: N802 — Qt API
        super().resizeEvent(event)
        self._recalc()

    def _recalc(self) -> None:
        # Рахувати треба за фактичною шириною: якщо взяти більшу, ніж є,
        # вийде менше рядків, ніж потрібно — і хвіст тексту обріжеться.
        width = self.width() if self.width() > 60 else HINT_ASSUMED_WIDTH
        needed = self.fontMetrics().boundingRect(
            0, 0, width, 10000, Qt.TextWordWrap, self.text() or " "
        ).height()
        self.setMinimumHeight(needed + 6)


def _hint(text: str = "", lines: int = 2) -> QLabel:
    return _Hint(text)


def _page(title: str) -> tuple[QWidget, QFormLayout]:
    """Сторінка розділу з заголовком і готовою формою."""
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(16, 14, 16, 16)
    layout.setSpacing(10)
    layout.addWidget(_label(title, "h2"))

    holder = QWidget()
    form = _form(holder)
    form.setContentsMargins(0, 4, 0, 0)
    layout.addWidget(holder)
    layout.addStretch(1)
    return page, form


def _gap(form: QFormLayout, height: int = 10) -> None:
    spacer = QWidget()
    spacer.setFixedHeight(height)
    form.addRow(spacer)


def _scrollable(widget: QWidget) -> QScrollArea:
    """Сторінка гортається лише тоді, коли реально не влазить."""
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setWidget(widget)
    return area


def _same_prompt(left: str, right: str) -> bool:
    """Порівняння інструкцій без оглядки на переноси й зайві пробіли."""
    return " ".join((left or "").split()) == " ".join((right or "").split())


def _csv(text: str) -> List[str]:
    """Рядок «a, b, c» → список без порожніх і дублікатів."""
    seen, result = set(), []
    for chunk in (text or "").replace(";", ",").split(","):
        value = chunk.strip().lstrip("@#")
        if value and value.lower() not in seen:
            seen.add(value.lower())
            result.append(value)
    return result


def _form(parent: QWidget) -> QFormLayout:
    form = QFormLayout(parent)
    form.setSpacing(8)
    form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
    form.setFormAlignment(Qt.AlignTop)
    form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
    return form


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.cfg = Config.load()
        self.state = State(STATE_PATH)
        self.sessionid: str = load_session().get("sessionid", "")
        self.username: str = load_session().get("username", "")
        self.collections: List[CollectionInfo] = []

        self.sync_worker: Optional[SyncWorker] = None
        self.connect_worker: Optional[ConnectWorker] = None
        self.cookie_worker: Optional[CookieWorker] = None
        self.collections_worker: Optional[CollectionsWorker] = None
        self.refresh_worker: Optional[RefreshWorker] = None
        self.push_worker: Optional[PushWorker] = None
        self.cleanup_worker: Optional[CleanupWorker] = None
        self.describe_worker: Optional[DescribeWorker] = None
        self.dupe_worker: Optional[DupeWorker] = None

        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1020, 720)
        self.setMinimumSize(880, 600)
        icon_path = resource_dir() / "assets" / "icon.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self._quitting = False
        self._build()
        self._build_tray()
        self._load_config_into_ui()
        self._refresh_status()
        QTimer.singleShot(300, self._startup_hints)

    # ------------------------------------------------------------------ трей
    def _build_tray(self) -> None:
        icon = self.windowIcon()
        if icon.isNull():
            icon = self.style().standardIcon(QStyle.SP_DriveNetIcon)
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip(APP_NAME)

        menu = QMenu()
        act_show = QAction("Відкрити вікно", self)
        act_show.triggered.connect(self._restore_window)
        act_sync = QAction("Синхронізувати зараз", self)
        act_sync.triggered.connect(self.on_start)
        act_folder = QAction("Відкрити папку", self)
        act_folder.triggered.connect(self.on_open_folder)
        act_quit = QAction("Вийти", self)
        act_quit.triggered.connect(self._quit_app)
        for action in (act_show, act_sync, act_folder):
            menu.addAction(action)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.DoubleClick:
            self._restore_window()

    def _restore_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_app(self) -> None:
        from PySide6.QtWidgets import QApplication

        self._quitting = True
        self.close()
        QApplication.instance().quit()

    def _notify(self, title: str, text: str) -> None:
        if self.cfg.notify_on_finish and self.tray.isVisible():
            self.tray.showMessage(title, text, QSystemTrayIcon.Information, 6000)

    # ================================================================ побудова
    def _build(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = _label(APP_NAME, "h1")
        self.status_label = _label("", "muted")
        self.status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.status_label)
        layout.addLayout(header)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_sync(), "Синхронізація")
        self.tabs.addTab(self._tab_settings(), "Налаштування")
        self.review_tab = ReviewTab(self.cfg, self.state, self._log,
                                    on_change=self._set_review_badge)
        self.tabs.addTab(self.review_tab, "Ревʼю")
        # Вкладка рахувала себе ще до того, як опинилась у QTabWidget, тож
        # виставляємо значок тут — інакше на старті він був би порожній.
        self._set_review_badge(self.state.review_count())
        self.session_index = self.tabs.addTab(self._tab_session(), "Сесія")
        layout.addWidget(self.tabs, 1)

        self.setCentralWidget(root)

    # ------------------------------------------------------------- вкладка 1
    def _tab_sync(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        bar = QHBoxLayout()
        self.btn_refresh = QPushButton("Оновити підбірки")
        self.btn_refresh.clicked.connect(self.on_refresh_collections)
        self.btn_check_all = QPushButton("Позначити всі")
        self.btn_check_all.clicked.connect(lambda: self._set_all_checks(True))
        self.btn_uncheck_all = QPushButton("Зняти всі")
        self.btn_uncheck_all.clicked.connect(lambda: self._set_all_checks(False))
        bar.addWidget(self.btn_refresh)
        bar.addWidget(self.btn_check_all)
        bar.addWidget(self.btn_uncheck_all)
        bar.addStretch(1)
        self.btn_open_folder = QPushButton("Відкрити папку")
        self.btn_open_folder.clicked.connect(self.on_open_folder)
        bar.addWidget(self.btn_open_folder)
        layout.addLayout(bar)

        # Банер про невдалий попередній (зокрема фоновий) запуск
        self.banner = QFrame()
        self.banner.setProperty("role", "banner")
        banner_layout = QHBoxLayout(self.banner)
        banner_layout.setContentsMargins(12, 10, 10, 10)
        banner_layout.setSpacing(10)
        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        self.banner_title = _label("", "title")
        self.banner_text = _label("", "")
        text_col.addWidget(self.banner_title)
        text_col.addWidget(self.banner_text)
        banner_layout.addLayout(text_col, 1)
        self.btn_banner_action = QPushButton("Підключити сесію")
        self.btn_banner_action.clicked.connect(lambda: self._open_session_tab())
        self.btn_banner_close = QPushButton("Сховати")
        self.btn_banner_close.clicked.connect(self._dismiss_banner)
        banner_layout.addWidget(self.btn_banner_action)
        banner_layout.addWidget(self.btn_banner_close)
        self.banner.setVisible(False)
        layout.addWidget(self.banner)

        splitter = QSplitter(Qt.Vertical)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["", "Підбірка", "В Instagram", "Завантажено"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.Fixed)
        head.setSectionResizeMode(1, QHeaderView.Stretch)
        head.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        head.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.setColumnWidth(0, 36)
        splitter.addWidget(self.table)

        log_box = QWidget()
        log_layout = QVBoxLayout(log_box)
        log_layout.setContentsMargins(0, 6, 0, 0)
        log_layout.setSpacing(6)
        log_layout.addWidget(_label("Журнал", "h2"))
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("log")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(4000)
        log_layout.addWidget(self.log_view)
        splitter.addWidget(log_box)
        splitter.setSizes([300, 260])
        layout.addWidget(splitter, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("Очікування")
        layout.addWidget(self.progress)

        controls = QHBoxLayout()
        self.progress_label = _label("Готово до запуску", "muted")
        controls.addWidget(self.progress_label, 1)
        self.btn_stop = QPushButton("Зупинити")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.on_stop)
        self.btn_start = QPushButton("Синхронізувати")
        self.btn_start.setProperty("role", "primary")
        self.btn_start.clicked.connect(self.on_start)
        controls.addWidget(self.btn_stop)
        controls.addWidget(self.btn_start)
        layout.addLayout(controls)
        return page

    # ------------------------------------------------------------- вкладка 2
    def _tab_settings(self) -> QWidget:
        """Налаштування розкладені по розділах: без нескінченної прокрутки
        і з однаковою шириною колонки підписів на всіх сторінках."""
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(14, 12, 14, 10)
        outer.setSpacing(10)

        body = QHBoxLayout()
        body.setSpacing(12)

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(176)
        for name in ("Завантаження", "Сканування", "Пролайкане", "Модель",
                     "Eagle", "Автозапуск", "Додатково"):
            self.nav.addItem(name)

        self.pages = QStackedWidget()
        for builder in (self._page_download, self._page_scan, self._page_liked,
                        self._page_vision, self._page_eagle, self._page_autostart,
                        self._page_extra):
            self.pages.addWidget(_scrollable(builder()))
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)

        body.addWidget(self.nav)
        body.addWidget(self.pages, 1)
        outer.addLayout(body, 1)

        bottom = QHBoxLayout()
        self.btn_open_logs = QPushButton("Папка логів")
        self.btn_open_logs.clicked.connect(lambda: self._open_path(LOG_DIR))
        self.lbl_totals = _label("", "muted")
        self.btn_save = QPushButton("Зберегти налаштування")
        self.btn_save.setProperty("role", "primary")
        self.btn_save.clicked.connect(self.on_save_settings)
        bottom.addWidget(self.btn_open_logs)
        bottom.addWidget(self.lbl_totals, 1)
        bottom.addWidget(self.btn_save)
        outer.addLayout(bottom)

        self.nav.setCurrentRow(0)
        return page

    # ------------------------------------------------- розділ «Завантаження»
    def _page_download(self) -> QWidget:
        page, form = _page("Куди і що зберігати")

        self.ed_dir = QLineEdit()
        self.btn_pick_dir = QPushButton("Огляд…")
        self.btn_pick_dir.setFixedWidth(90)
        self.btn_pick_dir.clicked.connect(self.on_pick_dir)
        folder = QHBoxLayout()
        folder.addWidget(self.ed_dir, 1)
        folder.addWidget(self.btn_pick_dir)
        form.addRow(_flabel("Папка"), _row(folder))

        self.cb_structure = QComboBox()
        self.cb_structure.addItem("Усе в одну папку (рекомендовано з Eagle)", STRUCTURE_FLAT)
        self.cb_structure.addItem("Підпапка на кожну підбірку", STRUCTURE_PER_COLLECTION)
        form.addRow(_flabel("Структура"), self.cb_structure)

        self.ck_videos = QCheckBox("Відео / Reels")
        self.ck_photos = QCheckBox("Фото та каруселі")
        self.ck_thumbs = QCheckBox("Прев\u02bcю (.jpg)")
        types = QHBoxLayout()
        for widget in (self.ck_videos, self.ck_photos, self.ck_thumbs):
            types.addWidget(widget)
        types.addStretch(1)
        form.addRow(_flabel("Качати"), _row(types))

        self.ck_embed = QCheckBox("Вшивати у файл")
        self.ck_embed.setToolTip(
            "Опис, автор, дата, посилання й хештеги записуються всередину самого\n"
            "mp4 чи jpg. Видно в плеєрах, у Eagle і у властивостях файлу Windows."
        )
        self.ck_meta = QCheckBox("Ще й окремим .json")
        self.ck_meta.setToolTip("Додатковий файл поруч — з повними даними поста.")
        meta_row = QHBoxLayout()
        meta_row.addWidget(self.ck_embed)
        meta_row.addWidget(self.ck_meta)
        meta_row.addStretch(1)
        form.addRow(_flabel("Опис і автор"), _row(meta_row))

        _gap(form)

        self.ed_template = QLineEdit()
        self.ed_template.setPlaceholderText(DEFAULT_TEMPLATE)
        self.ed_template.setToolTip(
            "Доступні токени:\n" + "\n".join(f"{k} — {v}" for k, v in TEMPLATE_TOKENS.items())
        )
        form.addRow(_flabel("Шаблон імені"), self.ed_template)
        form.addRow(_flabel(""), _hint(" ".join(TEMPLATE_TOKENS)))

        self.sp_maxsize = QSpinBox()
        self.sp_maxsize.setRange(0, 20000)
        self.sp_maxsize.setSuffix(" МБ")
        self.sp_maxsize.setSpecialValueText("без обмеження")
        self.sp_maxsize.setFixedWidth(185)
        form.addRow(_flabel("Пропускати більші за"), _row(_left(self.sp_maxsize)))
        return page

    # --------------------------------------------------- розділ «Сканування»
    def _page_scan(self) -> QWidget:
        page, form = _page("Як обходити збережене")

        self.ck_incremental = QCheckBox("Лише нове (пропускати вже завантажене)")
        form.addRow(_flabel(""), self.ck_incremental)

        self.sp_stop_known = QSpinBox()
        self.sp_stop_known.setRange(0, 500)
        self.sp_stop_known.setSuffix(" постів")
        self.sp_stop_known.setFixedWidth(185)
        self.sp_stop_known.setToolTip(
            "Скільки вже відомих постів поспіль зустріти, щоб зупинити обхід підбірки.\n"
            "0 — завжди проходити підбірку до кінця."
        )
        form.addRow(_flabel("Стоп після"), _row(_left(self.sp_stop_known)))

        self.sp_scan_limit = QSpinBox()
        self.sp_scan_limit.setRange(0, 100000)
        self.sp_scan_limit.setSpecialValueText("без обмеження")
        self.sp_scan_limit.setSuffix(" постів")
        self.sp_scan_limit.setFixedWidth(185)
        self.sp_scan_limit.setToolTip(
            "Скільки найсвіжіших постів переглядати в збережених і підбірках.\n"
            "У пролайканого свій окремий ліміт."
        )
        form.addRow(_flabel("Дивитись останніх"), _row(_left(self.sp_scan_limit)))
        form.addRow(_flabel(""), _hint(
            "Обмежує обхід збережених і підбірок найсвіжішими постами. "
            "У пролайканого свій ліміт — у розділі «Пролайкане»."))

        self.sp_limit = QSpinBox()
        self.sp_limit.setRange(0, 100000)
        self.sp_limit.setSpecialValueText("без ліміту")
        self.sp_limit.setFixedWidth(185)
        form.addRow(_flabel("Максимум за запуск"), _row(_left(self.sp_limit)))

        _gap(form)

        self.sp_delay_min = QDoubleSpinBox()
        self.sp_delay_min.setRange(0.5, 60.0)
        self.sp_delay_min.setSingleStep(0.5)
        self.sp_delay_min.setSuffix(" с")
        self.sp_delay_min.setFixedWidth(112)
        self.sp_delay_max = QDoubleSpinBox()
        self.sp_delay_max.setRange(0.5, 120.0)
        self.sp_delay_max.setSingleStep(0.5)
        self.sp_delay_max.setSuffix(" с")
        self.sp_delay_max.setFixedWidth(112)
        delays = QHBoxLayout()
        delays.addWidget(QLabel("від"))
        delays.addWidget(self.sp_delay_min)
        delays.addWidget(QLabel("до"))
        delays.addWidget(self.sp_delay_max)
        delays.addStretch(1)
        form.addRow(_flabel("Пауза між сторінками"), _row(delays))
        form.addRow(_flabel(""), _hint(
            "Головний захист від блокування акаунта — знижувати без потреби не варто."))
        return page

    # -------------------------------------------------- розділ «Пролайкане»
    def _page_liked(self) -> QWidget:
        page, form = _page("Пролайкане та фільтр мемів")

        self.ck_sync_liked = QCheckBox("Синхронізувати пролайкане")
        form.addRow(_flabel(""), self.ck_sync_liked)
        form.addRow(_flabel(""), _hint(
            "Зʼявиться окремою підбіркою у списку на вкладці «Синхронізація», "
            "в Eagle — своєю папкою."))

        self.sp_liked_limit = QSpinBox()
        self.sp_liked_limit.setRange(0, 100000)
        self.sp_liked_limit.setSpecialValueText("без обмеження")
        self.sp_liked_limit.setSuffix(" постів")
        self.sp_liked_limit.setFixedWidth(185)
        self.sp_liked_limit.setToolTip(
            "Скільки найсвіжіших пролайканих переглядати за запуск.\n"
            "Рахуються саме переглянуті пости, а не завантажені."
        )
        form.addRow(_flabel("Дивитись останніх"), _row(_left(self.sp_liked_limit)))
        form.addRow(_flabel(""), _hint(
            "Стрічка лайків довга, а відсіяні меми не зменшують ліміт завантажень — "
            "без цього обмеження обхід тягнувся б через усю історію."))

        _gap(form)

        self.ck_classify = QCheckBox("Відсівати меми")
        self.ck_classify.setToolTip(
            "Працює тільки для пролайканого. Збережене качається як є."
        )
        form.addRow(_flabel(""), self.ck_classify)
        form.addRow(_flabel(""), _hint(
            "Оцінка за хештегами, іменем акаунта, тривалістю та позначкою реклами. "
            "Метод приблизний: явні випадки визначає добре, межові — ні, тому вони "
            "їдуть у ревʼю, а не видаляються."))

        self.cb_uncertain = QComboBox()
        self.cb_uncertain.addItem("Відкласти на ревʼю", "review")
        self.cb_uncertain.addItem("Качати", "download")
        self.cb_uncertain.addItem("Пропускати", "skip")
        self.cb_uncertain.setFixedWidth(240)
        form.addRow(_flabel("Сумнівні пости"), _row(_left(self.cb_uncertain)))

        self.sp_meme_seconds = QDoubleSpinBox()
        self.sp_meme_seconds.setRange(0.0, 120.0)
        self.sp_meme_seconds.setSingleStep(1.0)
        self.sp_meme_seconds.setSuffix(" с")
        self.sp_meme_seconds.setSpecialValueText("не зважати")
        self.sp_meme_seconds.setFixedWidth(150)
        self.sp_meme_seconds.setToolTip(
            "Коротке відео без арт-ознак додає бал на користь мема. 0 — вимкнено."
        )
        form.addRow(_flabel("Короткі відео до"), _row(_left(self.sp_meme_seconds)))

        _gap(form)

        self.ed_block = QLineEdit()
        self.ed_block.setPlaceholderText("memepage, 9gag — через кому")
        form.addRow(_flabel("Ніколи не качати"), self.ed_block)

        self.ed_allow = QLineEdit()
        self.ed_allow.setPlaceholderText("studioalt, formnorm — через кому")
        form.addRow(_flabel("Завжди качати"), self.ed_allow)
        form.addRow(_flabel(""), _hint(
            "Списки акаунтів мають пріоритет над усіма правилами й поповнюються "
            "кнопками у вкладці «Ревʼю» — саме вони з часом дають точність."))

        _gap(form)

        self.ed_meme_tags = QLineEdit()
        self.ed_meme_tags.setPlaceholderText("додаткові хештеги мемів, через кому")
        form.addRow(_flabel("Мем-хештеги"), self.ed_meme_tags)

        self.ed_art_tags = QLineEdit()
        self.ed_art_tags.setPlaceholderText("додаткові хештеги арту, через кому")
        form.addRow(_flabel("Арт-хештеги"), self.ed_art_tags)
        form.addRow(_flabel(""), _hint(
            "Візуальна модель, яка дивиться сумнівні пости, — у розділі «Модель»."))
        return page

    # ------------------------------------------------------ розділ «Модель»
    def _page_vision(self) -> QWidget:
        page, form = _page("Візуальна модель (LM Studio)")

        self.ck_vision = QCheckBox("Показувати сумнівні пости моделі")
        self.ck_vision.setToolTip(
            "Модель дивиться кадри поста й каже: мем / арт / реклама / гра / інше,\n"
            "а заразом пише опис і теги. Викликається лише для сумнівних постів —\n"
            "явне вирішують правила, швидко й безкоштовно."
        )
        form.addRow(_flabel(""), self.ck_vision)
        form.addRow(_flabel(""), _hint(
            "У LM Studio має бути завантажена візуальна модель і увімкнений сервер "
            "(вкладка Developer → Start Server). Адресу можна вставляти як є — "
            "/v1 допишеться сам. Якщо сервер не відповідає, застосунок мовчки "
            "працює за правилами."))

        self.ed_vision_url = QLineEdit()
        self.ed_vision_url.setPlaceholderText("http://localhost:1234")
        form.addRow(_flabel("Адреса LM Studio"), self.ed_vision_url)

        self.cb_vision_model = QComboBox()
        self.cb_vision_model.setEditable(True)
        self.cb_vision_model.lineEdit().setPlaceholderText(
            "порожньо — перша завантажена модель")
        self.btn_vision_test = QPushButton("Перевірити")
        self.btn_vision_test.clicked.connect(self.on_test_vision)
        model_row = QHBoxLayout()
        model_row.addWidget(self.cb_vision_model, 1)
        model_row.addWidget(self.btn_vision_test)
        form.addRow(_flabel("Модель"), _row(model_row))
        self.lbl_vision = _hint("")
        form.addRow(_flabel(""), self.lbl_vision)

        _gap(form)

        self.sp_vision_frames = QSpinBox()
        self.sp_vision_frames.setRange(1, VISION_MAX_FRAMES)
        self.sp_vision_frames.setSuffix(" кадр(ів)")
        self.sp_vision_frames.setFixedWidth(185)
        self.sp_vision_frames.setToolTip(
            "Скільки кадрів дістати з ролика й показати моделі одним запитом.\n"
            "1 — тільки обкладинка, як було раніше."
        )
        self.sp_vision_frames.valueChanged.connect(self._update_frames_note)
        form.addRow(_flabel("Кадрів на відео"), _row(_left(self.sp_vision_frames)))
        self.lbl_frames = _hint("")
        form.addRow(_flabel(""), self.lbl_frames)
        form.addRow(_flabel(""), _hint(
            "Скільки кадрів з одного ролика бачить модель за раз. Беруться "
            "рівномірно по всій тривалості, краї підрізаються. Обкладинка reels — "
            "часто чорний кадр або титр, тому один кадр давав найбільше помилок. "
            "Каруселі модель бачить по слайдах."))

        self.ck_vision_describe = QCheckBox("Писати опис і теги для кожного нового поста")
        self.ck_vision_describe.setToolTip(
            "Не лише для сумнівних: модель подивиться кожне нове завантаження."
        )
        form.addRow(_flabel(""), self.ck_vision_describe)
        form.addRow(_flabel(""), _hint(
            "Збережене модель не судить — тільки описує, і воно одразу йде в Eagle. "
            "Опис і теги вшиваються у сам файл та в нотатку Eagle. "
            "Помітно повільніше: кожен пост — окремий запит до моделі."))

        _gap(form)

        self.ck_vision_meme = QCheckBox("меми")
        self.ck_vision_game = QCheckBox("ігрові")
        skip_row = QHBoxLayout()
        skip_row.addWidget(self.ck_vision_meme)
        skip_row.addWidget(self.ck_vision_game)
        skip_row.addStretch(1)
        form.addRow(_flabel("Модель відсіює"), _row(skip_row))
        form.addRow(_flabel(""), _hint(
            "Арт і рекламу модель пропускає на завантаження, «інше» лишає тобі в ревʼю."))

        self.sp_vision_conf = QDoubleSpinBox()
        self.sp_vision_conf.setRange(0.0, 1.0)
        self.sp_vision_conf.setSingleStep(0.05)
        self.sp_vision_conf.setDecimals(2)
        self.sp_vision_conf.setFixedWidth(120)
        self.sp_vision_conf.setToolTip(
            "Нижче цієї впевненості рішення моделі не приймається — пост іде в ревʼю."
        )
        form.addRow(_flabel("Мінімальна впевненість"), _row(_left(self.sp_vision_conf)))

        self.ck_model_glance = QCheckBox("Показувати рішення моделі окремою вкладкою")
        self.ck_model_glance.setToolTip(
            "Модель вирішила — але в Eagle пост іде тільки після твого погляду."
        )
        form.addRow(_flabel(""), self.ck_model_glance)
        form.addRow(_flabel(""), _hint(
            "Схвалене моделлю лягає в основну теку й чекає підтвердження; "
            "відсіяне лежить у теці ревʼю, доки не погодишся його видалити. "
            "Кнопка «Погодитись з моделлю» розбирає всю чергу одним кліком. "
            "Без галочки модель діє мовчки: качає й видаляє одразу."))

        self.sp_vision_timeout = QSpinBox()
        self.sp_vision_timeout.setRange(10, 900)
        self.sp_vision_timeout.setSuffix(" с")
        self.sp_vision_timeout.setFixedWidth(120)
        self.sp_vision_timeout.setToolTip(
            "Кілька кадрів плюс опис — це довше за одну обкладинку.\n"
            "Якщо в журналі часто «модель не встигла відповісти», збільш."
        )
        form.addRow(_flabel("Чекати відповідь до"), _row(_left(self.sp_vision_timeout)))

        _gap(form)

        self.ck_taxonomy = QCheckBox("Брати теги лише зі словника")
        self.ck_taxonomy.setToolTip(
            "Усе, чого немає у словнику, відкидається кодом — не проханням до моделі."
        )
        form.addRow(_flabel(""), self.ck_taxonomy)
        form.addRow(_flabel(""), _hint(
            "Без словника кожен запуск вигадує свої слова: 3d-render, 3drender, "
            "render і 3d — чотири різні теги для Eagle, і жоден не знайде решту. "
            "Списки лежать у taxonomy.json — їх можна правити руками."))

        self.btn_taxonomy = QPushButton("Пропозиції до словника")
        self.btn_taxonomy.setToolTip(
            "Теги, які модель пропонувала часто, а словник не прийняв.\n"
            "Звідти ж відкривається сам taxonomy.json."
        )
        self.btn_taxonomy.clicked.connect(self.on_tag_suggestions)
        self.sp_suggest_after = QSpinBox()
        self.sp_suggest_after.setRange(1, 100)
        self.sp_suggest_after.setSuffix(" разів")
        self.sp_suggest_after.setFixedWidth(120)
        self.sp_suggest_after.setToolTip(
            "Скільки разів модель має попроситись зі своїм тегом,\n"
            "щоб застосунок запропонував додати його у словник."
        )
        vocab = QHBoxLayout()
        vocab.addWidget(self.btn_taxonomy)
        vocab.addWidget(QLabel("після"))
        vocab.addWidget(self.sp_suggest_after)
        vocab.addStretch(1)
        form.addRow(_flabel("Словник росте"), _row(vocab))

        _gap(form)

        prompt_head = QHBoxLayout()
        prompt_head.addWidget(QLabel("Що саме модель має зробити з кадрами"))
        prompt_head.addStretch(1)
        self.btn_prompt_reset = QPushButton("Повернути типову")
        self.btn_prompt_reset.setFixedWidth(160)
        self.btn_prompt_reset.clicked.connect(self.on_reset_prompt)
        prompt_head.addWidget(self.btn_prompt_reset)
        form.addRow(_flabel("Інструкція"), _row(prompt_head))

        self.ed_vision_prompt = QPlainTextEdit()
        self.ed_vision_prompt.setMinimumHeight(230)
        self.ed_vision_prompt.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        form.addRow(_flabel(""), self.ed_vision_prompt)
        form.addRow(_flabel(""), _hint(
            "Відповідь має лишатись JSON із полями category, confidence, description "
            "і tags — інакше застосунок її не зрозуміє. "
            + " ".join(f"{token} — {what}." for token, what in VISION_PLACEHOLDERS.items())
            + " Поки інструкцію не змінено, вона оновлюється разом із застосунком."))
        return page

    # -------------------------------------------------------- розділ «Eagle»
    def _page_eagle(self) -> QWidget:
        page, form = _page("Імпорт у бібліотеку Eagle")

        self.ck_eagle = QCheckBox("Імпортувати завантажене в Eagle")
        form.addRow(_flabel(""), self.ck_eagle)

        self.ed_eagle_url = QLineEdit()
        self.ed_eagle_url.setPlaceholderText("http://localhost:41595")
        form.addRow(_flabel("API"), self.ed_eagle_url)
        form.addRow(_flabel(""), _hint(
            "Eagle слухає порт 41595, поки програма відкрита. Інший порт — не запрацює."))

        self.ed_eagle_token = QLineEdit()
        self.ed_eagle_token.setPlaceholderText("лишити порожнім, якщо Eagle не просить")
        self.ed_eagle_token.setToolTip(
            "Новіші версії Eagle можуть вимагати токен:\n"
            "Preferences → Developer (Розробник) → API token."
        )
        form.addRow(_flabel("Токен"), self.ed_eagle_token)

        self.ed_eagle_root = QLineEdit()
        form.addRow(_flabel("Коренева папка"), self.ed_eagle_root)

        self.ck_eagle_once = QCheckBox("Один пост — один елемент у бібліотеці")
        self.ck_eagle_once.setToolTip(
            "Пост часто лежить і в збережених, і в лайках, і в підбірці.\n"
            "Eagle на кожен імпорт КОПІЮЄ файл, тож без цієї галочки\n"
            "той самий ролик з'являється в бібліотеці двічі-тричі."
        )
        form.addRow(_flabel(""), self.ck_eagle_once)

        self.ck_eagle_per_col = QCheckBox("Підпапка на кожну підбірку")
        form.addRow(_flabel(""), self.ck_eagle_per_col)

        _gap(form)

        self.ck_eagle_tags = QCheckBox("Хештеги з підпису")
        self.ck_eagle_tag_author = QCheckBox("Ім\u02bcя автора (@username)")
        self.ck_eagle_tag_col = QCheckBox("Назва підбірки")
        tags_col = QVBoxLayout()
        tags_col.setSpacing(4)
        for widget in (self.ck_eagle_tags, self.ck_eagle_tag_author, self.ck_eagle_tag_col):
            tags_col.addWidget(widget)
        form.addRow(_flabel("Додавати як теги"), _row(tags_col))

        self.ed_eagle_tags = QLineEdit()
        self.ed_eagle_tags.setPlaceholderText("instagram, reference — через кому")
        form.addRow(_flabel("Постійні теги"), self.ed_eagle_tags)

        _gap(form)

        self.btn_eagle_test = QPushButton("Перевірити зв\u02bcязок")
        self.btn_eagle_test.clicked.connect(self.on_test_eagle)
        self.lbl_eagle = _hint("")
        check = QHBoxLayout()
        check.addWidget(self.btn_eagle_test)
        check.addWidget(self.lbl_eagle, 1)
        form.addRow(_flabel(""), _row(check))
        return page

    # --------------------------------------------------- розділ «Автозапуск»
    def _page_autostart(self) -> QWidget:
        page, form = _page("Запуск за розкладом і разом із Windows")

        self.ck_schedule = QCheckBox("Синхронізувати за розкладом")
        self.ck_schedule.toggled.connect(self._update_schedule_widgets)
        form.addRow(_flabel(""), self.ck_schedule)

        self.cb_sched_mode = QComboBox()
        for key in (SCHED_DAILY, SCHED_HOURLY, SCHED_WEEKLY, SCHED_ONLOGON):
            self.cb_sched_mode.addItem(SCHEDULE_LABELS[key], key)
        self.cb_sched_mode.setFixedWidth(190)
        self.cb_sched_mode.currentIndexChanged.connect(self._update_schedule_widgets)

        self.sp_sched_hours = QSpinBox()
        self.sp_sched_hours.setRange(1, 23)
        self.sp_sched_hours.setSuffix(" год")
        self.sp_sched_hours.setFixedWidth(108)

        self.cb_weekday = QComboBox()
        for key, label in WEEKDAYS:
            self.cb_weekday.addItem(label, key)
        self.cb_weekday.setFixedWidth(130)

        self.ed_time = QTimeEdit()
        self.ed_time.setDisplayFormat("HH:mm")
        self.ed_time.setFixedWidth(100)

        mode_row = QHBoxLayout()
        mode_row.addWidget(self.cb_sched_mode)
        mode_row.addWidget(self.sp_sched_hours)
        mode_row.addWidget(self.cb_weekday)
        mode_row.addWidget(QLabel("о"))
        mode_row.addWidget(self.ed_time)
        mode_row.addStretch(1)
        form.addRow(_flabel("Коли"), _row(mode_row))

        self.btn_sched_apply = QPushButton("Застосувати розклад")
        self.btn_sched_apply.clicked.connect(self.on_apply_schedule)
        self.btn_sched_run = QPushButton("Запустити зараз")
        self.btn_sched_run.setToolTip("Виконати задачу планувальника негайно, у фоні")
        self.btn_sched_run.clicked.connect(self.on_run_task_now)
        buttons = QHBoxLayout()
        buttons.addWidget(self.btn_sched_apply)
        buttons.addWidget(self.btn_sched_run)
        buttons.addStretch(1)
        form.addRow(_flabel(""), _row(buttons))

        self.lbl_sched = _hint("")
        form.addRow(_flabel(""), self.lbl_sched)

        _gap(form)

        self.ck_run_at_login = QCheckBox("Запускати застосунок разом із Windows")
        self.ck_start_min = QCheckBox("Стартувати згорнутим у трей")
        self.ck_sync_launch = QCheckBox("Синхронізувати одразу після запуску")
        self.ck_tray = QCheckBox("Закриття вікна згортає в трей")
        self.ck_notify = QCheckBox("Сповіщення після завершення")
        behaviour = QVBoxLayout()
        behaviour.setSpacing(4)
        for widget in (self.ck_run_at_login, self.ck_start_min, self.ck_sync_launch,
                       self.ck_tray, self.ck_notify):
            behaviour.addWidget(widget)
        form.addRow(_flabel("Поведінка"), _row(behaviour))
        return page

    # ---------------------------------------------------- розділ «Додатково»
    def _page_extra(self) -> QWidget:
        page, form = _page("Мережа та обслуговування")

        self.sp_timeout = QSpinBox()
        self.sp_timeout.setRange(10, 600)
        self.sp_timeout.setSuffix(" с")
        self.sp_timeout.setFixedWidth(106)
        self.sp_retries = QSpinBox()
        self.sp_retries.setRange(1, 10)
        self.sp_retries.setSuffix(" спроб")
        self.sp_retries.setFixedWidth(124)
        net = QHBoxLayout()
        net.addWidget(QLabel("таймаут"))
        net.addWidget(self.sp_timeout)
        net.addWidget(QLabel("повтори"))
        net.addWidget(self.sp_retries)
        net.addStretch(1)
        form.addRow(_flabel("Мережа"), _row(net))

        self.ed_proxy = QLineEdit()
        self.ed_proxy.setPlaceholderText("http://user:pass@host:port")
        form.addRow(_flabel("Проксі"), self.ed_proxy)
        form.addRow(_flabel(""), _hint("Лишити порожнім, якщо не потрібно."))

        _gap(form)

        self.btn_refresh_library = QPushButton("Оновити назви й метадані наявних файлів")
        self.btn_refresh_library.setToolTip(
            "Перейменовує вже завантажене за поточним шаблоном і дописує\n"
            "опис та автора всередину файлів. Нічого не перекачує."
        )
        self.btn_refresh_library.clicked.connect(self.on_refresh_library)
        self.btn_push_eagle = QPushButton("Імпортувати наявні файли в Eagle")
        self.btn_push_eagle.setToolTip(
            "Заливає в Eagle те, що вже лежить на диску.\n"
            "Потрібно, якщо імпорт увімкнули після завантаження."
        )
        self.btn_push_eagle.clicked.connect(self.on_push_eagle)
        self.btn_describe = QPushButton(DESCRIBE_LABEL)
        self.btn_describe.setToolTip(
            "Показує моделі те, що вже лежить у Eagle, і дописує опис та теги.\n"
            "Синхронізація описує лише нові пости — стару бібліотеку вона не чіпає."
        )
        self.btn_describe.clicked.connect(self.on_describe_library)
        self.btn_dupes = QPushButton("Знайти дублікати в Eagle")
        self.btn_dupes.setToolTip(
            "Шукає в бібліотеці кілька елементів на один пост Instagram.\n"
            "Спершу лише показує; видаляти чи ні — вирішуєш ти."
        )
        self.btn_dupes.clicked.connect(self.on_find_dupes)
        self.btn_shortcut = QPushButton("Ярлик на робочому столі")
        self.btn_shortcut.setToolTip("Створює ярлик із нормальною іконкою замість .bat")
        self.btn_shortcut.clicked.connect(self.on_make_shortcut)
        self.btn_clear_downloads = QPushButton("Очистити папку завантажень")
        self.btn_clear_downloads.setToolTip(
            "Видаляє завантажені файли, але памʼятає, що вони вже качались —\n"
            "дублі не зʼявляться. Eagle не чіпається."
        )
        self.btn_clear_downloads.clicked.connect(self.on_clear_downloads)
        self.btn_forget_downloads = QPushButton("Забути історію завантажень")
        self.btn_forget_downloads.setToolTip(
            "Очищає локальну базу, щоб наступний запуск перекачав усе заново.\n"
            "Самі файли на диску не чіпаються."
        )
        self.btn_forget_downloads.clicked.connect(self.on_reset_state)
        self.btn_reset_settings = QPushButton("Скинути налаштування")
        self.btn_reset_settings.clicked.connect(self.on_reset_settings)

        actions = QVBoxLayout()
        actions.setSpacing(6)
        for widget in (self.btn_refresh_library, self.btn_push_eagle, self.btn_describe,
                       self.btn_dupes, self.btn_shortcut,
                       self.btn_clear_downloads, self.btn_forget_downloads,
                       self.btn_reset_settings):
            widget.setMinimumHeight(34)
            actions.addWidget(widget)
        form.addRow(_flabel("Обслуговування"), _row(actions))
        return page

    # ------------------------------------------------------------- вкладка 3
    def _tab_session(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(10)

        box_auto = QGroupBox("Автоматично з браузера")
        auto = QHBoxLayout(box_auto)
        self.cb_browser = QComboBox()
        for key, label in BROWSER_LABELS.items():
            self.cb_browser.addItem(label, key)
        self.btn_find_cookie = QPushButton("Знайти сесію")
        self.btn_find_cookie.clicked.connect(self.on_find_cookie)
        self.btn_cookies_file = QPushButton("З файлу cookies.txt…")
        self.btn_cookies_file.clicked.connect(self.on_cookies_file)
        auto.addWidget(self.cb_browser, 1)
        auto.addWidget(self.btn_find_cookie)
        auto.addWidget(self.btn_cookies_file)
        layout.addWidget(box_auto)

        box_manual = QGroupBox("Вручну")
        manual = QVBoxLayout(box_manual)
        manual.addWidget(_label(MANUAL_HELP, "muted"))
        row = QHBoxLayout()
        self.ed_session = QLineEdit()
        self.ed_session.setEchoMode(QLineEdit.Password)
        self.ed_session.setPlaceholderText("sessionid…")
        self.ck_show_session = QCheckBox("Показати")
        self.ck_show_session.toggled.connect(
            lambda on: self.ed_session.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password)
        )
        self.btn_verify = QPushButton("Перевірити")
        self.btn_verify.setProperty("role", "primary")
        self.btn_verify.clicked.connect(self.on_verify_session)
        row.addWidget(self.ed_session, 1)
        row.addWidget(self.ck_show_session)
        row.addWidget(self.btn_verify)
        manual.addLayout(row)
        layout.addWidget(box_manual)

        self.lbl_session = _label("", "muted")
        layout.addWidget(self.lbl_session)

        self.session_log = QPlainTextEdit()
        self.session_log.setObjectName("log")
        self.session_log.setReadOnly(True)
        self.session_log.setMaximumBlockCount(400)
        layout.addWidget(self.session_log, 1)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.btn_forget = QPushButton("Забути сесію")
        self.btn_forget.clicked.connect(self.on_forget_session)
        bottom.addWidget(self.btn_forget)
        layout.addLayout(bottom)
        return page

    # ============================================================ конфіг ↔ UI
    def _load_config_into_ui(self) -> None:
        cfg = self.cfg
        self.ed_dir.setText(cfg.download_dir)
        index = self.cb_structure.findData(cfg.structure)
        self.cb_structure.setCurrentIndex(max(0, index))
        self.ck_videos.setChecked(cfg.download_videos)
        self.ck_photos.setChecked(cfg.download_photos)
        self.ck_thumbs.setChecked(cfg.download_thumbnails)
        self.ck_meta.setChecked(cfg.write_metadata)
        self.ck_embed.setChecked(cfg.embed_metadata)
        self.ed_template.setText(cfg.filename_template or DEFAULT_TEMPLATE)
        self.sp_maxsize.setValue(cfg.skip_larger_than_mb)

        self.ck_incremental.setChecked(cfg.incremental)
        self.sp_scan_limit.setValue(cfg.scan_limit)
        self.sp_stop_known.setValue(cfg.stop_after_known)
        self.sp_limit.setValue(cfg.max_items_per_run)
        self.sp_delay_min.setValue(cfg.page_delay_min)
        self.sp_delay_max.setValue(cfg.page_delay_max)
        self.sp_timeout.setValue(cfg.request_timeout)
        self.sp_retries.setValue(cfg.max_retries)
        self.ed_proxy.setText(cfg.proxy)

        self.ck_sync_liked.setChecked(cfg.sync_liked)
        self.ck_classify.setChecked(cfg.classify_liked)
        self.sp_liked_limit.setValue(cfg.liked_scan_limit)
        self.cb_uncertain.setCurrentIndex(
            max(0, self.cb_uncertain.findData(cfg.uncertain_action)))
        self.sp_meme_seconds.setValue(cfg.max_meme_seconds)
        self.ed_block.setText(", ".join(cfg.block_accounts))
        self.ed_allow.setText(", ".join(cfg.allow_accounts))
        self.ck_vision.setChecked(cfg.vision_enabled)
        self.ed_vision_url.setText(cfg.vision_url)
        self.cb_vision_model.setCurrentText(cfg.vision_model)
        skip = {c.lower() for c in cfg.vision_skip_categories}
        self.ck_vision_meme.setChecked("meme" in skip)
        self.ck_vision_game.setChecked("game" in skip)
        self.sp_vision_conf.setValue(cfg.vision_min_confidence)
        self.sp_vision_frames.setValue(max(1, min(VISION_MAX_FRAMES, cfg.vision_frames)))
        self._update_frames_note(self.sp_vision_frames.value())
        self.sp_vision_timeout.setValue(max(10, min(900, cfg.vision_timeout)))
        self.ck_vision_describe.setChecked(cfg.vision_describe_downloads)
        self.ck_model_glance.setChecked(cfg.model_needs_glance)
        self.ck_taxonomy.setChecked(cfg.taxonomy_enabled)
        self.sp_suggest_after.setValue(max(1, cfg.taxonomy_suggest_after))
        # Порожньо в конфізі = типова інструкція; у полі показуємо саме її,
        # щоб було що читати й правити.
        self.ed_vision_prompt.setPlainText(cfg.vision_prompt or VISION_PROMPT)
        self.ed_meme_tags.setText(", ".join(cfg.extra_meme_tags))
        self.ed_art_tags.setText(", ".join(cfg.extra_art_tags))

        self.ck_eagle.setChecked(cfg.eagle_enabled)
        self.ed_eagle_url.setText(cfg.eagle_url)
        self.ed_eagle_token.setText(cfg.eagle_token)
        self.ed_eagle_root.setText(cfg.eagle_root_folder)
        self.ck_eagle_per_col.setChecked(cfg.eagle_folder_per_collection)
        self.ck_eagle_once.setChecked(cfg.eagle_one_item_per_post)
        self.ck_eagle_tags.setChecked(cfg.eagle_tags_from_hashtags)
        self.ck_eagle_tag_author.setChecked(cfg.eagle_tag_author)
        self.ck_eagle_tag_col.setChecked(cfg.eagle_tag_collection)
        self.ed_eagle_tags.setText(", ".join(cfg.eagle_extra_tags))

        self.ck_schedule.setChecked(cfg.schedule_enabled)
        mode_index = self.cb_sched_mode.findData(cfg.schedule_mode)
        self.cb_sched_mode.setCurrentIndex(max(0, mode_index))
        self.sp_sched_hours.setValue(cfg.schedule_interval_hours)
        weekday_index = self.cb_weekday.findData(cfg.schedule_weekday)
        self.cb_weekday.setCurrentIndex(max(0, weekday_index))
        hour, _, minute = (cfg.schedule_time or "09:00").partition(":")
        self.ed_time.setTime(QTime(int(hour or 9), int(minute or 0)))

        from .. import scheduler

        self.ck_run_at_login.setChecked(scheduler.is_run_at_login() or cfg.run_on_windows_start)
        self.ck_start_min.setChecked(cfg.start_minimized)
        self.ck_sync_launch.setChecked(cfg.sync_on_launch)
        self.ck_tray.setChecked(cfg.minimize_to_tray)
        self.ck_notify.setChecked(cfg.notify_on_finish)

        browser_index = self.cb_browser.findData(cfg.browser)
        self.cb_browser.setCurrentIndex(max(0, browser_index))
        self._update_schedule_widgets()
        self._update_schedule_label()
        self._update_totals()
        self._update_tag_badge()

    def _collect_ui_into_config(self) -> None:
        cfg = self.cfg
        cfg.download_dir = self.ed_dir.text().strip() or cfg.download_dir
        cfg.structure = self.cb_structure.currentData() or STRUCTURE_FLAT
        cfg.download_videos = self.ck_videos.isChecked()
        cfg.download_photos = self.ck_photos.isChecked()
        cfg.download_thumbnails = self.ck_thumbs.isChecked()
        cfg.write_metadata = self.ck_meta.isChecked()
        cfg.embed_metadata = self.ck_embed.isChecked()
        cfg.filename_template = self.ed_template.text().strip() or DEFAULT_TEMPLATE
        cfg.skip_larger_than_mb = self.sp_maxsize.value()

        cfg.incremental = self.ck_incremental.isChecked()
        cfg.scan_limit = self.sp_scan_limit.value()
        cfg.stop_after_known = self.sp_stop_known.value()
        cfg.max_items_per_run = self.sp_limit.value()
        cfg.page_delay_min = self.sp_delay_min.value()
        cfg.page_delay_max = max(self.sp_delay_max.value(), self.sp_delay_min.value())
        cfg.request_timeout = self.sp_timeout.value()
        cfg.max_retries = self.sp_retries.value()
        cfg.proxy = self.ed_proxy.text().strip()

        cfg.sync_liked = self.ck_sync_liked.isChecked()
        cfg.classify_liked = self.ck_classify.isChecked()
        cfg.liked_scan_limit = self.sp_liked_limit.value()
        cfg.uncertain_action = self.cb_uncertain.currentData() or "review"
        cfg.max_meme_seconds = self.sp_meme_seconds.value()
        cfg.block_accounts = _csv(self.ed_block.text())
        cfg.allow_accounts = _csv(self.ed_allow.text())
        cfg.vision_enabled = self.ck_vision.isChecked()
        cfg.vision_url = self.ed_vision_url.text().strip() or "http://localhost:1234/v1"
        cfg.vision_model = self.cb_vision_model.currentText().strip()
        cfg.vision_min_confidence = self.sp_vision_conf.value()
        cfg.vision_skip_categories = (
            (["meme"] if self.ck_vision_meme.isChecked() else [])
            + (["game"] if self.ck_vision_game.isChecked() else [])
        )
        cfg.vision_frames = self.sp_vision_frames.value()
        cfg.vision_timeout = self.sp_vision_timeout.value()
        cfg.vision_describe_downloads = self.ck_vision_describe.isChecked()
        cfg.model_needs_glance = self.ck_model_glance.isChecked()
        cfg.taxonomy_enabled = self.ck_taxonomy.isChecked()
        cfg.taxonomy_suggest_after = self.sp_suggest_after.value()
        prompt = self.ed_vision_prompt.toPlainText().strip()
        # Незмінену інструкцію зберігаємо як порожню: тоді наступна версія
        # застосунку принесе кращу, а свою правку ми не затираємо ніколи.
        cfg.vision_prompt = "" if _same_prompt(prompt, VISION_PROMPT) else prompt
        cfg.extra_meme_tags = _csv(self.ed_meme_tags.text())
        cfg.extra_art_tags = _csv(self.ed_art_tags.text())

        cfg.eagle_enabled = self.ck_eagle.isChecked()
        cfg.eagle_url = self.ed_eagle_url.text().strip() or "http://localhost:41595"
        cfg.eagle_token = self.ed_eagle_token.text().strip()
        cfg.eagle_root_folder = self.ed_eagle_root.text().strip() or "Instagram Saved"
        cfg.eagle_folder_per_collection = self.ck_eagle_per_col.isChecked()
        cfg.eagle_one_item_per_post = self.ck_eagle_once.isChecked()
        cfg.eagle_tags_from_hashtags = self.ck_eagle_tags.isChecked()
        cfg.eagle_tag_author = self.ck_eagle_tag_author.isChecked()
        cfg.eagle_tag_collection = self.ck_eagle_tag_col.isChecked()
        cfg.eagle_extra_tags = [
            tag.strip() for tag in self.ed_eagle_tags.text().split(",") if tag.strip()
        ]

        cfg.schedule_enabled = self.ck_schedule.isChecked()
        cfg.schedule_mode = self.cb_sched_mode.currentData() or SCHED_DAILY
        cfg.schedule_interval_hours = self.sp_sched_hours.value()
        cfg.schedule_weekday = self.cb_weekday.currentData() or "MON"
        cfg.schedule_time = self.ed_time.time().toString("HH:mm")

        cfg.run_on_windows_start = self.ck_run_at_login.isChecked()
        cfg.start_minimized = self.ck_start_min.isChecked()
        cfg.sync_on_launch = self.ck_sync_launch.isChecked()
        cfg.minimize_to_tray = self.ck_tray.isChecked()
        cfg.notify_on_finish = self.ck_notify.isChecked()
        cfg.browser = self.cb_browser.currentData() or "auto"

        selected = self._checked_pks()
        if selected:
            cfg.sync_all_saved = ALL_POSTS_PK in selected
            # Пролайкане має власний прапорець — без цього рядка галочка у
            # списку працювала лише для ручного запуску, а задача за розкладом
            # пролайкане ігнорувала.
            cfg.sync_liked = LIKED_PK in selected
            cfg.enabled_collections = [
                pk for pk in selected if pk not in (ALL_POSTS_PK, LIKED_PK)
            ]
            cfg.sync_all_collections = False

    # ================================================================== дії
    def on_save_settings(self) -> None:
        from .. import scheduler

        self._collect_ui_into_config()
        self.cfg.save()

        # автозапуск застосунку разом із Windows
        if self.cfg.run_on_windows_start != scheduler.is_run_at_login():
            result = scheduler.set_run_at_login(self.cfg.run_on_windows_start)
            self._log(result.message)

        self._apply_schedule(quiet=True)
        self._log("Налаштування збережено.")
        self._flash(self.btn_save, "Збережено ✓", "Зберегти налаштування")
        self._update_totals()

    def on_pick_dir(self) -> None:
        start = self.ed_dir.text() or str(app_dir())
        chosen = QFileDialog.getExistingDirectory(self, "Куди зберігати відео", start)
        if chosen:
            self.ed_dir.setText(chosen)

    def on_open_folder(self) -> None:
        self._open_path(Path(self.ed_dir.text() or self.cfg.download_dir))

    def on_test_eagle(self) -> None:
        url = self.ed_eagle_url.text().strip() or EAGLE_DEFAULT_URL
        token = self.ed_eagle_token.text().strip()
        client = EagleClient(url, token)
        try:
            info = client.ping()
        except EagleError as exc:
            # Найчастіша помилка — інший порт. Пробуємо типовий і, якщо Eagle
            # там відповідає, виправляємо поле самі, а не змушуємо гадати.
            fixed = self._try_default_eagle(url, token)
            if fixed is not None:
                info = fixed
                self.ed_eagle_url.setText(EAGLE_DEFAULT_URL)
                self._log(f"Адресу Eagle виправлено на {EAGLE_DEFAULT_URL}")
            else:
                self.lbl_eagle.setText(self._eagle_advice(url, exc))
                self.lbl_eagle.setProperty("role", "err")
                self._restyle(self.lbl_eagle)
                return

        if True:
            probe = EagleClient(self.ed_eagle_url.text().strip() or EAGLE_DEFAULT_URL, token)
            version = info.get("version", "") if isinstance(info, dict) else ""
            library = probe.library_name()
            text = "Eagle на зв'язку"
            if version:
                text += f" · v{version}"
            if library:
                text += f" · бібліотека «{library}»"
            if not self.ck_eagle.isChecked():
                # Найчастіша пастка: звʼязок є, а імпорт вимкнено — і синхронізація
                # мовчки кладе все лише на диск. Вмикаємо і повідомляємо.
                self.ck_eagle.setChecked(True)
                text += ".  Імпорт увімкнено — не забудь зберегти налаштування."
                self._log("Eagle на звʼязку — імпорт увімкнено автоматично.")
            self.lbl_eagle.setText(text)
            self.lbl_eagle.setProperty("role", "ok")
        self._restyle(self.lbl_eagle)

    def on_test_vision(self) -> None:
        """Показує, які моделі зараз завантажені в LM Studio."""
        from ..vision import VisionClient, VisionError

        url = self.ed_vision_url.text().strip() or "http://localhost:1234/v1"
        try:
            models = VisionClient(url).list_models()
        except VisionError as exc:
            self.lbl_vision.setText(
                f"{exc} Перевір, що в LM Studio запущений сервер "
                "(Developer → Start Server)."
            )
            self.lbl_vision.setProperty("role", "err")
            self._restyle(self.lbl_vision)
            return

        current = self.cb_vision_model.currentText().strip()
        self.cb_vision_model.clear()
        self.cb_vision_model.addItems(models)
        self.cb_vision_model.setCurrentText(current or (models[0] if models else ""))
        if not models:
            self.lbl_vision.setText("Сервер відповідає, але жодної моделі не завантажено.")
            self.lbl_vision.setProperty("role", "warn")
        else:
            self.lbl_vision.setText(
                f"LM Studio на звʼязку · завантажено моделей: {len(models)}. "
                "Обери візуальну — текстова не побачить картинку."
            )
            self.lbl_vision.setProperty("role", "ok")
            if not self.ck_vision.isChecked():
                self.ck_vision.setChecked(True)
        self._restyle(self.lbl_vision)

    def _update_frames_note(self, count: int) -> None:
        """Чесно каже, чим доведеться заплатити за багато кадрів.

        Стеля висока навмисно, але кожен кадр — це сотні токенів контексту й
        зайві секунди. Мовчазний спінбокс до 60 виглядав би як обіцянка, що
        так робити нормально.
        """
        from ..frames import side_for

        if count <= 1:
            text, role = ("Модель бачитиме лише обкладинку — як до появи кадрів.", "")
        elif count <= VISION_SAFE_FRAMES:
            text, role = (f"Кадри по {side_for(count)} px. Робочий діапазон.", "ok")
        else:
            text, role = (
                f"Кадри здрібнюються до {side_for(count)} px, щоб запит не розпух. "
                f"Понад {VISION_SAFE_FRAMES} кадрів вистачає контексту не кожній "
                "моделі: якщо відповіді почнуть ламатись або довго не приходити — "
                "зменш або підніми таймаут нижче.",
                "warn",
            )
        self.lbl_frames.setText(text)
        self.lbl_frames.setProperty("role", role or "hint")
        self._restyle(self.lbl_frames)

    def on_tag_suggestions(self) -> None:
        """Показує, чого словнику бракує за думкою моделі."""
        from ..taxonomy import Taxonomy
        from .tag_dialog import TagSuggestionsDialog

        path = Taxonomy.ensure_file()
        dialog = TagSuggestionsDialog(
            self.state, Taxonomy.load(path), path,
            min_hits=self.sp_suggest_after.value(), log=self._log, parent=self)
        dialog.exec()
        self._update_tag_badge()

    def _update_tag_badge(self) -> None:
        """Кнопка сама каже, чи є на що дивитись — інакше туди ніхто не зайде."""
        button = getattr(self, "btn_taxonomy", None)
        if button is None:
            return
        try:
            pending = self.state.tag_candidate_count(self.sp_suggest_after.value())
        except Exception:  # noqa: BLE001
            pending = 0
        button.setText(
            f"Пропозиції до словника ({pending})" if pending
            else "Пропозиції до словника")

    def on_reset_prompt(self) -> None:
        """Повертає вбудовану інструкцію — але спершу питає, бо це затирає правки."""
        if _same_prompt(self.ed_vision_prompt.toPlainText(), VISION_PROMPT):
            return
        answer = QMessageBox.question(
            self, "Повернути типову інструкцію",
            "Свій текст інструкції буде замінено на вбудований. Продовжити?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.ed_vision_prompt.setPlainText(VISION_PROMPT)

    @staticmethod
    def _try_default_eagle(url: str, token: str):
        """Якщо вказана адреса мовчить — чи не працює Eagle на типовому порту?"""
        if url.rstrip("/") == EAGLE_DEFAULT_URL:
            return None
        try:
            return EagleClient(EAGLE_DEFAULT_URL, token).ping()
        except EagleError:
            return None

    @staticmethod
    def _eagle_advice(url: str, exc: Exception) -> str:
        """Замість голої мережевої помилки — що саме зробити."""
        text = str(exc).lower()
        if "41595" not in url:
            return ("Eagle слухає порт 41595. Вкажи http://localhost:41595 "
                    "і спробуй ще раз.")
        if "refused" in text or "недоступний" in text or "max retries" in text:
            return ("Eagle не відповідає. Він має бути запущений — API працює лише "
                    "поки програма відкрита.")
        if "token" in text or "401" in text or "403" in text:
            return "Eagle вимагає токен: Preferences → Developer → API token."
        return str(exc)

    def on_apply_schedule(self) -> None:
        self._collect_ui_into_config()
        self.cfg.save()
        if self.cfg.schedule_enabled and not self._warn_schedule_without_session():
            return
        self._apply_schedule(quiet=False)

    def _warn_schedule_without_session(self) -> bool:
        """Розклад без збереженої сесії — найчастіша причина тихих збоїв."""
        if self.sessionid:
            return True
        answer = QMessageBox.warning(
            self, APP_NAME,
            "Сесію Instagram ще не підключено.\n\n"
            "Запуск за розкладом працює без вікна і не зможе взяти кукі з Chrome "
            "чи Edge — вони зашифровані системою. Без збереженої сесії задача "
            "просто нічого не зробить.\n\n"
            "Спершу підключити сесію?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if answer == QMessageBox.Yes:
            self._open_session_tab()
            return False
        return True

    def _apply_schedule(self, quiet: bool = False) -> None:
        from .. import scheduler

        if self.cfg.schedule_enabled:
            result = scheduler.create_task(
                self.cfg.schedule_task_name,
                self.cfg.schedule_mode,
                self.cfg.schedule_time,
                self.cfg.schedule_interval_hours,
                self.cfg.schedule_weekday,
            )
        else:
            existing = scheduler.status(self.cfg.schedule_task_name)
            if not existing and quiet:
                return
            result = scheduler.delete(self.cfg.schedule_task_name)

        self.lbl_sched.setText(result.message)
        self.lbl_sched.setProperty("role", "ok" if result.ok else "err")
        self._restyle(self.lbl_sched)
        self._log(result.message)

    def on_run_task_now(self) -> None:
        from .. import scheduler

        result = scheduler.run_now(self.cfg.schedule_task_name)
        self.lbl_sched.setText(result.message)
        self.lbl_sched.setProperty("role", "ok" if result.ok else "err")
        self._restyle(self.lbl_sched)
        self._log(result.message)

    def _update_schedule_widgets(self) -> None:
        """Показує лише ті поля, які мають сенс для обраного режиму."""
        enabled = self.ck_schedule.isChecked()
        mode = self.cb_sched_mode.currentData() or SCHED_DAILY
        for widget in (self.cb_sched_mode, self.btn_sched_apply, self.btn_sched_run):
            widget.setEnabled(enabled or widget is self.btn_sched_apply)
        self.sp_sched_hours.setVisible(mode == SCHED_HOURLY)
        self.cb_weekday.setVisible(mode == SCHED_WEEKLY)
        self.ed_time.setVisible(mode != SCHED_ONLOGON)
        self.ed_time.setEnabled(enabled)
        self.sp_sched_hours.setEnabled(enabled)
        self.cb_weekday.setEnabled(enabled)

    def _update_schedule_label(self) -> None:
        from .. import scheduler

        info = scheduler.status(self.cfg.schedule_task_name)
        self.lbl_sched.setText(f"Стан задачі: {info}" if info else "Задачу ще не створено")
        self.lbl_sched.setProperty("role", "muted")
        self._restyle(self.lbl_sched)

    # ---------------------------------------------------------- обслуговування
    def on_make_shortcut(self) -> None:
        from .. import scheduler

        result = scheduler.create_shortcut()
        self._log(result.message)
        if result.ok:
            self._flash(self.btn_shortcut, "Створено ✓", "Ярлик на робочому столі")
        else:
            QMessageBox.warning(self, APP_NAME, result.message)

    def on_refresh_library(self) -> None:
        """Приводить уже завантажене до поточного шаблону імен і дописує теги."""
        if self.sync_worker and self.sync_worker.isRunning():
            QMessageBox.information(self, APP_NAME, "Спершу дочекайся синхронізації.")
            return

        self._collect_ui_into_config()
        self.cfg.save()
        answer = QMessageBox.question(
            self, APP_NAME,
            "Перейменувати вже завантажені файли за шаблоном\n"
            f"    {self.cfg.filename_template}\n"
            "і дописати опис та автора всередину?\n\n"
            "Нічого не перекачується, підписи беруться з локальної бази\n"
            "та з файлів у _metadata. Прев'ю і .json перейменуються слідом.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return

        self.tabs.setCurrentIndex(0)
        self.btn_refresh_library.setEnabled(False)
        self.btn_start.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.setFormat("Оновлюю бібліотеку…")
        self._log("═══ Оновлення назв і метаданих ═══")

        self.refresh_worker = RefreshWorker(self.cfg, self.state, self)
        self.refresh_worker.line.connect(self._log)
        self.refresh_worker.done.connect(self._on_refresh_library_done)
        self.refresh_worker.start()

    def _on_refresh_library_done(self, stats) -> None:
        self.btn_refresh_library.setEnabled(True)
        self.btn_start.setEnabled(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.progress.setFormat("Завершено")
        self.progress_label.setText(stats.summary())
        self._log(f"═══ {stats.summary()} ═══")
        self._notify(f"{APP_NAME}: бібліотеку оновлено", stats.summary())

    def on_push_eagle(self) -> None:
        """Заливає вже завантажене в Eagle — синхронізація до старих постів не доходить."""
        if self.push_worker and self.push_worker.isRunning():
            return
        self._collect_ui_into_config()
        self.cfg.save()

        self.tabs.setCurrentIndex(0)
        self.btn_push_eagle.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.setFormat("Заливаю в Eagle…")
        self._log("═══ Імпорт наявних файлів у Eagle ═══")

        self.push_worker = PushWorker(self.cfg, self.state, self)
        self.push_worker.line.connect(self._log)
        self.push_worker.done.connect(self._on_push_done)
        self.push_worker.start()

    def _on_push_done(self, stats) -> None:
        self.btn_push_eagle.setEnabled(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.progress.setFormat("Завершено")
        self.progress_label.setText(stats.summary())
        self._log(f"═══ {stats.summary()} ═══")
        self._notify(f"{APP_NAME}: імпорт у Eagle", stats.summary())

    def on_describe_library(self) -> None:
        """Дописує описи до вже зібраної бібліотеки.

        Звичайна синхронізація описує лише нові пости, а бібліотека зібрана
        раніше — і після чистки папки завантажень файли лишились тільки в Eagle.
        Тому й читаємо їх звідти.
        """
        if self.describe_worker and self.describe_worker.isRunning():
            self.describe_worker.stop()
            self._log("Зупиняю опис бібліотеки…")
            return
        self._collect_ui_into_config()
        self.cfg.save()

        if not self.cfg.vision_enabled:
            QMessageBox.information(
                self, APP_NAME,
                "Спершу увімкни візуальну модель у розділі «Модель» "
                "і перевір звʼязок із LM Studio.")
            return

        box = QMessageBox(self)
        box.setWindowTitle("Описати бібліотеку")
        box.setIcon(QMessageBox.Question)
        box.setText("Показати моделі те, що вже лежить у Eagle?")
        box.setInformativeText(
            "Кожен файл — окремий запит до моделі, тож на велику бібліотеку "
            "піде багато часу. Вже описане пропускається, зупинити можна "
            "будь-коли, а зроблене нікуди не дінеться.\n\n"
            "Радимо спершу спробувати на двадцяти."
        )
        first = box.addButton("Спробувати на 20", QMessageBox.AcceptRole)
        everything = box.addButton("Описати все", QMessageBox.AcceptRole)
        box.addButton("Скасувати", QMessageBox.RejectRole)
        box.setDefaultButton(first)
        box.exec()

        clicked = box.clickedButton()
        if clicked not in (first, everything):
            return
        limit = 20 if clicked is first else 0

        self.tabs.setCurrentIndex(0)
        self.btn_describe.setText("Зупинити опис")
        self.progress.setRange(0, 0)
        self.progress.setFormat("Описую бібліотеку…")
        self._log("═══ Опис бібліотеки ═══")

        self.describe_worker = DescribeWorker(self.cfg, self.state, limit=limit, parent=self)
        self.describe_worker.line.connect(self._log)
        self.describe_worker.done.connect(self._on_describe_done)
        self.describe_worker.start()

    def _on_describe_done(self, stats) -> None:
        self.btn_describe.setText(DESCRIBE_LABEL)
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.progress.setFormat("Завершено")
        self.progress_label.setText(stats.summary())
        self._log(f"═══ {stats.summary()} ═══")
        self._notify(f"{APP_NAME}: опис бібліотеки", stats.summary())

    def on_find_dupes(self) -> None:
        """Показує дублікати в Eagle. Видаляє тільки після окремої згоди."""
        if self.dupe_worker and self.dupe_worker.isRunning():
            self.dupe_worker.stop()
            return
        self._collect_ui_into_config()
        self.tabs.setCurrentIndex(0)
        self.progress.setRange(0, 0)
        self.progress.setFormat("Шукаю дублікати…")
        self._log("═══ Пошук дублікатів у Eagle ═══")
        self.btn_dupes.setEnabled(False)

        self.dupe_worker = DupeWorker(self.cfg, self.state, remove=False, parent=self)
        self.dupe_worker.line.connect(self._log)
        self.dupe_worker.done.connect(self._on_dupes_found)
        self.dupe_worker.start()

    def _on_dupes_found(self, stats) -> None:
        self.btn_dupes.setEnabled(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.progress.setFormat("Завершено")
        self.progress_label.setText(stats.summary())
        self._log(f"═══ {stats.summary()} ═══")
        if stats.error or not stats.extra:
            return

        answer = QMessageBox.question(
            self, "Дублікати в Eagle",
            f"Зайвих копій: {stats.extra} (постів із копіями: {stats.groups}).\n\n"
            "Відправити зайві в кошик Eagle? Найстарша копія кожного поста "
            "лишається — саме в неї могли потрапити твої правки.\n"
            "З кошика все можна дістати назад.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            self._log("Дублікати лишились на місці.")
            return

        self.progress.setRange(0, 0)
        self.progress.setFormat("Прибираю дублікати…")
        self.btn_dupes.setEnabled(False)
        self.dupe_worker = DupeWorker(self.cfg, self.state, remove=True, parent=self)
        self.dupe_worker.line.connect(self._log)
        self.dupe_worker.done.connect(self._on_dupes_removed)
        self.dupe_worker.start()

    def _on_dupes_removed(self, stats) -> None:
        self.btn_dupes.setEnabled(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.progress.setFormat("Завершено")
        self.progress_label.setText(stats.summary())
        self._log(f"═══ {stats.summary()} ═══")

    def on_clear_downloads(self) -> None:
        """Звільняє місце на диску, не втрачаючи памʼять про завантажене."""
        from ..maintenance import downloads_summary

        if self.cleanup_worker and self.cleanup_worker.isRunning():
            return
        if self.sync_worker and self.sync_worker.isRunning():
            QMessageBox.information(self, APP_NAME, "Спершу дочекайся синхронізації.")
            return

        self._collect_ui_into_config()
        self.cfg.save()
        count, size = downloads_summary(self.state)
        if not count:
            QMessageBox.information(self, APP_NAME, "Файлів застосунку на диску не знайдено.")
            return

        answer = QMessageBox.question(
            self, APP_NAME,
            f"Видалити {count} файл(ів) на {human_size(size)}?\n\n"
            "Застосунок запамʼятає, що ці пости вже завантажувались, тож наступна\n"
            "синхронізація не скачає їх повторно.\n\n"
            "Видаляється лише те, що створив сам застосунок. Eagle не чіпається —\n"
            "файли, вже імпортовані в бібліотеку, лежать у ній окремою копією.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        self.tabs.setCurrentIndex(0)
        self.btn_clear_downloads.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.setFormat("Чищу папку…")
        self._log("═══ Чистка папки завантажень ═══")

        self.cleanup_worker = CleanupWorker(self.cfg, self.state, self)
        self.cleanup_worker.line.connect(self._log)
        self.cleanup_worker.done.connect(self._on_cleanup_done)
        self.cleanup_worker.start()

    def _on_cleanup_done(self, stats) -> None:
        self.btn_clear_downloads.setEnabled(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.progress.setFormat("Завершено")
        self.progress_label.setText(stats.summary())
        self._log(f"═══ {stats.summary()} ═══")
        self._update_totals()
        self._refresh_review_badge()
        self._notify(f"{APP_NAME}: папку очищено", stats.summary())

    def on_reset_state(self) -> None:
        answer = QMessageBox.question(
            self, APP_NAME,
            "Очистити історію завантажень?\n\n"
            "Файли на диску залишаться, але наступна синхронізація вважатиме\n"
            "всі пости новими й перекачає їх заново.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.state.close()
        try:
            STATE_PATH.unlink(missing_ok=True)
            for extra in ("-wal", "-shm"):
                Path(str(STATE_PATH) + extra).unlink(missing_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, APP_NAME, f"Не вдалось видалити базу: {exc}")
        self.state = State(STATE_PATH)
        self._fill_table()
        self._update_totals()
        self._log("Історію завантажень очищено.")

    def on_reset_settings(self) -> None:
        answer = QMessageBox.question(
            self, APP_NAME,
            "Повернути всі налаштування до типових? Сесія Instagram залишиться.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.cfg = Config()
        self.cfg.save()
        self._load_config_into_ui()
        self._log("Налаштування скинуто до типових.")

    # ------------------------------------------------------------- сесія
    def on_find_cookie(self) -> None:
        browser = self.cb_browser.currentData() or "auto"
        self.btn_find_cookie.setEnabled(False)
        self.btn_find_cookie.setText("Шукаю…")
        self._session_log(f"Шукаю кукі instagram.com ({BROWSER_LABELS.get(browser, browser)})…")
        self.cookie_worker = CookieWorker(browser, self)
        self.cookie_worker.done.connect(self._on_cookie_found)
        self.cookie_worker.start()

    def _on_cookie_found(self, result) -> None:
        self.btn_find_cookie.setEnabled(True)
        self.btn_find_cookie.setText("Знайти сесію")
        for note in result.notes:
            self._session_log("  " + note)
        if result.ok:
            self.ed_session.setText(result.sessionid)
            self._session_log("Сесію знайдено — перевіряю…")
            self.on_verify_session()
            return

        if result.hint:
            self._session_log("")
            self._session_log(result.hint)
        self.lbl_session.setText(
            "Автоматично не вийшло — встав sessionid у поле вище. "
            "Подробиці нижче в журналі."
        )
        self.lbl_session.setProperty("role", "warn")
        self._restyle(self.lbl_session)
        self.ed_session.setFocus()

    def on_cookies_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Файл cookies.txt", str(Path.home()), "Cookies (*.txt);;Усі файли (*.*)"
        )
        if not path:
            return
        try:
            value = sessionid_from_cookies_txt(path)
        except Exception as exc:  # noqa: BLE001
            self._session_log(f"Не вдалось прочитати файл: {exc}")
            return
        if value:
            self.ed_session.setText(value)
            self._session_log("sessionid зчитано з файлу — перевіряю…")
            self.on_verify_session()
        else:
            self._session_log("У файлі немає cookie sessionid для instagram.com.")

    def on_verify_session(self) -> None:
        value = normalize_sessionid(self.ed_session.text())
        if not value:
            self._session_log("Порожнє поле sessionid.")
            return
        self.btn_verify.setEnabled(False)
        self.btn_verify.setText("Перевіряю…")
        self.connect_worker = ConnectWorker(value, self)
        self.connect_worker.line.connect(self._session_log)
        self.connect_worker.done.connect(lambda user: self._on_session_ok(value, user))
        self.connect_worker.failed.connect(self._on_session_failed)
        self.connect_worker.start()

    def _on_session_ok(self, sessionid: str, username: str) -> None:
        self.btn_verify.setEnabled(True)
        self.btn_verify.setText("Перевірити")
        self.sessionid = sessionid
        self.username = username
        save_session({
            "sessionid": sessionid,
            "username": username,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        })
        self.lbl_session.setText(f"Підключено як @{username}. Сесію збережено.")
        self.lbl_session.setProperty("role", "ok")
        self._restyle(self.lbl_session)
        self._session_log(f"✓ Підключено як @{username}")
        self._refresh_status()
        if not self.collections:
            self.on_refresh_collections()

    def _on_session_failed(self, message: str) -> None:
        self.btn_verify.setEnabled(True)
        self.btn_verify.setText("Перевірити")
        self.lbl_session.setText(message)
        self.lbl_session.setProperty("role", "err")
        self._restyle(self.lbl_session)
        self._session_log(f"✖ {message}")

    def on_forget_session(self) -> None:
        clear_session()
        self.sessionid = ""
        self.username = ""
        self.ed_session.clear()
        self.lbl_session.setText("Сесію видалено.")
        self.lbl_session.setProperty("role", "muted")
        self._restyle(self.lbl_session)
        self._refresh_status()

    # -------------------------------------------------------- підбірки
    def on_refresh_collections(self) -> None:
        if not self._require_session():
            return
        self.btn_refresh.setEnabled(False)
        self.btn_refresh.setText("Оновлюю…")
        self._log("Отримую список підбірок…")
        self.collections_worker = CollectionsWorker(self.sessionid, self)
        self.collections_worker.line.connect(self._log)
        self.collections_worker.done.connect(self._on_collections)
        self.collections_worker.failed.connect(self._on_collections_failed)
        self.collections_worker.start()

    def _on_collections(self, collections: List[CollectionInfo]) -> None:
        self.btn_refresh.setEnabled(True)
        self.btn_refresh.setText("Оновити підбірки")
        self.collections = collections
        for col in collections:
            self.state.upsert_collection(col.pk, col.display, col.media_count)
        self._fill_table()
        self._log(f"Підбірок у списку: {len(collections)}")

    def _on_collections_failed(self, message: str) -> None:
        self.btn_refresh.setEnabled(True)
        self.btn_refresh.setText("Оновити підбірки")
        self._log(f"✖ {message}")
        QMessageBox.warning(self, APP_NAME, message)

    def _fill_table(self) -> None:
        enabled = set(self.cfg.enabled_collections)
        self.table.setRowCount(len(self.collections))
        for row, col in enumerate(self.collections):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            should_check = (
                self.cfg.sync_all_saved if col.is_all_saved
                else (self.cfg.sync_all_collections or col.pk in enabled)
            )
            check.setCheckState(Qt.Checked if should_check else Qt.Unchecked)
            check.setData(Qt.UserRole, col.pk)
            self.table.setItem(row, 0, check)

            name = QTableWidgetItem(col.display)
            name.setData(Qt.UserRole, col.pk)
            self.table.setItem(row, 1, name)

            total = QTableWidgetItem(str(col.media_count or "—"))
            total.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 2, total)

            done = QTableWidgetItem(str(self.state.collection_downloaded_count(col.pk)))
            done.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 3, done)

    def _checked_pks(self) -> List[str]:
        result = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.checkState() == Qt.Checked:
                result.append(str(item.data(Qt.UserRole)))
        return result

    def _set_all_checks(self, checked: bool) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item:
                item.setCheckState(Qt.Checked if checked else Qt.Unchecked)

    # ------------------------------------------------------ синхронізація
    def on_start(self) -> None:
        if not self._require_session():
            return
        if not self.collections:
            self._log("Спершу онови список підбірок.")
            self.on_refresh_collections()
            return
        selected = self._checked_pks()
        if not selected:
            QMessageBox.information(self, APP_NAME, "Познач хоча б одну підбірку.")
            return

        self._collect_ui_into_config()
        self.cfg.save()

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_refresh.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.setFormat("Працюю…")
        self._log("═══ Старт синхронізації ═══")

        self.sync_worker = SyncWorker(self.cfg, self.state, self.sessionid, selected, self)
        self.sync_worker.line.connect(self._log)
        self.sync_worker.progress.connect(self._on_progress)
        self.sync_worker.done.connect(self._on_sync_done)
        self.sync_worker.start()

    def on_stop(self) -> None:
        if self.sync_worker:
            self.sync_worker.stop()
            self.btn_stop.setEnabled(False)
            self._log("Зупиняю після поточного файлу…")

    def _on_progress(self, message: str, current: int, total: int) -> None:
        self.progress_label.setText(message)
        if total and total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(min(current, total))
            self.progress.setFormat(f"{current} / {total}")
        else:
            self.progress.setFormat(f"{current} переглянуто")

    def _on_sync_done(self, stats) -> None:
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_refresh.setEnabled(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(100 if not stats.errors else 0)
        self.progress.setFormat("Завершено")
        self.progress_label.setText(stats.summary())
        self._log(f"═══ Підсумок: {stats.summary()} ═══")
        self._fill_table()
        self._update_totals()
        status.write(
            status.FAILED if stats.errors else status.OK,
            source="gui", summary=stats.summary(), errors=stats.errors,
        )
        if not stats.errors:
            self.banner.setVisible(False)
        self._refresh_review_badge()
        self._notify(
            f"{APP_NAME}: синхронізацію завершено",
            stats.summary() + (f"\n{stats.errors[0]}" if stats.errors else ""),
        )

    # =============================================================== дрібне
    def _require_session(self) -> bool:
        if self.sessionid:
            return True
        QMessageBox.information(
            self, APP_NAME,
            "Спершу підключи сесію Instagram на вкладці «Сесія»."
        )
        self._open_session_tab()
        return False

    def _refresh_status(self) -> None:
        if self.username:
            self.status_label.setText(f"@{self.username}")
            self.status_label.setProperty("role", "ok")
        elif self.sessionid:
            self.status_label.setText("сесія збережена")
            self.status_label.setProperty("role", "muted")
        else:
            self.status_label.setText("сесію не підключено")
            self.status_label.setProperty("role", "warn")
        self._restyle(self.status_label)

    def _open_session_tab(self) -> None:
        """Не за номером: вкладок побільшало, і жорсткий індекс уже двічі
        вказував не туди після кожної нової."""
        self.tabs.setCurrentIndex(getattr(self, "session_index", self.tabs.count() - 1))

    def _set_review_badge(self, pending: int) -> None:
        """Тільки напис на вкладці. Викликається щоразу, коли черга змінилась.

        Вкладка встигає повідомити про кількість ще під час власної побудови,
        коли посилання на неї у вікні ще немає — тому перевіряємо явно.
        """
        tab = getattr(self, "review_tab", None)
        if tab is None:
            return
        index = self.tabs.indexOf(tab)
        if index >= 0:
            self.tabs.setTabText(index, f"Ревʼю ({pending})" if pending else "Ревʼю")

    def _refresh_review_badge(self) -> None:
        """Перечитати чергу з бази — після синхронізації та на старті."""
        try:
            self.review_tab.reload()
        except Exception:  # noqa: BLE001
            pass

    def _update_totals(self) -> None:
        totals = self.state.totals()
        self.lbl_totals.setText(
            f"У бібліотеці: {totals['media']} постів · {totals['files']} файлів · "
            f"{human_size(totals['bytes'])}"
        )

    # ------------------------------------------------------------------ банер
    def _show_banner(self, title: str, text: str, action: str = "", role: str = "banner") -> None:
        self.banner.setProperty("role", role)
        self._restyle(self.banner)
        self.banner_title.setText(title)
        self.banner_text.setText(text)
        self.btn_banner_action.setText(action or "Підключити сесію")
        self.btn_banner_action.setVisible(bool(action))
        self.banner.setVisible(True)

    def _dismiss_banner(self) -> None:
        self.banner.setVisible(False)
        status.clear()

    def _check_last_run(self) -> None:
        """Якщо попередній (найчастіше фоновий) запуск впав — сказати про це вголос."""
        last = status.read()
        if not last or not last.failed:
            return
        when = f" · {last.when_human}" if last.when_human else ""
        where = "Запуск за розкладом" if last.source == "scheduled" else "Попередній запуск"
        detail = last.advice or last.summary or (last.errors[0] if last.errors else "")
        action = "Підключити сесію" if last.result == status.NO_SESSION else ""
        self._show_banner(f"{last.headline}{when}", f"{where}: {detail}", action)
        self._log(f"⚠ {last.headline}: {detail}")

    def _startup_hints(self) -> None:
        self._log(f"{APP_NAME} {__version__}")
        self._refresh_review_badge()
        self._check_last_run()
        if self.cfg.start_minimized:
            self.hide()
        if not self.sessionid:
            self._log("Сесію не підключено — відкрий вкладку «Сесія».")
            self._open_session_tab()
            return

        self._log(f"Сесія збережена{f' (@{self.username})' if self.username else ''}.")
        self.on_refresh_collections()
        if self.cfg.sync_on_launch:
            self._log("Автосинхронізація при запуску…")
            QTimer.singleShot(4000, self._autostart_sync)

    def _autostart_sync(self) -> None:
        if self.sync_worker and self.sync_worker.isRunning():
            return
        if not self.collections:  # список ще не приїхав — почекаємо ще трохи
            QTimer.singleShot(4000, self._autostart_sync)
            return
        self.on_start()

    def _log(self, message: str) -> None:
        self.log_view.appendPlainText(f"[{datetime.now():%H:%M:%S}] {message}")

    def _session_log(self, message: str) -> None:
        self.session_log.appendPlainText(message)

    def _flash(self, button: QPushButton, temp: str, original: str) -> None:
        button.setText(temp)
        QTimer.singleShot(1400, lambda: button.setText(original))

    @staticmethod
    def _restyle(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    @staticmethod
    def _open_path(path: Path) -> None:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(path))  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt API
        # Згортання в трей замість виходу (якщо це не команда «Вийти»)
        if not self._quitting and self.cfg.minimize_to_tray and self.tray.isVisible():
            event.ignore()
            self.hide()
            self.tray.showMessage(
                APP_NAME,
                "Застосунок згорнуто в трей. Правий клік на іконці — меню.",
                QSystemTrayIcon.Information, 3000,
            )
            return

        if self.sync_worker and self.sync_worker.isRunning():
            answer = QMessageBox.question(
                self, APP_NAME,
                "Синхронізація ще триває. Зупинити й вийти?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self.sync_worker.stop()
            self.sync_worker.wait(8000)
        self._collect_ui_into_config()
        self.cfg.save()
        self.state.close()
        self.tray.hide()
        event.accept()
