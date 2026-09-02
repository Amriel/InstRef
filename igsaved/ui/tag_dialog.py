"""Пропозиції до словника тегів.

Модель регулярно проситься сказати щось, чого у словнику немає. Мовчки
відкидати такі теги — значить ніколи не дізнатись, чого словнику бракує; тихо
приймати їх — значить повернутись до звалища синонімів, заради якого словник і
заводили. Тому третій шлях: рахувати, а потім показувати те, що повторилось,
і давати додати його свідомо — в конкретну категорію, одним кліком.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from ..state import State
from ..taxonomy import Taxonomy


class TagSuggestionsDialog(QDialog):
    def __init__(self, state: State, taxonomy: Taxonomy, path,
                 min_hits: int = 5, log: Callable[[str], None] = print, parent=None):
        super().__init__(parent)
        self.state = state
        self.taxonomy = taxonomy
        self.path = path
        self.min_hits = min_hits
        self.log = log
        self.changed = False

        self.setWindowTitle("Пропозиції до словника")
        self.resize(620, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        self.title = QLabel()
        self.title.setProperty("role", "h2")
        layout.addWidget(self.title)

        hint = QLabel(
            "Теги, які модель пропонувала, а словник не прийняв. Обери категорію "
            "й натисни «Додати» — тег стане частиною словника й далі братиметься "
            "нарівні з рештою. «Не треба» прибирає його зі списку назавжди."
        )
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        layout.addWidget(hint)

        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QScrollArea.NoFrame)
        self.holder = QWidget()
        self.rows = QVBoxLayout(self.holder)
        self.rows.setContentsMargins(0, 0, 8, 0)
        self.rows.setSpacing(6)
        self.rows.setAlignment(Qt.AlignTop)
        self.area.setWidget(self.holder)
        layout.addWidget(self.area, 1)

        bottom = QHBoxLayout()
        self.btn_open = QPushButton("Відкрити taxonomy.json")
        self.btn_open.setToolTip("Правити словник руками — категорії, ліміти, синоніми")
        self.btn_open.clicked.connect(self._open_file)
        close = QPushButton("Закрити")
        close.setProperty("role", "primary")
        close.clicked.connect(self.accept)
        bottom.addWidget(self.btn_open)
        bottom.addStretch(1)
        bottom.addWidget(close)
        layout.addLayout(bottom)

        self.reload()

    # ------------------------------------------------------------------ дані
    def reload(self) -> None:
        while self.rows.count():
            item = self.rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        candidates = self.state.tag_candidates(self.min_hits)
        self.title.setText(
            f"Пропозицій: {len(candidates)}" if candidates
            else "Словнику поки нічого не бракує"
        )
        if not candidates:
            empty = QLabel(
                "Модель не пропонувала нічого поза словником частіше, ніж "
                f"{self.min_hits} раз(ів). Це добра новина: словник покриває "
                "твій контент."
            )
            empty.setWordWrap(True)
            empty.setProperty("role", "muted")
            self.rows.addWidget(empty)
            return

        for row in candidates:
            self.rows.addWidget(self._row(str(row["tag"]), int(row["hits"] or 0)))

    def _row(self, tag: str, hits: int) -> QWidget:
        wrapper = QWidget()
        line = QHBoxLayout(wrapper)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)

        label = QLabel(tag)
        label.setProperty("role", "title")
        label.setMinimumWidth(190)
        count = QLabel(f"×{hits}")
        count.setProperty("role", "muted")
        count.setFixedWidth(48)

        picker = QComboBox()
        for key, title in self.taxonomy.category_titles():
            picker.addItem(title.title(), key)
        picker.setMinimumWidth(190)

        add = QPushButton("Додати")
        add.setFixedWidth(90)
        add.clicked.connect(lambda: self._add(tag, picker.currentData()))
        never = QPushButton("Не треба")
        never.setFixedWidth(96)
        never.clicked.connect(lambda: self._ignore(tag))

        line.addWidget(label, 1)
        line.addWidget(count)
        line.addWidget(picker)
        line.addWidget(add)
        line.addWidget(never)
        return wrapper

    # --------------------------------------------------------------- дії
    def _add(self, tag: str, category_key: str) -> None:
        if self.taxonomy.add(tag, category_key):
            self.taxonomy.save(self.path)
            self.changed = True
            self.log(f"Тег «{tag}» додано у словник ({category_key})")
        self.state.resolve_tag_candidate(tag, "added")
        self.reload()

    def _ignore(self, tag: str) -> None:
        self.state.resolve_tag_candidate(tag, "ignored")
        self.log(f"Тег «{tag}» більше не пропонується")
        self.reload()

    def _open_file(self) -> None:
        import os
        import subprocess
        import sys

        path = str(self.path)
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
