"""Спільні дрібні віджети й помічники сторінок налаштувань."""

from __future__ import annotations

from typing import List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QTabWidget,
    QVBoxLayout, QWidget,
)


EAGLE_DEFAULT_URL = "http://localhost:41595"
LABEL_WIDTH = 168  # однакова колонка підписів на всіх сторінках налаштувань

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




def _section(title: str, hint: str = "") -> tuple[QWidget, QFormLayout]:
    """Блок усередині сторінки: підзаголовок, за бажанням — пояснення, форма."""
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    layout.addWidget(_label(title, "h2"))
    if hint:
        layout.addWidget(_hint(hint))
    holder = QWidget()
    form = _form(holder)
    form.setContentsMargins(0, 2, 0, 0)
    layout.addWidget(holder)
    return box, form


def _stack_page(title: str, *sections: QWidget) -> QWidget:
    """Сторінка з кількох блоків, що гортається лише за потреби."""
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(16, 14, 16, 16)
    layout.setSpacing(18)
    if title:
        layout.addWidget(_label(title, "h1"))
    for section in sections:
        layout.addWidget(section)
    layout.addStretch(1)
    return page


def _subtabs(*pairs) -> QTabWidget:
    """Внутрішні вкладки розділу: (назва, віджет), кожна гортається окремо."""
    tabs = QTabWidget()
    tabs.setDocumentMode(True)
    for name, widget in pairs:
        tabs.addTab(_scrollable(widget), name)
    return tabs

