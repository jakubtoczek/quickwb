"""Entry point: launch the QuickWB GUI."""

from __future__ import annotations

import sys


def _apply_dark(app):
    """Deterministic dark-grey Fusion theme so every widget matches regardless of
    the host OS theme (one dark palette shared across these small tools)."""
    from PySide6.QtGui import QColor, QPalette

    app.setStyle("Fusion")
    win = QColor("#2b2b2b")
    base = QColor("#232323")
    text = QColor("#e0e0e0")
    hi = QColor("#3d6ea5")
    p = QPalette()
    p.setColor(QPalette.Window, win)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, base)
    p.setColor(QPalette.AlternateBase, win)
    p.setColor(QPalette.ToolTipBase, win)
    p.setColor(QPalette.ToolTipText, text)
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, win)
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.Highlight, hi)
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.Disabled, QPalette.Text, QColor("#7a7a7a"))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#7a7a7a"))
    app.setPalette(p)


def _set_app_user_model_id(app_id):
    """Windows: an explicit AppUserModelID makes the taskbar group the app under
    its own icon (pythonw.exe otherwise shows the generic Python icon)."""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:  # noqa: BLE001 — non-Windows or blocked; harmless
        pass


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from .app import APP_NAME, MainWindow, app_icon

    _set_app_user_model_id(APP_NAME)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    icon = app_icon()
    if icon is not None:
        app.setWindowIcon(icon)
    _apply_dark(app)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
