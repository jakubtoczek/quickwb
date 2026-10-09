"""Entry point: launch the QuickWB GUI."""

from __future__ import annotations

import sys
from pathlib import Path

from . import winshell

_PKG = Path(__file__).resolve().parent  # this package's folder


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


def _splash():
    """The icon and the name, on screen while the rest of the app is imported and built.
    A frameless label, not QSplashScreen: that one waits up to a second for Windows to
    report it exposed (1.0 s against 0.02 s, measured on 2026-10-09)."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPainter, QPixmap
    from PySide6.QtWidgets import QLabel

    pm = QPixmap(320, 150)
    pm.fill(QColor("#2b2b2b"))
    p = QPainter(pm)
    icon = QPixmap(str(_PKG / "assets" / "quickwb.png"))
    if not icon.isNull():
        p.drawPixmap(20, 35, icon.scaled(80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    p.setPen(QColor("#e0e0e0"))
    f = p.font()
    f.setPointSize(16)
    p.setFont(f)
    p.drawText(120, 70, "QuickWB")
    f.setPointSize(9)
    p.setFont(f)
    p.setPen(QColor("#8d8d8d"))
    p.drawText(120, 95, "starting…")
    p.end()
    s = QLabel()
    s.setWindowFlags(Qt.SplashScreen | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
    s.setPixmap(pm)
    s.resize(pm.size())
    s.move(s.screen().geometry().center() - s.rect().center())
    s.show()
    return s


def main() -> int:
    from PySide6.QtWidgets import QApplication

    winshell.taskbar_identity("QuickWB", "QuickWB", _PKG.parents[1] / "QuickWB.bat",
                              _PKG / "assets" / "quickwb.ico")  # before any window
    app = QApplication(sys.argv)
    splash = _splash()
    app.processEvents()

    from .app import APP_NAME, MainWindow, app_icon   # the slow part: on screen meanwhile

    app.setApplicationName(APP_NAME)
    icon = app_icon()
    if icon is not None:
        app.setWindowIcon(icon)
    _apply_dark(app)
    win = MainWindow()
    win.show()
    splash.close()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
