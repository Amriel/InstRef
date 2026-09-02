"""Вкладка перегляду: все, що чекає на твоє око.

Спершу це були дві вкладки — «Ревʼю» для сумнівного й «Рішення моделі» для
того, що модель розсудила сама. Але дії над ними виявились однакові: лишити
файл (і тоді він іде в Eagle) або видалити. Дві вкладки з тими самими двома
кнопками — це не два інструменти, а один, розрізаний навпіл.

Тепер черга одна, а походження поста показує позначка на картці:

* без позначки — правила не змогли вирішити, рішення цілком за тобою;
* «модель: качати» — вона схвалила, файл уже в основній теці й чекає згоди;
* «модель: пропустити» — вона відсіяла, файл у теці ревʼю й буде видалений.

Відсіяне моделлю теж показується: ролик уже завантажений заради кадрів, тож
показати його нічого не коштує, а помилкове «мем» інакше було б не спіймати.
Тому воно й стоїть першим у списку — рятувати треба саме його.

Показує великі превʼю сіткою: рішення «мем чи арт» приймається очима, тому
картинка тут головна, а не текст. Подвійний клік по превʼю відкриває файл.

У Eagle нічого не потрапляє, доки пост не схвалено.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Callable, List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from ..config import Config
from ..state import REVIEW_MODEL, REVIEW_RULES, State

CARD_W = 300
THUMB_W, THUMB_H = 268, 336   # вертикальний кадр Reels як орієнтир

KEPT = "kept"
DROPPED = "dropped"

# Що модель запропонувала зробити з постом (значення колонки review.verdict).
PROPOSE_KEEP = "download"
PROPOSE_DROP = "skip"

MAX_CARDS = 60          # більше карток одночасно — марна витрата памʼяті й часу
OPEN_COOLDOWN = 1.5     # секунди між відкриттями файлу
_last_open = [0.0]      # спільний для всіх карток запобіжник


def _may_open() -> bool:
    """Не даємо відкрити десятки плеєрів поспіль, навіть якщо щось піде не так."""
    now = time.monotonic()
    if now - _last_open[0] < OPEN_COOLDOWN:
        return False
    _last_open[0] = now
    return True


def _open_path(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class Preview(QLabel):
    """Превʼю на весь верх картки. Клік відкриває файл у системному плеєрі."""

    def __init__(self, image_path: str, media_path: str):
        super().__init__()
        self.media_path = media_path
        self.setFixedSize(THUMB_W, THUMB_H)
        self.setAlignment(Qt.AlignCenter)
        self.setProperty("role", "thumb")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Подвійний клік — відкрити файл")

        pixmap = QPixmap(str(image_path)) if image_path and Path(image_path).exists() \
            else QPixmap()
        if pixmap.isNull():
            self.setText("без превʼю")
        else:
            scaled = pixmap.scaled(
                THUMB_W, THUMB_H, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
            )
            # обрізаємо по центру, щоб картка не «стрибала» на різних пропорціях
            x = max(0, (scaled.width() - THUMB_W) // 2)
            y = max(0, (scaled.height() - THUMB_H) // 2)
            self.setPixmap(scaled.copy(x, y, THUMB_W, THUMB_H))

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 — Qt API
        # Свідомо подвійний клік і тільки ліва кнопка: відкриття плеєра —
        # важка дія, випадковим дотиком її запускати не можна.
        if event.button() == Qt.LeftButton and _may_open():
            path = Path(self.media_path or "")
            if path.exists():
                _open_path(path)
        super().mouseDoubleClickEvent(event)


class ReviewCard(QFrame):
    def __init__(self, row, on_decide: Callable[[str, str, str], None]):
        super().__init__()
        self.setProperty("role", "card")
        self.setFixedWidth(CARD_W)
        self.media_pk = str(row["media_pk"])
        self.username = row["username"] or ""
        self.url = row["url"] or ""
        # Пропозиція є лише там, де рішення прийняла модель. Для решти позначки
        # немає — і це теж інформація: значить, вирішувати цілком тобі.
        self.proposal = (row["verdict"] or "") if _source_of(row) == REVIEW_MODEL else ""
        self._on_decide = on_decide

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 12)
        layout.setSpacing(8)

        layout.addWidget(Preview(row["thumb"] or row["path"], row["path"]),
                         alignment=Qt.AlignHCenter)

        head = QHBoxLayout()
        author = QLabel(f"@{self.username}" if self.username else "невідомий автор")
        author.setProperty("role", "title")
        link = QPushButton("IG")
        link.setFixedWidth(38)
        link.setToolTip("Відкрити пост в Instagram")
        link.clicked.connect(self._open_url)
        head.addWidget(author, 1)
        head.addWidget(link)
        layout.addLayout(head)

        caption = (row["caption"] or "").strip().replace("\n", " ")
        if len(caption) > 110:
            caption = caption[:109] + "…"
        body = QLabel(caption or "без підпису")
        body.setWordWrap(True)
        body.setProperty("role", "muted")
        body.setFixedHeight(34)
        body.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        layout.addWidget(body)

        reason = QLabel(row["reason"] or "немає виразних ознак")
        reason.setWordWrap(True)
        reason.setProperty("role", "hint")
        # Тут тепер буває й опис від моделі — двох рядків стало замало.
        reason.setFixedHeight(46)
        reason.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        reason.setToolTip(row["reason"] or "")
        layout.addWidget(reason)

        if self.proposal:
            badge = QLabel(
                "◆ модель: пропустити" if self.proposal == PROPOSE_DROP
                else "◆ модель: качати"
            )
            badge.setProperty(
                "role", "warn" if self.proposal == PROPOSE_DROP else "ok")
            badge.setToolTip(
                "Файл лежить у теці ревʼю й буде видалений, якщо погодишся."
                if self.proposal == PROPOSE_DROP
                else "Файл уже в основній теці, у Eagle чекає твого підтвердження."
            )
            layout.addWidget(badge)

        top = QHBoxLayout()
        top.setSpacing(6)
        # Дія одна й та сама, міняється лише напис: коли модель зібралась
        # викинути пост, головна кнопка — це рятування.
        keep = QPushButton("Врятувати" if self.proposal == PROPOSE_DROP else "Залишити")
        keep.setProperty("role", "primary")
        keep.setToolTip("Лишити файл і додати його в Eagle")
        keep.clicked.connect(lambda: self._decide(KEPT, ""))
        drop = QPushButton(
            "Погоджуюсь" if self.proposal == PROPOSE_DROP else "Не качати")
        drop.setToolTip("Видалити файл. Автора це ніяк не позначає —\n"
                        "буває, що пост просто не потрібен.")
        drop.clicked.connect(lambda: self._decide(DROPPED, ""))
        top.addWidget(keep, 1)
        top.addWidget(drop, 1)
        layout.addLayout(top)

        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        always = QPushButton("Завжди")
        always.setToolTip("Залишити цей пост і завжди качати цього автора")
        always.clicked.connect(lambda: self._decide(KEPT, "allow"))
        never = QPushButton("Ніколи")
        never.setToolTip("Видалити і більше ніколи не качати цього автора")
        never.clicked.connect(lambda: self._decide(DROPPED, "block"))
        bottom.addWidget(always, 1)
        bottom.addWidget(never, 1)
        layout.addLayout(bottom)

    def _decide(self, decision: str, list_action: str) -> None:
        self._on_decide(self.media_pk, decision, list_action)

    def _open_url(self) -> None:
        if self.url:
            webbrowser.open(self.url)


class ReviewTab(QWidget):
    def __init__(self, cfg: Config, state: State, log: Callable[[str], None],
                 on_change: Optional[Callable[[int], None]] = None, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.state = state
        self.log = log
        self.on_change = on_change
        self._cards: List[ReviewCard] = []
        self._columns = 0
        self._pending_total = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        head = QHBoxLayout()
        self.title = QLabel()
        self.title.setProperty("role", "h2")
        head.addWidget(self.title, 1)
        self.btn_agree = QPushButton("Погодитись з моделлю")
        self.btn_agree.setProperty("role", "primary")
        self.btn_agree.setToolTip(
            "Виконати те, що модель запропонувала: схвалене йде в Eagle,\n"
            "відсіяне видаляється з диска. Пости без позначки не чіпає."
        )
        self.btn_agree.clicked.connect(self._agree_with_model)
        head.addWidget(self.btn_agree)
        self.btn_keep_all = QPushButton("Залишити все")
        self.btn_keep_all.setToolTip("Схвалити всі пости, що зараз у списку")
        self.btn_keep_all.clicked.connect(self._keep_all)
        self.btn_clear = QPushButton("Очистити чергу")
        self.btn_clear.setToolTip("Прибрати всі пости з черги, нічого не схвалюючи")
        self.btn_clear.clicked.connect(self._clear_queue)
        self.btn_open_folder = QPushButton("Папка ревʼю")
        self.btn_open_folder.clicked.connect(self._open_folder)
        self.btn_reload = QPushButton("Оновити")
        self.btn_reload.clicked.connect(self.reload)
        for button in (self.btn_keep_all, self.btn_clear, self.btn_open_folder,
                       self.btn_reload):
            head.addWidget(button)
        layout.addLayout(head)

        self.hint = QLabel(
            "Усе, що чекає на твоє око. Позначка «◆ модель» означає, що рішення "
            "вже прийнято за тебе і його лишається підтвердити або скасувати; "
            "без позначки — правила не змогли вирішити. У Eagle нічого не піде, "
            "доки не натиснеш «Залишити». Подвійний клік по картинці відкриває файл."
        )
        self.hint.setWordWrap(True)
        self.hint.setProperty("role", "muted")
        layout.addWidget(self.hint)

        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QScrollArea.NoFrame)
        # Смуга завжди на місці: інакше її поява/зникнення міняє ширину
        # вьюпорта, кількість колонок стрибає — і перекладання зациклюється.
        self.area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.holder = QWidget()
        self.grid = QGridLayout(self.holder)
        self.grid.setContentsMargins(0, 0, 8, 0)
        self.grid.setSpacing(12)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.area.setWidget(self.holder)
        layout.addWidget(self.area, 1)

        # Перекладання відкладене: подія зміни розміру приходить пачками,
        # і реагувати на кожну — це і є те саме зациклення.
        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.timeout.connect(self._relayout)

        self.reload()

    # ------------------------------------------------------------------ дані
    def reload(self) -> None:
        for card in self._cards:
            card.setParent(None)
            card.deleteLater()
        self._cards = []

        rows = self.state.pending_review()
        self._pending_total = len(rows)
        columns = self._fit_columns()
        for index, row in enumerate(rows[:MAX_CARDS]):
            card = ReviewCard(row, self._decide)
            self.grid.addWidget(card, index // columns, index % columns)
            self._cards.append(card)
        self._columns = columns
        self._update_title()
        self._enable_bulk(bool(rows))

    def _enable_bulk(self, enabled: bool) -> None:
        self.btn_keep_all.setEnabled(enabled)
        # «Погодитись з моделлю» має сенс тільки коли в черзі є її рішення.
        self.btn_agree.setEnabled(
            any(card.proposal for card in self._cards) if enabled else False)

    def _update_title(self) -> None:
        # Єдина точка, де змінюється кількість — звідси й повідомляємо вкладку,
        # інакше значок у її назві оновлювався б лише після синхронізації.
        if self.on_change is not None:
            self.on_change(self.state.review_count())
        total = self._pending_total
        shown = len(self._cards)
        if not total:
            self.title.setText("На ревʼю нічого немає")
            return
        from_model = sum(1 for card in self._cards if card.proposal)
        text = f"На ревʼю: {total}"
        if shown < total:
            text += f" · показано {shown}"
        if from_model:
            text += f" · з них рішень моделі {from_model}"
        self.title.setText(text)

    def _drop_card(self, media_pk: str) -> None:
        """Прибирає одну картку без перебудови сітки — інакше скрол злітає вниз
        і всі превʼю перечитуються з диска на кожен клік."""
        card = next((c for c in self._cards if c.media_pk == str(media_pk)), None)
        if card is None:
            self.reload()
            return

        bar = self.area.verticalScrollBar()
        position = bar.value()

        self._cards.remove(card)
        card.setParent(None)
        card.deleteLater()
        self._pending_total = max(0, self._pending_total - 1)

        # добираємо наступну картку з черги, щоб сітка не рідшала
        shown = {c.media_pk for c in self._cards}
        for row in self.state.pending_review():
            if len(self._cards) >= MAX_CARDS:
                break
            if str(row["media_pk"]) not in shown:
                self._cards.append(ReviewCard(row, self._decide))
                break

        columns = self._columns or 1
        for index, existing in enumerate(self._cards):
            self.grid.addWidget(existing, index // columns, index % columns)

        self._update_title()
        self._enable_bulk(bool(self._cards))
        bar.setValue(min(position, bar.maximum()))

    def _fit_columns(self) -> int:
        width = self.area.viewport().width() or self.width() or 900
        return max(1, min(5, (width - 16) // (CARD_W + 12)))

    def resizeEvent(self, event) -> None:  # noqa: N802 — Qt API
        super().resizeEvent(event)
        self._relayout_timer.start(150)

    def _relayout(self) -> None:
        """Переставляє наявні картки, НЕ створюючи нових. Без цього кожна
        зміна розміру перебудовувала сітку, що знову міняла розмір."""
        columns = self._fit_columns()
        if columns == self._columns or not self._cards:
            return
        self._columns = columns
        for index, card in enumerate(self._cards):
            self.grid.addWidget(card, index // columns, index % columns)

    def pending(self) -> int:
        return self.state.review_count()

    # --------------------------------------------------------------- рішення
    def _decide(self, media_pk: str, decision: str, list_action: str) -> None:
        row = next((r for r in self.state.pending_review()
                    if str(r["media_pk"]) == str(media_pk)), None)
        if row is None:
            self.reload()
            return

        username = (row["username"] or "").strip().lower()
        if list_action == "allow" and username:
            self._remember(self.cfg.allow_accounts, username, "білий")
        elif list_action == "block" and username:
            self._remember(self.cfg.block_accounts, username, "чорний")

        if decision == KEPT:
            self._keep(row)
        else:
            self._drop(row)

        self.state.decide_review(media_pk, decision)
        self._drop_card(media_pk)

    def _keep_all(self) -> None:
        for row in list(self.state.pending_review()):
            self._keep(row)
            self.state.decide_review(str(row["media_pk"]), KEPT)
        self.reload()

    def _agree_with_model(self) -> None:
        """Виконує рішення моделі: схвалене — в Eagle, відсіяне — з диска."""
        # Тільки пости з рішенням моделі: решту вона не бачила, і вирішувати
        # за неї було б підміною.
        rows = [row for row in self.state.pending_review()
                if _source_of(row) == REVIEW_MODEL]
        drops = sum(1 for row in rows if (row["verdict"] or "") == PROPOSE_DROP)
        if not rows:
            self.log("У черзі немає рішень моделі — нічого підтверджувати.")
            return
        if drops:
            answer = QMessageBox.question(
                self, "Погодитись з моделлю",
                f"Схвалених постів: {len(rows) - drops} — підуть у Eagle.\n"
                f"Відсіяних: {drops} — їхні файли будуть видалені.\n\nПродовжити?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            )
            if answer != QMessageBox.Yes:
                return

        for row in rows:
            if (row["verdict"] or "") == PROPOSE_DROP:
                self._drop(row)
                self.state.decide_review(str(row["media_pk"]), DROPPED)
            else:
                self._keep(row)
                self.state.decide_review(str(row["media_pk"]), KEPT)
        self.log(f"Рішення моделі прийнято для {len(rows)} пост(ів)")
        self.reload()

    def _clear_queue(self) -> None:
        """Скидає чергу, коли вона накопичилась і розбирати її вже немає сенсу."""
        pending = self.state.review_count()
        if not pending:
            return

        box = QMessageBox(self)
        box.setWindowTitle("Очистити чергу")
        box.setIcon(QMessageBox.Question)
        box.setText(f"У черзі {pending} пост(ів). Що зробити з файлами?")
        box.setInformativeText(
            "Схвалене моделлю лежить в основній теці, решта — у "
            f"«{self.cfg.review_dir.name}».\n"
            "У Eagle нічого не додається й не видаляється в обох випадках."
        )
        keep_files = box.addButton("Лишити файли на диску", QMessageBox.AcceptRole)
        delete_files = box.addButton("Видалити файли", QMessageBox.DestructiveRole)
        box.addButton("Скасувати", QMessageBox.RejectRole)
        box.setDefaultButton(keep_files)
        box.exec()

        clicked = box.clickedButton()
        if clicked not in (keep_files, delete_files):
            return

        if clicked is delete_files:
            removed = 0
            for row in self.state.pending_review():
                for path_text in self.state.media_files(str(row["media_pk"])):
                    path = Path(path_text)
                    try:
                        if path.exists():
                            path.unlink()
                            removed += 1
                    except OSError as exc:
                        self.log(f"Не вдалось видалити {path.name}: {exc}")
                # щоб наступна синхронізація не скачала їх знову
                self.state.mark_skipped(str(row["media_pk"]), "чергу очищено")
            self.log(f"Чергу очищено, видалено {removed} файл(ів)")
        else:
            self.log(f"Чергу очищено, {pending} файл(ів) лишились на диску")

        self.state.clear_review()
        self.reload()

    def _remember(self, bucket: List[str], username: str, label: str) -> None:
        if username not in {u.lower() for u in bucket}:
            bucket.append(username)
            self.cfg.save()
            self.log(f"@{username} додано в {label} список")

    def _keep(self, row) -> None:
        """Переносить файли з теки ревʼю до основної й лише тепер веде в Eagle."""
        target_dir = self.cfg.target_dir("")
        target_dir.mkdir(parents=True, exist_ok=True)
        moved = 0
        for path_text in self.state.media_files(str(row["media_pk"])):
            source = Path(path_text)
            if not source.exists():
                continue
            destination = target_dir / source.name
            if destination.exists() and destination != source:
                destination = _free_name(destination)
            if source == destination:
                continue
            try:
                shutil.move(str(source), str(destination))
                self.state.update_file_path(str(source), str(destination))
                moved += 1
            except OSError as exc:
                self.log(f"Не вдалось перенести {source.name}: {exc}")
        self.log(f"Залишено @{row['username']} ({moved} файл(ів))")

        if self.cfg.eagle_enabled:
            from ..maintenance import push_media

            # Позначку «на ревʼю» знімаємо заздалегідь, інакше імпорт її пропустить.
            self.state.decide_review(str(row["media_pk"]), KEPT)
            push_media(self.cfg, self.state, str(row["media_pk"]), self.log)

    def _drop(self, row) -> None:
        removed = 0
        for path_text in self.state.media_files(str(row["media_pk"])):
            path = Path(path_text)
            try:
                if path.exists():
                    path.unlink()
                    removed += 1
            except OSError as exc:
                self.log(f"Не вдалось видалити {path.name}: {exc}")
        thumb = row["thumb"]
        if thumb and Path(thumb).exists() and Path(thumb) != Path(row["path"] or ""):
            try:
                Path(thumb).unlink()
            except OSError:
                pass
        self.state.mark_skipped(str(row["media_pk"]), "відхилено вручну в ревʼю")
        self.log(f"Видалено @{row['username']} ({removed} файл(ів))")

    def _open_folder(self) -> None:
        path = self.cfg.review_dir
        path.mkdir(parents=True, exist_ok=True)
        _open_path(path)


def _source_of(row) -> str:
    """Походження запису. Старі рядки писались без цієї колонки — вони від правил."""
    try:
        return str(row["source"] or REVIEW_RULES)
    except (KeyError, IndexError):
        return REVIEW_RULES


def _free_name(path: Path) -> Path:
    for index in range(2, 500):
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    return path
