"""Точка входу GUI: python -m igsaved"""

from __future__ import annotations

import sys


def _claim_taskbar_identity() -> None:
    """Без цього Windows показує в панелі задач іконку pythonw.exe, а не нашу.

    AppUserModelID виділяє процес в окремий застосунок — тоді панель задач бере
    іконку з вікна. Викликати треба до створення QApplication.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "amriel.instref.app.1"
        )
    except Exception:  # noqa: BLE001 — косметика не варта падіння
        pass


def main() -> int:
    try:
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print(
            "Не встановлено PySide6.\n"
            "Запусти install.bat або виконай: pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 2

    from . import APP_NAME
    from .config import resource_dir
    from .ui import theme
    from .ui.main_window import MainWindow

    _claim_taskbar_identity()

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setDesktopFileName(APP_NAME)
    app.setStyleSheet(theme.STYLESHEET)
    # Вікно може ховатись у трей — не завершувати процес разом із ним.
    app.setQuitOnLastWindowClosed(False)

    icon_path = resource_dir() / "assets" / "icon.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
