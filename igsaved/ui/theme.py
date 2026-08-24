"""Темна тема інтерфейсу."""

ACCENT = "#e1306c"
ACCENT_DIM = "#b02455"
BG = "#16171c"
PANEL = "#1e2027"
PANEL_2 = "#262932"
BORDER = "#333744"
TEXT = "#e8e9ee"
MUTED = "#9aa0b0"
OK = "#4ec98a"
WARN = "#e8b23a"
ERR = "#ef6461"

STYLESHEET = f"""
QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 13px;
}}
QLabel, QCheckBox, QRadioButton {{ background: transparent; }}
QWidget[role="row"] {{ background: transparent; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}
QLabel[role="h1"] {{ font-size: 19px; font-weight: 600; }}
QLabel[role="hint"] {{ color: {MUTED}; font-size: 11px; }}
QFrame[role="banner"] {{
    background: #2b1f22;
    border: 1px solid {ERR};
    border-radius: 9px;
}}
QFrame[role="banner"] QLabel {{ color: #f3d3d3; }}
QFrame[role="banner"] QLabel[role="title"] {{ color: {ERR}; font-weight: 600; }}
QFrame[role="notice"] {{
    background: #2a2418;
    border: 1px solid {WARN};
    border-radius: 9px;
}}
QFrame[role="notice"] QLabel {{ color: #efe3c8; }}
QFrame[role="notice"] QLabel[role="title"] {{ color: {WARN}; font-weight: 600; }}
QLabel[role="h2"] {{ font-size: 14px; font-weight: 600; padding-top: 6px; }}
QLabel[role="muted"] {{ color: {MUTED}; }}
QLabel[role="ok"]    {{ color: {OK}; }}
QLabel[role="warn"]  {{ color: {WARN}; }}
QLabel[role="err"]   {{ color: {ERR}; }}

QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 10px;
    background: {PANEL};
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {MUTED};
    padding: 9px 18px;
    margin-right: 4px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
}}
QTabBar::tab:selected {{
    background: {PANEL};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-bottom: 1px solid {PANEL};
}}
QTabBar::tab:hover:!selected {{ color: {TEXT}; }}

QFrame[role="card"] {{
    background: {PANEL_2};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QFrame[role="card"]:hover {{ border-color: {ACCENT_DIM}; }}
QFrame[role="card"] QLabel[role="title"] {{ font-size: 14px; font-weight: 600; color: {TEXT}; }}
QLabel[role="thumb"] {{
    background: {BG};
    border: 1px solid {BORDER};
    border-radius: 8px;
    color: {MUTED};
    font-size: 11px;
}}

QListWidget#nav {{
    background: {BG};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 6px;
    outline: none;
}}
QListWidget#nav::item {{
    padding: 10px 12px;
    border-radius: 7px;
    color: {MUTED};
}}
QListWidget#nav::item:selected {{
    background: {ACCENT};
    color: white;
    font-weight: 600;
}}
QListWidget#nav::item:hover:!selected {{ background: {PANEL_2}; color: {TEXT}; }}

QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 10px;
    margin-top: 14px;
    padding: 14px 12px 12px 12px;
    background: {PANEL_2};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: {MUTED};
    font-weight: 600;
}}

QPushButton {{
    background: {PANEL_2};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px 16px;
    color: {TEXT};
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: #5c6070; border-color: #2a2d38; }}
QPushButton[role="primary"] {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
    color: white;
    font-weight: 600;
    padding: 9px 22px;
}}
QPushButton[role="primary"]:hover {{ background: {ACCENT_DIM}; }}
QPushButton[role="primary"]:disabled {{ background: #4a2b39; border-color: #4a2b39; color: #96798a; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTimeEdit, QPlainTextEdit, QTextEdit {{
    background: {BG};
    border: 1px solid {BORDER};
    border-radius: 7px;
    padding: 7px 9px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QTimeEdit:focus {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
/* Крутилки прибрані навмисно: у Qt їхні субконтроли рендеряться
   непередбачувано й виходять крихітні недокнопки. Це поля, куди вводять
   число — значення так само міняється колесом миші та стрілками клавіатури. */
QSpinBox, QDoubleSpinBox, QTimeEdit {{
    min-height: 20px;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button, QTimeEdit::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button, QTimeEdit::down-button {{
    width: 0;
    height: 0;
    border: none;
    background: transparent;
}}
QComboBox QAbstractItemView {{
    background: {PANEL_2};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT};
}}

QCheckBox {{ spacing: 8px; padding: 3px 0; }}
QCheckBox::indicator, QTableWidget::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {BORDER};
    border-radius: 4px;
    background: {BG};
}}
QCheckBox::indicator:checked, QTableWidget::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
    image: none;
}}

QTableWidget {{
    background: {BG};
    border: 1px solid {BORDER};
    border-radius: 8px;
    gridline-color: {BORDER};
    selection-background-color: {PANEL_2};
    selection-color: {TEXT};
}}
QHeaderView::section {{
    background: {PANEL_2};
    color: {MUTED};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 8px;
    font-weight: 600;
}}
QTableWidget::item {{ padding: 6px; }}

QProgressBar {{
    background: {BG};
    border: 1px solid {BORDER};
    border-radius: 8px;
    height: 18px;
    text-align: center;
    color: {TEXT};
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 7px; }}

QPlainTextEdit#log {{
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 12px;
    background: #101116;
    color: #c9ccd6;
}}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #3a3f4e; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #4c5264; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #3a3f4e; border-radius: 5px; min-width: 30px; }}

QToolTip {{
    background: {PANEL_2};
    color: {TEXT};
    border: 1px solid {BORDER};
    padding: 6px;
    border-radius: 6px;
}}
"""
