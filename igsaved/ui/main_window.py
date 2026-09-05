"""Головне вікно застосунку: бічна панель розділів + сторінки.

Композиція сторінок тут, вміст блоків — у pages.py (PagesMixin), спільні
дрібні віджети — у widgets.py. Обробники дій лишаються тут, бо тримають стан
вікна (воркери, конфіг, базу).
"""

from __future__ import annotations

import os
import subprocess
import sys
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QSize, Qt, QTime, QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QStackedWidget, QStyle, QSystemTrayIcon, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import APP_NAME, __version__, status
from ..config import (
    ALL_POSTS_PK, Config, DEFAULT_TEMPLATE, FROZEN, LIKED_PK, LOG_DIR, SCHED_DAILY,
    SCHED_HOURLY, SCHED_ONLOGON, SCHED_WEEKLY, STATE_PATH, STRUCTURE_FLAT, app_dir, clear_session, load_session, resource_dir,
    save_session,
)
from ..downloader import human_size
from ..eagle import EagleClient, EagleError
from ..instagram import CollectionInfo
from ..session import BROWSER_LABELS, normalize_sessionid, sessionid_from_cookies_txt
from ..state import State
from ..vision import (
    DEFAULT_PROMPT as VISION_PROMPT,
    MAX_FRAMES as VISION_MAX_FRAMES,
    SAFE_FRAMES as VISION_SAFE_FRAMES,
)
from .pages import DESCRIBE_LABEL, PagesMixin
from .review_tab import ReviewTab
from .widgets import _csv, _label, _same_prompt, _scrollable, _stack_page, _subtabs
from .workers import (
    CleanupWorker, CollectionsWorker, ConnectWorker, CookieWorker, DescribeWorker,
    DupeWorker, HealthWorker, NormalizeWorker, PushWorker, RefreshWorker, SyncWorker,
    UpdateWorker, UpgradeWorker, UrlWorker,
)

EAGLE_DEFAULT_URL = "http://localhost:41595"

# Розділи бічної панелі — у цьому порядку. Індекси використовуються в _go().
PAGE_OVERVIEW, PAGE_SYNC, PAGE_REVIEW, PAGE_MODEL, PAGE_EAGLE, PAGE_ACCOUNT, \
    PAGE_MAINTENANCE, PAGE_ABOUT = range(8)
PAGE_TITLES = ["Огляд", "Синхронізація", "Перегляд", "Модель", "Eagle",
               "Акаунт", "Обслуговування", "Про застосунок"]


