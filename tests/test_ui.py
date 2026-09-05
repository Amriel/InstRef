"""GUI-тести без екрана (QT_QPA_PLATFORM=offscreen).

Раніше перевірки вікна жили у /tmp-скриптах поза репозиторієм, і регресії на
кшталт краху ініціалізації вкладки суїт не ловив. Тепер вони тут.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from igsaved.state import REVIEW_MODEL, REVIEW_RULES, State  # noqa: E402


@pytest.fixture(scope="session")
def app():
    application = QApplication.instance() or QApplication([])
    yield application
    # Qt-обʼєкти, що доживають до завершення інтерпретатора, валять процес
    # уже ПІСЛЯ «209 passed»: segfault на Linux, exit code 1 на Windows —
    # і CI червоний при зелених тестах. Тому прибираємо все явно.
    for widget in application.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    application.processEvents()
    application.sendPostedEvents()
    application.processEvents()


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    """Вікно з тимчасовими config.json і state.db — без слідів у робочих файлах."""
    from igsaved import config as cfgmod
    from igsaved.ui import main_window as mw

    monkeypatch.setattr(cfgmod, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(cfgmod, "STATE_PATH", tmp_path / "state.db")
    monkeypatch.setattr(mw, "STATE_PATH", tmp_path / "state.db")
    monkeypatch.setattr(mw.MainWindow, "_check_health", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_check_updates", lambda self: None)
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    win = mw.MainWindow()
    win.cfg.download_dir = str(tmp_path / "dl")
    win.cfg.eagle_enabled = False
    win.cfg.save = lambda *a, **k: None
    win.review_tab.cfg = win.cfg
    yield win
    win.tray.hide()
    win.close()
    win.deleteLater()
    app.processEvents()


def _seed_review(state: State, root: Path):
    root.mkdir(parents=True, exist_ok=True)
    (root / "_review").mkdir(exist_ok=True)
    keep = root / "art.jpg"
    keep.write_bytes(b"x")
    drop = root / "_review" / "meme.jpg"
    drop.write_bytes(b"y")
    rows = (("1", str(keep), "alice", "download", REVIEW_MODEL),
            ("2", str(drop), "bob", "skip", REVIEW_MODEL),
            ("3", "", "carol", "review", REVIEW_RULES))
    for pk, path, user, verdict, src in rows:
        state.record_media(pk, "c", user, None, 1, "feed", "підпис", f"https://ig/{pk}")
        if path:
            state.add_file(path, pk, "photo", 0, 1)
        state.add_review(pk, path, "", user, "підпис", f"https://ig/{pk}", "причина",
                         verdict, source=src)
    state.set_ai_meta("1", "art", 0.9, "A chrome sculpture.", ["cgi", "autotagged"], "m", 1)
    return keep, drop


def test_window_builds_with_all_pages(window):
    """Бічна панель: кожен розділ — одна тема, всередині — підвкладки."""
    from igsaved import APP_NAME, __version__
    from igsaved.ui import main_window as mw

    assert window.windowTitle() == f"{APP_NAME} {__version__}"
    assert [window.sidebar.item(i).text() for i in range(window.sidebar.count())] == \
        mw.PAGE_TITLES
    assert window.stack.count() == len(mw.PAGE_TITLES)
    sync = window.stack.widget(mw.PAGE_SYNC)
    assert [sync.subtabs.tabText(i) for i in range(sync.subtabs.count())] == \
        ["Підбірки", "Що качати", "Обхід", "Лайки та фільтр"]
    window._go(mw.PAGE_ACCOUNT, 1)
    assert window.stack.currentIndex() == mw.PAGE_ACCOUNT
    assert window.stack.widget(mw.PAGE_ACCOUNT).subtabs.currentIndex() == 1
    window._open_session_tab()
    assert window.stack.widget(mw.PAGE_ACCOUNT).subtabs.currentIndex() == 0
    for widget in (window.sp_jitter, window.sp_attempts, window.sp_rate_cooldown,
                   window.sp_sec_per_frame, window.ck_by_scene, window.sp_backlog,
                   window.cb_dupe, window.btn_normalize, window.btn_vocab,
                   window.btn_given_up, window.btn_urls, window.ind_session):
        assert widget is not None


def test_settings_round_trip(window):
    window.sp_jitter.setValue(7)
    window.sp_sec_per_frame.setValue(3)
    window.ck_by_scene.setChecked(False)
    window.cb_dupe.setCurrentIndex(window.cb_dupe.findData("skip"))
    window._collect_ui_into_config()
    cfg = window.cfg
    assert (cfg.schedule_jitter_minutes, cfg.vision_seconds_per_frame,
            cfg.vision_frames_by_scene, cfg.dupe_action) == (7, 3.0, False, "skip")
    window._load_config_into_ui()
    assert window.sp_jitter.value() == 7


def test_review_cards_show_editable_description_and_tags(window, tmp_path):
    keep, drop = _seed_review(window.state, Path(window.cfg.download_dir))
    tab = window.review_tab
    tab.reload()
    cards = {c.media_pk: c for c in tab._cards}
    assert set(cards) == {"1", "2", "3"}
    assert tab._cards[0].media_pk == "2"                     # відсіяне — першим
    card = cards["1"]
    assert card.ed_description.toPlainText() == "A chrome sculpture."
    assert card.ed_tags.text() == "cgi"

    # правка перед «Залишити» потрапляє в базу — звідти її візьме Eagle
    card.ed_description.setPlainText("Slow dolly along a chrome sculpture.")
    card.ed_tags.setText("cgi, Studio-Lighting, ")
    card.btn_star.setChecked(True)
    card._decide("kept", "")
    meta = window.state.ai_meta("1")
    assert meta["description"] == "Slow dolly along a chrome sculpture."
    assert meta["tags"] == ["cgi", "studio-lighting", "autotagged"]
    assert window.state.exemplars() == ["Slow dolly along a chrome sculpture."]
    assert not window.state.is_pending_review("1")


def test_keyboard_triage_moves_and_decides(window):
    keep, drop = _seed_review(window.state, Path(window.cfg.download_dir))
    tab = window.review_tab
    tab.reload()
    assert tab.current_card().media_pk == "2"
    tab.move_cursor(1)
    assert tab.current_card().media_pk == "1"

    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent

    tab.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Y, Qt.NoModifier))
    assert not window.state.is_pending_review("1")
    assert keep.exists()
    tab.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Left, Qt.NoModifier))
    tab.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_N, Qt.NoModifier))
    assert not drop.exists()
    assert window.state.review_count() == 1


def test_agreement_stats_are_recorded_and_shown(window):
    _seed_review(window.state, Path(window.cfg.download_dir))
    tab = window.review_tab
    tab.reload()
    tab._decide("2", "kept", "")        # не погодився: модель казала «skip»
    tab._decide("1", "kept", "")        # погодився
    stats = window.state.agreement_stats()
    assert stats == {"total": 2, "agreed": 1, "by_category": {"?": (0, 1), "art": (1, 1)}}
    assert "Згода з моделлю: 50%" in tab.stats_label.text()


def test_given_up_dialog_returns_posts_to_the_queue(window):
    window.state.mark_failure("9", "media not found", username="ghost", max_attempts=1)
    assert window.state.is_known("9")
    window.on_given_up()
    assert not window.state.is_known("9")


def test_skipped_run_does_not_look_like_a_failure(window):
    from igsaved.sync import Stats

    stats = Stats()
    stats.reason = "locked"
    stats.errors.append("locked")
    window._on_sync_done(stats)
    assert window.progress.format() == "Пропущено"


def test_describe_toggle_per_collection_writes_config(window):
    from igsaved.instagram import CollectionInfo

    window.collections = [CollectionInfo("1", "Houdini", 5), CollectionInfo("2", "Ads", 3)]
    window._fill_table()
    window.table.item(1, 4).setCheckState(Qt.Unchecked)
    assert window.cfg.describe_skip_collections == ["2"]
    window.table.item(1, 4).setCheckState(Qt.Checked)
    assert window.cfg.describe_skip_collections == []


def test_quick_start_indicators_reflect_health(window):
    window.sessionid = ""
    window._refresh_status()
    assert "не підключена" in window.ind_session.text.text()
    window._on_health({"eagle": (False, "nope"), "model": (True, "qwen")})
    assert window.ind_model.text.text() == "qwen"
    assert window.ind_model.button.isHidden() or not window.ind_model.button.isVisible()


def test_update_notice_appears_for_a_newer_release(window):
    window._on_update_checked({"version": "99.0.0", "url": "https://x/r"})
    assert window.update_notice.isVisibleTo(window)
    assert "99.0.0" in window.update_text.text()
    assert window.state.get_meta("last_update_check")


def test_switching_pages_autosaves_settings(window):
    """Забуте «Зберегти» коштувало налаштувань — тепер перехід зберігає сам."""
    from igsaved.ui import main_window as mw

    saved = []
    window.cfg.save = lambda *a, **k: saved.append(True)
    window.sp_jitter.setValue(11)
    window._go(mw.PAGE_ABOUT)
    assert window.cfg.schedule_jitter_minutes == 11 and saved


def test_review_badge_shows_in_sidebar_and_overview(window):
    from igsaved.ui import main_window as mw

    _seed_review(window.state, Path(window.cfg.download_dir))
    window.review_tab.reload()
    assert window.sidebar.item(mw.PAGE_REVIEW).text() == "Перегляд (3)"
    assert "3" in window.btn_goto_review.text()


def test_about_page_offers_one_click_update(window):
    window._on_update_checked({"version": "9.0.0", "url": "u", "notes": "notes", "assets": []})
    assert window.btn_update.isEnabled()
    assert window.btn_update.text() == "Оновити до 9.0.0"
    assert window.update_notes.toPlainText() == "notes"
    window._manual_check = True
    window._on_update_checked(None)
    assert "найновіша" in window.lbl_update.text()


def test_model_list_is_filled_from_health_check(window):
    """Список моделей LM Studio приходить сам; текстові позначені й стоять нижче."""
    window._on_health({"eagle": (True, "Eagle"),
                       "model": (True, "qwen3-vl-4b-instruct"),
                       "models": ["qwen3.8-27b", "qwen3-vl-4b-instruct"]})
    items = [window.cb_vision_model.itemText(i) for i in range(window.cb_vision_model.count())]
    assert items[0] == "qwen3-vl-4b-instruct"
    assert items[1].startswith("qwen3.8-27b") and "текстова" in items[1]
    window.cb_vision_model.setCurrentIndex(1)
    window._collect_ui_into_config()
    assert window.cfg.vision_model == "qwen3.8-27b"       # без помітки
