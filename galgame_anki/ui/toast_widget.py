"""輕量右下角浮動通知條（支援「一鍵收錄 → ↩ 撤銷」，永不搶佔遊戲／mpv 焦點）。"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from galgame_anki.adapters.window_pin import TOAST_WINDOW_TITLE
from galgame_anki.ui.theme import get_palette


class FloatingUndoToast(QWidget):
    """螢幕右下角置頂浮動提示條：顯示「已收錄『xxx』｜↩ 撤銷」，6 秒後自動收起。"""

    undo_requested = Signal(int, str)  # (note_id, lemma)

    def __init__(self, theme_name: str = "starlight", parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setWindowTitle(TOAST_WINDOW_TITLE)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self._theme_name = theme_name
        self._pending_note_id: int = 0
        self._pending_lemma: str = ""
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

        self._build_ui()
        self.apply_theme(theme_name)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(2, 2, 2, 2)

        self.card = QFrame()
        self.card.setObjectName("UndoToastCard")
        row = QHBoxLayout(self.card)
        row.setContentsMargins(14, 9, 10, 9)
        row.setSpacing(10)

        self.msg_label = QLabel("")
        self.msg_label.setWordWrap(False)
        row.addWidget(self.msg_label)

        self.undo_btn = QPushButton("↩ 撤銷")
        self.undo_btn.setObjectName("UndoToastBtn")
        self.undo_btn.setFixedHeight(24)
        self.undo_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.undo_btn.clicked.connect(self._on_undo_clicked)
        row.addWidget(self.undo_btn)

        self.close_btn = QPushButton("✕")
        self.close_btn.setObjectName("UndoToastClose")
        self.close_btn.setFixedSize(22, 22)
        self.close_btn.clicked.connect(self.hide)
        row.addWidget(self.close_btn)

        root.addWidget(self.card)

    def apply_theme(self, theme_name: str) -> None:
        self._theme_name = theme_name
        p = get_palette(theme_name)
        bg = "rgba(18, 22, 36, 0.96)" if theme_name == "starlight" else "rgba(255, 250, 253, 0.97)"
        self.card.setStyleSheet(
            f"""
            QFrame#UndoToastCard {{
                background-color: {bg};
                border: 1.5px solid {p.accent_pink};
                border-radius: 13px;
            }}
            QLabel {{
                color: {p.text_primary};
                font-size: 13px;
                font-weight: 600;
                background: transparent;
            }}
            QPushButton#UndoToastBtn {{
                background-color: {p.accent_pink};
                color: #ffffff;
                border: none;
                border-radius: 8px;
                padding: 0px 10px;
                font-size: 12px;
                font-weight: 700;
            }}
            QPushButton#UndoToastBtn:hover {{
                background-color: {p.accent_lilac};
            }}
            QPushButton#UndoToastClose {{
                background: transparent;
                color: {p.text_muted};
                border: none;
                padding: 0px;
                min-width: 20px;
                font-size: 12px;
                font-weight: 700;
            }}
            QPushButton#UndoToastClose:hover {{
                color: {p.accent_pink};
            }}
            """
        )

    def show_mined(self, message: str, note_id: int = 0, lemma: str = "", duration_ms: int = 6000) -> None:
        """顯示收錄成功通知並附帶「↩ 撤銷」按鈕（duration_ms 後自動收起）。"""
        self._pending_note_id = int(note_id or 0)
        self._pending_lemma = lemma
        self.msg_label.setText(message)
        self.undo_btn.setVisible(self._pending_note_id > 0)
        self.undo_btn.setEnabled(True)
        self.adjustSize()
        self._position_bottom_right()
        self.show()
        self.raise_()
        self._hide_timer.start(duration_ms)

    def show_info(self, message: str, duration_ms: int = 3500) -> None:
        """顯示簡短狀態訊息（不含撤銷按鈕）。"""
        self.show_mined(message, note_id=0, lemma="", duration_ms=duration_ms)

    def _position_bottom_right(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        w = max(self.sizeHint().width(), 220)
        h = max(self.sizeHint().height(), 42)
        self.resize(w, h)
        x = area.x() + area.width() - w - 18
        y = area.y() + area.height() - h - 22
        self.move(max(area.x() + 8, x), max(area.y() + 8, y))

    def _on_undo_clicked(self) -> None:
        if self._pending_note_id <= 0:
            self.hide()
            return
        nid = self._pending_note_id
        lemma = self._pending_lemma
        self._pending_note_id = 0
        self.undo_btn.setEnabled(False)
        self.undo_btn.hide()
        self.msg_label.setText(f"↩ 正在撤銷「{lemma}」…")
        self.adjustSize()
        self._position_bottom_right()
        self.undo_requested.emit(nid, lemma)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hide_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self.isVisible():
            self._hide_timer.start(2200)
        super().leaveEvent(event)
