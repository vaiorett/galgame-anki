"""可互動點擊分詞台詞渲染元件（支援二次元雙主題與自然視覺小說行文節奏）。"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QGraphicsDropShadowEffect,
    QLayout,
    QLayoutItem,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from galgame_anki.core.tokenizer import (
    Token,
    TokenizedSentence,
    build_conjugated_inline_furigana,
    contains_kanji,
)
from galgame_anki.ui.theme import get_palette


class FlowLayout(QLayout):
    """Qt 標準自動換行流式佈局，讓日文台詞自然橫排換行且每個詞都能獨立點擊，並支援逐行左對齊／居中對齊。"""

    def __init__(self, parent: QWidget | None = None, margin: int = 2, h_spacing: int = 1, v_spacing: int = 4) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self._horizontal_alignment: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft
        self.setContentsMargins(margin, margin, margin, margin)

    def set_horizontal_alignment(self, alignment: Qt.AlignmentFlag) -> None:
        if self._horizontal_alignment != alignment:
            self._horizontal_alignment = alignment
            self.invalidate()
            self.activate()

    def horizontal_alignment(self) -> Qt.AlignmentFlag:
        return self._horizontal_alignment

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        lines: list[tuple[list[tuple[QLayoutItem, QSize]], int, int]] = []
        current_line: list[tuple[QLayoutItem, QSize]] = []
        line_width = 0
        line_height = 0

        for item in self._items:
            hint = item.sizeHint()
            candidate_w = hint.width() if not current_line else line_width + self._h_spacing + hint.width()
            if current_line and candidate_w > effective.width():
                lines.append((current_line, line_width, line_height))
                current_line = [(item, hint)]
                line_width = hint.width()
                line_height = hint.height()
            else:
                current_line.append((item, hint))
                line_width = candidate_w
                line_height = max(line_height, hint.height())

        if current_line:
            lines.append((current_line, line_width, line_height))

        y = effective.y()
        center = bool(self._horizontal_alignment & Qt.AlignmentFlag.AlignHCenter)
        for idx, (line_items, lw, lh) in enumerate(lines):
            if idx > 0:
                y += self._v_spacing
            if not test_only:
                x = effective.x() + max(0, (effective.width() - lw) // 2) if center else effective.x()
                for item, hint in line_items:
                    item.setGeometry(QRect(QPoint(x, y), hint))
                    x += hint.width() + self._h_spacing
            y += lh

        return y - rect.y() + m.bottom()


class InteractiveSentenceWidget(QWidget):
    """將 TokenizedSentence 渲染為自然緊湊、支援【滑鼠懸停即查（Yomitan 風格）＋點擊選詞】的日語台詞區。"""

    token_clicked = Signal(object, QPoint)   # (Token, global_top_left_point)
    token_hovered = Signal(object, QPoint)   # (Token, global_top_left_point)
    token_hover_left = Signal()

    def __init__(self, font_size: int = 22, theme_name: str = "sakura", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._font_size = font_size
        self._show_furigana = False
        self._theme_name = theme_name
        self._cinema_shadow = False
        self._text_align = "left"
        self._hover_enabled = False
        self._selected_index: int = -1
        self._buttons: list[tuple[QPushButton, Token]] = []
        self._btn_to_token: dict[QPushButton, Token] = {}
        self._sentence: TokenizedSentence | None = None
        self._pending_hover: tuple[Token, QPushButton] | None = None

        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(140)
        self._hover_timer.timeout.connect(self._fire_hover)

        self._flow = FlowLayout(self, margin=4, h_spacing=1, v_spacing=5)
        self.setLayout(self._flow)
        self.setStyleSheet("background: transparent;")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)

    @property
    def text_align(self) -> str:
        return self._text_align

    def set_text_align(self, align: str) -> None:
        normalized = "center" if align == "center" else "left"
        self._text_align = normalized
        self._flow.set_horizontal_alignment(
            Qt.AlignmentFlag.AlignHCenter if normalized == "center" else Qt.AlignmentFlag.AlignLeft
        )
        self.updateGeometry()
        self.update()

    def set_hover_lookup_enabled(self, enabled: bool) -> None:
        self._hover_enabled = enabled
        if not enabled:
            self._hover_timer.stop()

    def set_display_options(
        self,
        font_size: int,
        show_furigana: bool,
        theme_name: str | None = None,
        cinema_shadow: bool | None = None,
    ) -> None:
        self._font_size = font_size
        self._show_furigana = show_furigana
        if theme_name:
            self._theme_name = theme_name
        if cinema_shadow is not None:
            self._cinema_shadow = bool(cinema_shadow)
        if self._sentence is not None:
            self.set_sentence(self._sentence)

    def set_theme_name(self, theme_name: str) -> None:
        self._theme_name = theme_name
        for i, (b, t) in enumerate(self._buttons):
            b.setStyleSheet(self._token_style(t, selected=(i == self._selected_index)))

    def clear_tokens(self) -> None:
        self._hover_timer.stop()
        self._pending_hover = None
        while self._flow.count():
            item = self._flow.takeAt(0)
            if item and item.widget():
                w = item.widget()
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self._buttons.clear()
        self._btn_to_token.clear()
        self._selected_index = -1

    def set_sentence(self, tokenized: TokenizedSentence) -> None:
        self.clear_tokens()
        self._sentence = tokenized

        for idx, tok in enumerate(tokenized.tokens):
            label_text = tok.surface
            if (
                self._show_furigana
                and tok.reading
                and tok.reading != tok.surface
                and contains_kanji(tok.surface)
            ):
                label_text = build_conjugated_inline_furigana(tok.surface, tok.lemma, tok.reading)

            btn = QPushButton(label_text)
            btn.setFlat(True)
            btn.setCursor(
                Qt.CursorShape.ArrowCursor
                if tok.is_punctuation
                else Qt.CursorShape.PointingHandCursor
            )
            btn.setEnabled(not tok.is_punctuation)
            btn.setStyleSheet(self._token_style(tok, selected=False))
            if self._cinema_shadow:
                shadow = QGraphicsDropShadowEffect(btn)
                shadow.setBlurRadius(9.0)
                shadow.setOffset(0.0, 1.5)
                shadow.setColor(QColor(0, 0, 0, 250))
                btn.setGraphicsEffect(shadow)
            if not tok.is_punctuation:
                btn.clicked.connect(lambda _checked=False, i=idx, t=tok, b=btn: self._on_click(i, t, b))
                btn.installEventFilter(self)
                self._btn_to_token[btn] = tok
            self._flow.addWidget(btn)
            btn.show()  # 讓捲動區立即取得新詞元的尺寸。
            self._buttons.append((btn, tok))

        self.updateGeometry()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if self._hover_enabled and isinstance(watched, QPushButton) and watched in self._btn_to_token:
            if event.type() == QEvent.Type.Enter:
                self._pending_hover = (self._btn_to_token[watched], watched)
                self._hover_timer.start()
            elif event.type() == QEvent.Type.Leave:
                self._hover_timer.stop()
                self.token_hover_left.emit()
        return super().eventFilter(watched, event)

    def _fire_hover(self) -> None:
        if not self._hover_enabled or not self._pending_hover:
            return
        tok, btn = self._pending_hover
        if btn in self._btn_to_token:
            anchor = btn.mapToGlobal(QPoint(0, 0))
            self.token_hovered.emit(tok, anchor)

    def _on_click(self, idx: int, token: Token, btn: QPushButton) -> None:
        self._hover_timer.stop()
        self._selected_index = idx
        for i, (b, t) in enumerate(self._buttons):
            b.setStyleSheet(self._token_style(t, selected=(i == idx)))
        anchor = btn.mapToGlobal(QPoint(0, 0))
        self.token_clicked.emit(token, anchor)

    def _token_style(self, tok: Token, selected: bool) -> str:
        fs = self._font_size
        p = get_palette(self._theme_name)

        if tok.is_punctuation:
            punct_color = "#f8fafc" if self._cinema_shadow else p.text_muted
            return (
                f"QPushButton {{ background: transparent; border: none; "
                f"color: {punct_color}; font-size: {fs}px; padding: 1px 0px; }}"
            )

        bg = "transparent"
        border_bottom = "2px solid transparent"
        color = "#ffffff" if self._cinema_shadow else p.text_primary
        weight = "600" if self._cinema_shadow else "500"

        if selected:
            bg = p.token_selected_bg
            border_bottom = f"2px solid {p.accent_pink}"
            color = p.token_selected_fg
            weight = "700"
        elif tok.is_1t_candidate:
            bg = "rgba(250, 204, 21, 0.22)" if self._cinema_shadow else p.token_1t_bg
            border_bottom = f"2px solid {'#fde047' if self._cinema_shadow else p.token_1t_line}"
            color = "#fef08a" if self._cinema_shadow else p.token_1t_fg
            weight = "700"
        elif tok.is_function_word:
            color = "#e2e8f0" if self._cinema_shadow else p.text_muted
        elif not tok.is_known and tok.has_dict_entry:
            border_bottom = f"2px dotted {'rgba(244, 114, 182, 0.85)' if self._cinema_shadow else p.border_accent}"
            color = "#ffffff" if self._cinema_shadow else p.text_primary
        elif tok.is_known:
            color = "#f1f5f9" if self._cinema_shadow else p.text_secondary

        hover_bg = "rgba(244, 114, 182, 0.30)" if self._cinema_shadow else p.token_hover_bg
        return (
            f"QPushButton {{ background: {bg}; border: none; border-bottom: {border_bottom}; "
            f"border-radius: 6px; color: {color}; font-size: {fs}px; font-weight: {weight}; "
            f"padding: 1px 3px; }}"
            f"QPushButton:hover {{ background: {hover_bg}; color: #ffffff; }}"
        )