class MainWindow(PagesMixin, QMainWindow):
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
        self.normalize_worker: Optional[NormalizeWorker] = None
        self.dupe_worker: Optional[DupeWorker] = None
        self.url_worker: Optional[UrlWorker] = None
        self.health_worker: Optional[HealthWorker] = None
        self.update_worker: Optional[UpdateWorker] = None
        self.upgrade_worker: Optional[UpgradeWorker] = None
        self._manual_check = False

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
        QTimer.singleShot(500, self._check_health)
        QTimer.singleShot(2500, self._check_updates)

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
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        # Бічна панель: усі розділи на одному рівні, кожен — одна тема.
        self.sidebar = QListWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(196)
        for title in PAGE_TITLES:
            item = QListWidgetItem(title)
            item.setSizeHint(QSize(0, 40))
            self.sidebar.addItem(item)
        body.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        self.review_tab = ReviewTab(self.cfg, self.state, self._log,
                                    on_change=self._set_review_badge)
        for builder in (self._page_overview, self._page_sync, lambda: self.review_tab,
                        self._page_model, self._page_eagle, self._page_account,
                        self._page_maintenance, self._page_about):
            self.stack.addWidget(builder())
        self.sidebar.currentRowChanged.connect(self._on_page_changed)
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)

        # Нижня смуга: підсумок бібліотеки й одна кнопка збереження на всі сторінки.
        bottom = QFrame()
        bottom.setObjectName("bottombar")
        bar = QHBoxLayout(bottom)
        bar.setContentsMargins(14, 6, 14, 6)
        self.lbl_totals = _label("", "muted")
        self.status_label = _label("", "muted")
        self.status_label.setWordWrap(False)
        self.btn_save = QPushButton("Зберегти налаштування")
        self.btn_save.setProperty("role", "primary")
        self.btn_save.clicked.connect(self.on_save_settings)
        bar.addWidget(self.lbl_totals, 1)
        bar.addWidget(self.status_label)
        bar.addWidget(self.btn_save)
        outer.addWidget(bottom)

        self.setCentralWidget(root)
        self._set_review_badge(self.state.review_count())
        self.sidebar.setCurrentRow(PAGE_OVERVIEW)

    # ------------------------------------------------------------ навігація
    def _go(self, page: int, subtab: Optional[int] = None) -> None:
        self.sidebar.setCurrentRow(page)
        widget = self.stack.widget(page)
        tabs = getattr(widget, "subtabs", None)
        if tabs is not None and subtab is not None:
            tabs.setCurrentIndex(subtab)

    def _on_page_changed(self, index: int) -> None:
        """Перехід між розділами зберігає правки: забуте «Зберегти» коштувало
        не одному налаштуванню."""
        self.stack.setCurrentIndex(index)
        if getattr(self, "_ui_loaded", False):
            self._collect_ui_into_config()
            self.cfg.save()
        self.btn_save.setVisible(index not in (PAGE_OVERVIEW, PAGE_REVIEW, PAGE_ABOUT))
        if index == PAGE_REVIEW:
            self.review_tab.setFocus()
        if index == PAGE_MODEL and not getattr(self, "_models_loaded", False):
            self._check_health()

    def _open_session_tab(self) -> None:
        self._go(PAGE_ACCOUNT, 0)

    def _open_settings(self, page: int) -> None:
        """Сумісність зі старими викликами: номер сторінки старих налаштувань."""
        mapping = {0: (PAGE_SYNC, 1), 1: (PAGE_SYNC, 2), 2: (PAGE_SYNC, 3),
                   3: (PAGE_MODEL, 0), 4: (PAGE_EAGLE, 0), 5: (PAGE_ACCOUNT, 2),
                   6: (PAGE_MAINTENANCE, 0)}
        target, sub = mapping.get(page, (PAGE_SYNC, 0))
        self._go(target, sub)

    # ------------------------------------------------------------- сторінки
    def _page_overview(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        head = QHBoxLayout()
        head.addWidget(_label(APP_NAME, "h1"))
        head.addStretch(1)
        self.btn_health = QPushButton("Перевірити звʼязок")
        self.btn_health.setToolTip("Ще раз запитати Eagle і LM Studio")
        self.btn_health.clicked.connect(self._check_health)
        head.addWidget(self.btn_health)
        layout.addLayout(head)

        # Три речі, без яких конвеєр не працює — картками, з дорогою до налаштувань.
        cards = QHBoxLayout()
        cards.setSpacing(10)
        self.ind_session = self._indicator("Сесія", self._open_session_tab)
        self.ind_eagle = self._indicator("Eagle", lambda: self._go(PAGE_EAGLE, 0))
        self.ind_model = self._indicator("Модель", lambda: self._go(PAGE_MODEL, 0))
        for card in (self.ind_session, self.ind_eagle, self.ind_model):
            cards.addWidget(card, 1)
        layout.addLayout(cards)

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

        self.update_notice = QFrame()
        self.update_notice.setProperty("role", "notice")
        notice = QHBoxLayout(self.update_notice)
        notice.setContentsMargins(12, 8, 10, 8)
        self.update_text = _label("", "title")
        notice.addWidget(self.update_text, 1)
        self.btn_update_go = QPushButton("Оновити")
        self.btn_update_go.clicked.connect(lambda: self._go(PAGE_ABOUT))
        self.btn_update_close = QPushButton("Пізніше")
        self.btn_update_close.clicked.connect(lambda: self.update_notice.setVisible(False))
        notice.addWidget(self.btn_update_go)
        notice.addWidget(self.btn_update_close)
        self.update_notice.setVisible(False)
        self._update_url = ""
        self._latest = None
        layout.addWidget(self.update_notice)

        # Стан: останній запуск і черга — те, заради чого відкривають вікно.
        state_row = QHBoxLayout()
        self.lbl_last_run = _label("", "muted")
        state_row.addWidget(self.lbl_last_run, 1)
        self.btn_goto_review = QPushButton("Перегляд")
        self.btn_goto_review.clicked.connect(lambda: self._go(PAGE_REVIEW))
        state_row.addWidget(self.btn_goto_review)
        layout.addLayout(state_row)

        layout.addWidget(_label("Журнал", "h2"))
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("log")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(4000)
        layout.addWidget(self.log_view, 1)

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
        self.btn_start.setMinimumHeight(36)
        self.btn_start.clicked.connect(self.on_start)
        controls.addWidget(self.btn_stop)
        controls.addWidget(self.btn_start)
        layout.addLayout(controls)
        return page

    def _page_sync(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(8)
        layout.addWidget(_label("Синхронізація", "h1"))
        collections = self._sec_collections()
        page.subtabs = _subtabs(
            ("Що качати", _stack_page("", self._sec_download_what())),
            ("Обхід", _stack_page("", self._sec_scan())),
            ("Лайки та фільтр", _stack_page("", self._sec_liked())),
        )
        page.subtabs.insertTab(0, collections, "Підбірки")
        page.subtabs.setCurrentIndex(0)
        layout.addWidget(page.subtabs, 1)
        return page

    def _page_model(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(8)
        layout.addWidget(_label("Візуальна модель", "h1"))
        page.subtabs = _subtabs(
            ("Підключення", _stack_page("", self._sec_vision_connection())),
            ("Кадри", _stack_page("", self._sec_vision_frames())),
            ("Рішення", _stack_page("", self._sec_vision_decisions())),
            ("Інструкція", _stack_page("", self._sec_vision_prompt())),
            ("Словник", _stack_page("", self._sec_vocab())),
        )
        layout.addWidget(page.subtabs, 1)
        return page

    def _page_eagle(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(8)
        layout.addWidget(_label("Eagle", "h1"))
        page.subtabs = _subtabs(
            ("Підключення", _stack_page("", self._sec_eagle_connect())),
            ("Теги й нотатка", _stack_page("", self._sec_eagle_tags())),
            ("Прибирання й дублікати", _stack_page("", self._sec_eagle_cleanup())),
            ("Бібліотека", _stack_page("", self._sec_eagle_library())),
        )
        layout.addWidget(page.subtabs, 1)
        return page

    def _page_account(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(8)
        layout.addWidget(_label("Акаунт", "h1"))
        page.subtabs = _subtabs(
            ("Сесія", _stack_page("", self._sec_session())),
            ("Захист", _stack_page("", self._sec_protection())),
            ("Розклад", _stack_page("", self._sec_schedule())),
        )
        layout.addWidget(page.subtabs, 1)
        return page

    def _page_maintenance(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(8)
        layout.addWidget(_label("Обслуговування", "h1"))
        page.subtabs = _subtabs(
            ("Файли й база", _stack_page("", self._sec_files())),
            ("Мережа", _stack_page("", self._sec_network())),
        )
        layout.addWidget(page.subtabs, 1)
        return page

    def _page_about(self) -> QWidget:
        return _scrollable(_stack_page("", self._sec_about()))

    def _indicator(self, title: str, on_click) -> QWidget:
        """Картка стану: крапка, заголовок, деталь і кнопка до налаштувань."""
        box = QFrame()
        box.setProperty("role", "status")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)
        head = QHBoxLayout()
        dot = QLabel("●")
        dot.setProperty("role", "muted")
        caption = QLabel(title)
        caption.setProperty("role", "title")
        head.addWidget(dot)
        head.addWidget(caption)
        head.addStretch(1)
        layout.addLayout(head)
        text = QLabel("…")
        text.setProperty("role", "muted")
        text.setWordWrap(True)
        layout.addWidget(text)
        button = QPushButton("Налаштувати")
        button.setFlat(True)
        button.clicked.connect(on_click)
        layout.addWidget(button, alignment=Qt.AlignLeft)
        box.dot, box.text, box.button, box.title = dot, text, button, title  # type: ignore[attr-defined]
        return box

    @staticmethod
    def _set_indicator(box: QWidget, ok, title: str, detail: str) -> None:
        role = "ok" if ok else ("muted" if ok is None else "err")
        box.dot.setProperty("role", role)
        box.dot.style().unpolish(box.dot)
        box.dot.style().polish(box.dot)
        box.text.setText(detail)
        box.text.setToolTip(detail)
        box.button.setVisible(not ok)

    def _check_updates(self) -> None:
        if not self.cfg.check_updates:
            return
        last = self.state.get_meta("last_update_check")
        if last:
            try:
                from datetime import timedelta, timezone
                when = datetime.fromisoformat(last)
                if datetime.now(timezone.utc) - when < timedelta(hours=20):
                    return
            except ValueError:
                pass
        self.update_worker = UpdateWorker(__version__, self)
        self.update_worker.done.connect(self._on_update_checked)
        self.update_worker.start()

    def _on_update_checked(self, latest) -> None:
        from datetime import timezone
        self.state.set_meta("last_update_check",
                            datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self._latest = latest or None
        if not latest:
            if getattr(self, "_manual_check", False):
                self.lbl_update.setText(f"У тебе найновіша версія — {__version__}.")
            self._manual_check = False
            self.btn_check_update.setEnabled(True)
            return
        self._update_url = latest.get("url") or ""
        self.update_text.setText(
            f"Є нова версія {latest.get('version')} (у тебе {__version__}).")
        self.update_notice.setVisible(True)
        self.lbl_update.setText(
            f"Доступна версія {latest.get('version')} (у тебе {__version__}).")
        self.btn_update.setText(f"Оновити до {latest.get('version')}")
        self.btn_update.setEnabled(True)
        self.btn_check_update.setEnabled(True)
        from ..updates import plain_notes

        notes = plain_notes(latest.get("notes") or "")
        self.update_notes.setPlainText(notes or "Опису релізу немає.")
        self.update_notes.setVisible(True)
        self._manual_check = False
        self._log(f"Доступне оновлення InstRef {latest.get('version')}: {self._update_url}")

    def on_check_updates_now(self) -> None:
        self._manual_check = True
        self.lbl_update.setText("Питаю GitHub…")
        self.btn_check_update.setEnabled(False)
        self.update_worker = UpdateWorker(__version__, self)
        self.update_worker.done.connect(self._on_update_checked)
        self.update_worker.start()

    def on_update_now(self) -> None:
        """Одна кнопка: скачати й поставити. Далі застосунок сам закриється."""
        latest = getattr(self, "_latest", None)
        if not latest:
            return
        if self.sync_worker and self.sync_worker.isRunning():
            QMessageBox.information(self, APP_NAME, "Дочекайся кінця синхронізації.")
            return
        what = ("Інсталятор завантажиться і запуститься; застосунок закриється й "
                "відкриється вже новим." if FROZEN else
                "Код заміниться на новий, робочі файли (налаштування, база, сесія, "
                "словник) лишаться, залежності оновляться, застосунок перезапуститься.")
        answer = QMessageBox.question(
            self, APP_NAME,
            f"Оновити до {latest.get('version')}?\n\n{what}",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return
        self._collect_ui_into_config()
        self.cfg.save()
        self.btn_update.setEnabled(False)
        self.btn_check_update.setEnabled(False)
        self.update_progress.setVisible(True)
        self.update_progress.setRange(0, 0)
        self._log(f"═══ Оновлення до {latest.get('version')} ═══")
        self.upgrade_worker = UpgradeWorker(latest, self)
        self.upgrade_worker.progress.connect(self._on_update_progress)
        self.upgrade_worker.line.connect(self._log)
        self.upgrade_worker.done.connect(self._on_update_done)
        self.upgrade_worker.start()

    def _on_update_progress(self, message: str, done: int, total: int) -> None:
        self.lbl_update.setText(message)
        if total > 0:
            self.update_progress.setRange(0, total)
            self.update_progress.setValue(min(done, total))
        else:
            self.update_progress.setRange(0, 0)

    def _on_update_done(self, ok: bool, message: str) -> None:
        self.update_progress.setVisible(False)
        self.btn_check_update.setEnabled(True)
        if not ok:
            self.btn_update.setEnabled(True)
            self.lbl_update.setText(message)
            self._log(f"✖ Оновлення не вдалося: {message}")
            QMessageBox.warning(self, APP_NAME, f"Оновлення не вдалося.\n\n{message}")
            return
        self.lbl_update.setText(message)
        self._log(message)
        if not FROZEN:
            from ..updater import restart_from_source
            try:
                restart_from_source()
            except OSError as exc:
                self._log(f"Не вдалось перезапустити сам: {exc}. Запусти InstRef ще раз.")
        QTimer.singleShot(800, self._quit_app)

    def _open_url(self, url: str) -> None:
        webbrowser.open(url)

    def on_open_logs(self) -> None:
        self._open_path(LOG_DIR)

    def on_open_backups(self) -> None:
        self._open_path(self.state.backup_dir)

    def on_open_appdir(self) -> None:
        self._open_path(app_dir())

    def _check_health(self) -> None:
        if self.sessionid:
            self._set_indicator(self.ind_session, True, "Сесія",
                                f"@{self.username}" if self.username else "збережена")
        else:
            self._set_indicator(self.ind_session, False, "Сесія", "не підключена")
        if not self.cfg.eagle_enabled:
            self._set_indicator(self.ind_eagle, None, "Eagle", "імпорт вимкнено")
        if self.health_worker and self.health_worker.isRunning():
            return
        self.health_worker = HealthWorker(self.cfg, self)
        self.health_worker.done.connect(self._on_health)
        self.health_worker.start()

    def _fill_models(self, models: list) -> None:
        """Список моделей LM Studio у випадайку — візуальні першими, текстові з поміткою.

        Раніше список зʼявлявся лише після кнопки «Перевірити», і в поле легко
        потрапляла текстова модель, набрана руками.
        """
        from ..vision import looks_visual

        combo = self.cb_vision_model
        current = combo.currentText().strip()
        combo.blockSignals(True)
        combo.clear()
        ordered = sorted(models, key=lambda m: (not looks_visual(m), m.lower()))
        for name in ordered:
            combo.addItem(name if looks_visual(name) else f"{name}   — текстова", name)
        combo.setCurrentText(current)
        combo.blockSignals(False)
        self._models_loaded = bool(models)

    def _on_health(self, result: dict) -> None:
        if "models" in result:
            self._fill_models(result.get("models") or [])
        ok, text = result.get("eagle", (False, "?"))
        if self.cfg.eagle_enabled:
            self._set_indicator(self.ind_eagle, ok, "Eagle", text if ok else "не відповідає")
            self.ind_eagle.text.setToolTip(text)
        ok, text = result.get("model", (None, "?"))
        self._set_indicator(self.ind_model, ok, "Модель", text)

    def on_download_urls(self) -> None:
        """Другий вхід у конвеєр: конкретні пости за посиланнями."""
        if not self._require_session():
            return
        if self.url_worker and self.url_worker.isRunning():
            self.url_worker.stop()
            self._log("Зупиняю завантаження за посиланнями…")
            return
        from PySide6.QtWidgets import QDialog, QDialogButtonBox

        dialog = QDialog(self)
        dialog.setWindowTitle("Завантажити за посиланням")
        dialog.resize(560, 300)
        box = QVBoxLayout(dialog)
        box.addWidget(_label(
            "Адреси постів або reels — по одній на рядок. Пройдуть той самий шлях: "
            "завантаження, опис моделлю, Eagle. У бібліотеці — полиця «За посиланням».",
            "muted"))
        editor = QPlainTextEdit()
        editor.setPlaceholderText("https://www.instagram.com/reel/…\nhttps://www.instagram.com/p/…")
        box.addWidget(editor, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        box.addWidget(buttons)
        if dialog.exec() != QDialog.Accepted:
            return
        urls = [line.strip() for line in editor.toPlainText().splitlines()
                if "instagram.com" in line]
        if not urls:
            self._log("Жодного посилання на Instagram.")
            return
        self._collect_ui_into_config()
        self.cfg.save()
        self.btn_start.setEnabled(False)
        self.progress.setRange(0, len(urls))
        self.progress.setFormat("За посиланням…")
        self._log(f"═══ За посиланням: {len(urls)} ═══")
        self.url_worker = UrlWorker(self.cfg, self.state, self.sessionid, urls, self)
        self.url_worker.line.connect(self._log)
        self.url_worker.progress.connect(self._on_progress)
        self.url_worker.done.connect(self._on_sync_done)
        self.url_worker.start()


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
        self.sp_cooldown.setValue(cfg.min_hours_between_runs)
        self.sp_rate_cooldown.setValue(cfg.rate_limit_cooldown_hours)
        self.sp_attempts.setValue(cfg.max_post_attempts)
        self.sp_jitter.setValue(cfg.schedule_jitter_minutes)
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
        self.sp_sec_per_frame.setValue(cfg.vision_seconds_per_frame)
        self.ck_by_scene.setChecked(cfg.vision_frames_by_scene)
        self.sp_backlog.setValue(cfg.describe_backlog_per_run)
        index = self.cb_dupe.findData(cfg.dupe_action or "review")
        self.cb_dupe.setCurrentIndex(index if index >= 0 else 0)
        self.sp_dupe_dist.setValue(cfg.dupe_max_distance)
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
        self.ck_eagle_cleanup.setChecked(cfg.eagle_delete_local_after_import)
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
        self.ck_updates.setChecked(cfg.check_updates)
        self.ck_transcribe.setChecked(cfg.transcribe_enabled)
        self.ed_whisper.setText(cfg.transcribe_model)

        browser_index = self.cb_browser.findData(cfg.browser)
        self.cb_browser.setCurrentIndex(max(0, browser_index))
        self._update_schedule_widgets()
        self._update_schedule_label()
        self._update_totals()
        self._update_tag_badge()
        self._update_last_run()
        self._ui_loaded = True

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
        cfg.min_hours_between_runs = self.sp_cooldown.value()
        cfg.rate_limit_cooldown_hours = self.sp_rate_cooldown.value()
        cfg.max_post_attempts = self.sp_attempts.value()
        cfg.schedule_jitter_minutes = self.sp_jitter.value()
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
        chosen_model = self.cb_vision_model.currentText().strip()
        index = self.cb_vision_model.findText(chosen_model)
        if index >= 0 and self.cb_vision_model.itemData(index):
            chosen_model = str(self.cb_vision_model.itemData(index))
        cfg.vision_model = chosen_model.split("   — ")[0].strip()
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
        cfg.vision_seconds_per_frame = self.sp_sec_per_frame.value()
        cfg.vision_frames_by_scene = self.ck_by_scene.isChecked()
        cfg.describe_backlog_per_run = self.sp_backlog.value()
        cfg.dupe_action = str(self.cb_dupe.currentData() or "review")
        cfg.dupe_max_distance = self.sp_dupe_dist.value()
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
        cfg.eagle_delete_local_after_import = self.ck_eagle_cleanup.isChecked()
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
        cfg.check_updates = self.ck_updates.isChecked()
        cfg.transcribe_enabled = self.ck_transcribe.isChecked()
        cfg.transcribe_model = self.ed_whisper.text().strip() or "small"
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

        from ..vision import looks_visual

        self._fill_models(models)
        chosen = self.cb_vision_model.currentText().strip()
        if not chosen and models:
            chosen = next((m for m in models if looks_visual(m)), "")
            self.cb_vision_model.setCurrentText(chosen)
        if not models:
            self.lbl_vision.setText("Сервер відповідає, але жодної моделі не завантажено.")
            self.lbl_vision.setProperty("role", "warn")
        elif chosen and not looks_visual(chosen):
            visual = [m for m in models if looks_visual(m)]
            self.lbl_vision.setText(
                f"«{chosen}» — текстова модель: кадрів вона не побачить, описів не буде. "
                + (f"Візуальні серед завантажених: {', '.join(visual)}." if visual
                   else "Завантаж у LM Studio візуальну модель (qwen3-vl-4b-instruct).")
            )
            self.lbl_vision.setProperty("role", "err")
        else:
            self.lbl_vision.setText(
                f"LM Studio на звʼязку · завантажено моделей: {len(models)}"
                + (f" · обрано {chosen}" if chosen else " · буде взята перша візуальна") + "."
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

        self._go(PAGE_OVERVIEW)
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

        self._go(PAGE_OVERVIEW)
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
        stale = box.addButton("Переписати застарілі", QMessageBox.AcceptRole)
        stale.setToolTip("Лише те, що описано іншою інструкцією чи моделлю")
        other = box.addButton("Іншою моделлю…", QMessageBox.AcceptRole)
        other.setToolTip("Переописати все сильнішою моделлю з LM Studio")
        box.addButton("Скасувати", QMessageBox.RejectRole)
        box.setDefaultButton(first)
        box.exec()

        clicked = box.clickedButton()
        if clicked not in (first, everything, stale, other):
            return
        limit = 20 if clicked is first else 0
        only_stale = clicked is stale
        redo = False
        model_override = ""
        if clicked is other:
            from PySide6.QtWidgets import QInputDialog
            model_override, ok = QInputDialog.getText(
                self, APP_NAME,
                "Назва моделі в LM Studio (має бути завантажена):",
                text=self.cfg.vision_model or "")
            if not ok or not model_override.strip():
                return
            redo = True

        self._go(PAGE_OVERVIEW)
        self.btn_describe.setText("Зупинити опис")
        self.progress.setRange(0, 0)
        self.progress.setFormat("Описую бібліотеку…")
        self._log("═══ Опис бібліотеки ═══")

        self.describe_worker = DescribeWorker(
            self.cfg, self.state, limit=limit, redo=redo, only_stale=only_stale,
            model_override=model_override.strip(), parent=self)
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

    def on_normalize_tags(self) -> None:
        answer = QMessageBox.question(
            self, APP_NAME,
            "Прогнати всі збережені теги моделі через поточний словник і "
            "виправити елементи Eagle?\n\nТеги, яких у словнику немає, буде "
            "прибрано; синоніми — зведено. Ручні теги, яких модель не ставила, "
            "не чіпаються. Моделі це не потребує.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return
        self._collect_ui_into_config()
        self.cfg.save()
        self._go(PAGE_OVERVIEW)
        self._log("═══ Вирівнювання тегів за словником ═══")
        self.normalize_worker = NormalizeWorker(self.cfg, self.state, parent=self)
        self.normalize_worker.line.connect(self._log)
        self.normalize_worker.done.connect(
            lambda stats: self._log(f"═══ {stats.summary()} ═══"))
        self.normalize_worker.start()

    def on_vocab_report(self) -> None:
        from ..maintenance import vocabulary_report

        text = vocabulary_report(self.state)
        box = QMessageBox(self)
        box.setWindowTitle("Словник тегів")
        box.setText("Як працює словник на твоїй бібліотеці")
        box.setDetailedText(text)
        box.setInformativeText(text.split("\n")[0])
        box.exec()

    def on_given_up(self) -> None:
        rows = self.state.given_up()
        if not rows:
            QMessageBox.information(self, APP_NAME, "Таких постів немає — усе взялось.")
            return
        lines = []
        for row in rows[:40]:
            who = f"@{row['username']}" if row["username"] else row["pk"]
            lines.append(f"• {who} — {row['last_error'] or '?'}\n   {row['url'] or ''}")
        more = f"\n… і ще {len(rows) - 40}" if len(rows) > 40 else ""
        answer = QMessageBox.question(
            self, APP_NAME,
            f"{len(rows)} пост(ів) відкладено після {self.cfg.max_post_attempts} спроб:\n\n"
            + "\n".join(lines) + more
            + "\n\nПовернути їх у чергу? Наступний прохід спробує ще раз.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            count = self.state.retry_given_up()
            self._log(f"Повернуто в чергу: {count} пост(ів).")

    def on_find_dupes(self) -> None:
        """Показує дублікати в Eagle. Видаляє тільки після окремої згоди."""
        if self.dupe_worker and self.dupe_worker.isRunning():
            self.dupe_worker.stop()
            return
        self._collect_ui_into_config()
        self._go(PAGE_OVERVIEW)
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

        self._go(PAGE_OVERVIEW)
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
        self.table.blockSignals(True)
        try:
            self._fill_table_rows(enabled)
        finally:
            self.table.blockSignals(False)

    def _fill_table_rows(self, enabled: set) -> None:
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

            describe = QTableWidgetItem()
            describe.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            describe.setCheckState(
                Qt.Unchecked if col.pk in set(self.cfg.describe_skip_collections) else Qt.Checked)
            describe.setData(Qt.UserRole, col.pk)
            describe.setTextAlignment(Qt.AlignCenter)
            describe.setToolTip("Описувати пости цієї підбірки моделлю")
            self.table.setItem(row, 4, describe)

    def _on_table_item_changed(self, item) -> None:
        """Галочка «Опис» пишеться в конфіг одразу — окремої кнопки для неї немає."""
        if item.column() != 4:
            return
        pk = str(item.data(Qt.UserRole) or "")
        if not pk:
            return
        skip = [p for p in self.cfg.describe_skip_collections if p != pk]
        if item.checkState() != Qt.Checked:
            skip.append(pk)
        if skip != list(self.cfg.describe_skip_collections):
            self.cfg.describe_skip_collections = skip
            self.cfg.save()

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

        force = self._confirm_cooldown()
        if force is None:
            return

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_refresh.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.setFormat("Працюю…")
        self._log("═══ Старт синхронізації ═══")

        self.sync_worker = SyncWorker(
            self.cfg, self.state, self.sessionid, selected, force, self)
        self.sync_worker.line.connect(self._log)
        self.sync_worker.progress.connect(self._on_progress)
        self.sync_worker.done.connect(self._on_sync_done)
        self.sync_worker.start()

    def _confirm_cooldown(self):
        """None — не запускати; True — запустити попри запобіжник; False — звичайно.

        Ручний запуск не забороняємо: людина має право. Але показуємо, чим це
        загрожує, бо саме серії проходів за кілька хвилин Instagram позначає як
        автоматизацію — і за це вже приходило попередження.
        """
        limit = float(self.cfg.min_hours_between_runs or 0)
        if limit <= 0:
            return False
        passed = self.state.hours_since_last_run()
        if passed is None or passed >= limit:
            return False

        minutes = int(round((limit - passed) * 60))
        answer = QMessageBox.question(
            self, APP_NAME,
            f"Попередній прохід був {int(round(passed * 60))} хв тому.\n"
            f"За наявним обмеженням наступний — через {minutes} хв.\n\n"
            "Часті звернення до Instagram виглядають як автоматизація й ведуть "
            "до попереджень і обмежень акаунта.\n\nСинхронізувати все одно?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            self._log(f"Синхронізацію відкладено: наступний прохід через {minutes} хв.")
            return None
        self._log("Запобіжник частоти обійдено вручну.")
        return True

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
        if stats.skipped_run:
            self.progress.setFormat("Пропущено")
            self.progress_label.setText("Прохід пропущено — див. журнал")
            return
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
        if stats.reason == "rate_limited":
            QMessageBox.warning(
                self, APP_NAME,
                "Instagram попросив зупинитись.\n\nПрохід перервано, і застосунок "
                f"не звертатиметься до Instagram {self.cfg.rate_limit_cooldown_hours:g} год. "
                "Це захист акаунта: продовжувати після такої відповіді означає "
                "підтвердити підозру в автоматизації.",
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
        if hasattr(self, "ind_session"):
            if self.sessionid:
                self._set_indicator(self.ind_session, True, "Сесія",
                                    f"@{self.username}" if self.username else "збережена")
            else:
                self._set_indicator(self.ind_session, False, "Сесія", "не підключена")
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

    def _set_review_badge(self, pending: int) -> None:
        """Кількість у бічній панелі й на кнопці огляду — щоразу, коли черга змінилась.

        Вкладка встигає повідомити про кількість ще під час власної побудови,
        коли бічної панелі ще немає — тому перевіряємо явно.
        """
        sidebar = getattr(self, "sidebar", None)
        if sidebar is None or sidebar.count() <= PAGE_REVIEW:
            return
        sidebar.item(PAGE_REVIEW).setText(f"Перегляд ({pending})" if pending else "Перегляд")
        button = getattr(self, "btn_goto_review", None)
        if button is not None:
            button.setText(f"Перегляд: {pending} чекає" if pending else "Перегляд: порожньо")
        self._update_last_run()

    def _update_last_run(self) -> None:
        label = getattr(self, "lbl_last_run", None)
        if label is None:
            return
        last = status.read()
        left = self.state.hours_since_last_run()
        if last and last.when_human:
            where = "за розкладом" if last.source == "scheduled" else "з вікна"
            text = f"Останній запуск {last.when_human} ({where}): {last.summary or last.headline}"
        elif left is not None:
            text = f"Останній прохід {left:.1f} год тому."
        else:
            text = "Ще жодного проходу не було."
        label.setText(text)

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
        line = f"[{datetime.now():%H:%M:%S}] {message}"
        self.log_view.appendPlainText(line)
        # І у файл: журнал вікна жив лише у вікні, і після закриття від нього не
        # лишалось нічого — розбирати «чому не описало» було нізвідки.
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            with open(LOG_DIR / f"gui_{datetime.now():%Y-%m}.log", "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass

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
