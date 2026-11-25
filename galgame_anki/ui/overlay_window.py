"""原生級「覆蓋替換」沉浸式對話框覆蓋層（直接蓋住遊戲原對話文字不看兩份字幕、固定尺寸零跳動、懸浮角落微工具列與 Yomitan 懸停查詞）。"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication, QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QVBoxLayout,
    QWidget,
)

from galgame_anki.adapters.window_pin import (
    OVERLAY_WINDOW_TITLE,
    POPUP_WINDOW_TITLE,
    KWinOverlayPinner,
)
from galgame_anki.core.grammar_engine import OfflineGrammarEngine
from galgame_anki.core.sentence_store import SentenceRecord
from galgame_anki.core.tokenizer import Token
from galgame_anki.ui.lookup_popup import OverlayLookupPopup
from galgame_anki.ui.sentence_widget import InteractiveSentenceWidget
from galgame_anki.ui.theme import get_palette

# 三套精調視覺小說／動畫底色預設（100% 遮蓋原對話文字，不產生雙字重影）
BACKDROP_PRESETS: dict[str, dict[str, str]] = {
    "vn_dark": {
        "label": "深黑",
        "opaque_bg": "rgba(13, 17, 29, 0.985)",
        "translucent_bg": "rgba(13, 17, 29, 0.64)",
        "idle_border": "rgba(148, 163, 184, 0.14)",
        "active_border": "rgba(244, 114, 182, 0.72)",
        "token_theme": "starlight",
    },
    "vn_paper": {
        "label": "暖白",
        "opaque_bg": "rgba(255, 250, 253, 0.99)",
        "translucent_bg": "rgba(255, 250, 253, 0.72)",
        "idle_border": "rgba(236, 72, 153, 0.16)",
        "active_border": "rgba(236, 72, 153, 0.70)",
        "token_theme": "sakura",
    },
    "cinema_black": {
        "label": "純黑",
        "opaque_bg": "rgba(0, 0, 0, 0.98)",
        "translucent_bg": "rgba(0, 0, 0, 0.62)",
        "idle_border": "rgba(255, 255, 255, 0.08)",
        "active_border": "rgba(244, 114, 182, 0.68)",
        "token_theme": "starlight",
    },
}
BACKDROP_ORDER = ("vn_dark", "vn_paper", "cinema_black")


class OverlayWindow(QWidget):
    """直接覆蓋替換遊戲對話框／mpv 字幕的沉浸式覆蓋層：平時零按鈕零重影，滑鼠移入才浮出微工具列與懸停查詞。"""

    mine_requested = Signal(object, object)          # (SentenceRecord, Token)
    copy_prompt_requested = Signal(object, object)   # (SentenceRecord, Token | None)
    translate_requested = Signal(object)             # SentenceRecord
    prev_requested = Signal()
    next_requested = Signal()
    merge_prev_requested = Signal()
    open_console_requested = Signal()
    audio_preview_requested = Signal()
    visibility_changed = Signal(bool)
    replace_mode_changed = Signal(bool)
    always_on_top_changed = Signal(bool)
    state_saved = Signal(dict)

    def __init__(
        self,
        theme_name: str = "sakura",
        grammar_engine: OfflineGrammarEngine | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setWindowTitle(OVERLAY_WINDOW_TITLE)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(460, 84)
        self.resize(820, 128)

        self._theme_name = theme_name
        self._grammar_engine = grammar_engine
        self._drag_offset: QPoint | None = None
        self._freeflow_mode = False
        self._saved_normal_size = (820, 128)
        self._initial_saved_geom: tuple[int, int, int, int] | None = None
        self._locked = False
        self._hover_lookup = True
        self._always_on_top = True
        self._replace_mode = True
        self._backdrop = "vn_dark"
        self._font_size = 23
        self._show_furigana = False
        self._translucent_opacity = 0.90
        self._guide_dismissed = False
        self._mouse_inside_overlay = False
        self._mouse_inside_popup = False
        self._show_inline_trans = False
        self._dict_preference = "bilingual"
        self._known_words: set[str] = set()
        self._current_record: SentenceRecord | None = None
        self._pinner = KWinOverlayPinner(OVERLAY_WINDOW_TITLE, POPUP_WINDOW_TITLE)

        self.popup = OverlayLookupPopup(theme_name=self._effective_token_theme())
        self.popup.setWindowTitle(POPUP_WINDOW_TITLE)
        self.popup.mine_requested.connect(self._on_popup_mine)
        self.popup.copy_prompt_requested.connect(self._on_popup_copy_prompt)
        self.popup.audio_preview_requested.connect(self.audio_preview_requested.emit)
        self.popup.hover_entered.connect(self._on_popup_hover_enter)
        self.popup.hover_left.connect(self._on_popup_hover_leave)

        self._popup_hide_timer = QTimer(self)
        self._popup_hide_timer.setSingleShot(True)
        self._popup_hide_timer.setInterval(260)
        self._popup_hide_timer.timeout.connect(self._maybe_hide_popup)

        self._chrome_fade_timer = QTimer(self)
        self._chrome_fade_timer.setSingleShot(True)
        self._chrome_fade_timer.setInterval(450)
        self._chrome_fade_timer.timeout.connect(self._fade_chrome_idle)

        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._clear_toast)

        self._build_ui()
        self.apply_theme(theme_name)
        self._set_chrome_active(False)

    def _effective_token_theme(self) -> str:
        preset = BACKDROP_PRESETS.get(self._backdrop, BACKDROP_PRESETS["vn_dark"])
        return preset["token_theme"]

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(2, 2, 2, 2)

        self.container = QFrame()
        self.container.setObjectName("OverlayContainer")
        box = QVBoxLayout(self.container)
        box.setContentsMargins(16, 12, 16, 10)
        box.setSpacing(4)

        # === 主台詞區佔滿整個對話框（不預留浪費空間的頂部按鈕列，像原生遊戲文字框一樣自然！） ===
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("OverlayScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet(
            "QScrollArea#OverlayScroll { background: transparent; border: none; }"
            "QScrollArea#OverlayScroll > QWidget > QWidget { background: transparent; border: none; }"
        )

        self.sentence_widget = InteractiveSentenceWidget(
            font_size=self._font_size,
            theme_name=self._effective_token_theme(),
        )
        self.sentence_widget.set_hover_lookup_enabled(True)
        self.sentence_widget.token_clicked.connect(self._on_token_clicked)
        self.sentence_widget.token_hovered.connect(self._on_token_hovered)
        self.sentence_widget.token_hover_left.connect(self._on_token_hover_left)
        self.scroll_area.setWidget(self.sentence_widget)
        box.addWidget(self.scroll_area, 1)

        # === 按需行內語境譯文列（點 🌐 才出現，平時隱藏不劇透） ===
        self.inline_trans_label = QLabel("")
        self.inline_trans_label.setWordWrap(True)
        self.inline_trans_label.hide()
        box.addWidget(self.inline_trans_label)

        # === 迷你無阻膠囊列（僅在「🌸 無阻」收合模式顯示） ===
        self.capsule_bar = QWidget()
        self.capsule_bar.setObjectName("OverlayToolbar")
        self.capsule_bar.setStyleSheet("background: transparent; border: none;")
        cl = QHBoxLayout(self.capsule_bar)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(6)
        self.capsule_label = QLabel("無阻模式")
        cl.addWidget(self.capsule_label)
        cl.addStretch()
        expand_btn = QPushButton("展開字幕")
        expand_btn.setFixedHeight(22)
        expand_btn.clicked.connect(self.toggle_freeflow_mode)
        cl.addWidget(expand_btn)
        cap_close = QPushButton("✕")
        cap_close.setFixedSize(22, 22)
        cap_close.clicked.connect(self.hide)
        cl.addWidget(cap_close)
        self.capsule_bar.hide()
        box.addWidget(self.capsule_bar)

        root.addWidget(self.container)

        self.guide_bar = QFrame(self.container)
        self.guide_bar.setObjectName("OverlayGuide")
        gl = QHBoxLayout(self.guide_bar)
        gl.setContentsMargins(8, 2, 6, 2)
        gl.setSpacing(6)

        self.guide_label = QLabel(
            "拖到字幕區覆蓋原字 → 調好大小點鎖定 → 滑鼠移開即隱藏按鈕"
        )
        gl.addWidget(self.guide_label, 1)

        self.guide_dismiss_btn = QPushButton("知道了")
        self.guide_dismiss_btn.setObjectName("OverlayDismissBtn")
        self.guide_dismiss_btn.setFixedHeight(18)
        self.guide_dismiss_btn.clicked.connect(self._dismiss_guide)
        gl.addWidget(self.guide_dismiss_btn)

        # === 極簡浮動懸停微工具列（平時完全隱形，滑鼠移入才浮出 6 個直覺按鈕＋⚙ 設定選單） ===
        self.toolbar_widget = QFrame(self.container)
        self.toolbar_widget.setObjectName("OverlayToolbar")
        bar = QHBoxLayout(self.toolbar_widget)
        bar.setContentsMargins(7, 3, 7, 3)
        bar.setSpacing(4)

        self.drag_hint = QLabel("⠿")
        self.drag_hint.setToolTip("按住框內空白處可拖曳對齊遊戲／mpv 字幕")
        bar.addWidget(self.drag_hint)

        self.toast_label = QLabel("")
        bar.addWidget(self.toast_label)

        self.audio_btn = QPushButton("🔊")
        self.audio_btn.setFixedSize(26, 22)
        self.audio_btn.setToolTip("試聽本句聲優原聲")
        self.audio_btn.clicked.connect(self.audio_preview_requested.emit)
        bar.addWidget(self.audio_btn)

        self.trans_btn = QPushButton("🌐")
        self.trans_btn.setCheckable(True)
        self.trans_btn.setFixedSize(26, 22)
        self.trans_btn.setToolTip("顯示／隱藏本句中文語境理解")
        self.trans_btn.clicked.connect(self._toggle_inline_trans)
        bar.addWidget(self.trans_btn)

        self.prev_btn = QPushButton("◀")
        self.prev_btn.setFixedSize(24, 22)
        self.prev_btn.setToolTip("上一句")
        self.prev_btn.clicked.connect(self.prev_requested.emit)
        bar.addWidget(self.prev_btn)

        self.next_btn = QPushButton("▶")
        self.next_btn.setFixedSize(24, 22)
        self.next_btn.setToolTip("下一句")
        self.next_btn.clicked.connect(self.next_requested.emit)
        bar.addWidget(self.next_btn)

        self.lock_btn = QPushButton("🔓")
        self.lock_btn.setCheckable(True)
        self.lock_btn.setFixedSize(26, 22)
        self.lock_btn.setToolTip("鎖定／解鎖覆蓋層位置與大小")
        self.lock_btn.clicked.connect(self._toggle_lock)
        bar.addWidget(self.lock_btn)

        self.options_btn = QPushButton("⚙")
        self.options_btn.setFixedSize(26, 22)
        self.options_btn.setToolTip("外觀、置頂、字體、合併上句與開啟主控制台")
        self._options_menu = self._build_options_menu()
        self.options_btn.clicked.connect(self._open_options_menu)
        bar.addWidget(self.options_btn)

        hide_btn = QPushButton("✕")
        hide_btn.setFixedSize(22, 22)
        hide_btn.setToolTip("暫時隱藏覆蓋層（可從系統匣隨時喚回）")
        hide_btn.clicked.connect(self.hide)
        bar.addWidget(hide_btn)

        # 右下角透明自由縮放手柄（貼於 container 右下角，僅懸停且未鎖定時可見）
        self.size_grip = QSizeGrip(self.container)
        self.size_grip.setFixedSize(16, 16)
        self.size_grip.setStyleSheet("background: transparent; border: none;")
        self.size_grip.setToolTip("拖曳此角調整覆蓋區域大小以完全遮住遊戲原對話框")

    def _build_options_menu(self) -> QMenu:
        menu = QMenu(self)

        act_console = QAction("主控制台", self)
        act_console.triggered.connect(self.open_console_requested.emit)
        menu.addAction(act_console)

        menu.addSeparator()

        self.act_pin = QAction("永遠置頂", self)
        self.act_pin.setCheckable(True)
        self.act_pin.setChecked(True)
        self.act_pin.triggered.connect(self._toggle_always_on_top)
        menu.addAction(self.act_pin)

        self.act_replace = QAction("實色遮蓋原字", self)
        self.act_replace.setCheckable(True)
        self.act_replace.setChecked(True)
        self.act_replace.triggered.connect(self._toggle_replace_mode)
        menu.addAction(self.act_replace)

        self.act_backdrop = QAction(f"切換底色（{BACKDROP_PRESETS[self._backdrop]['label']}）", self)
        self.act_backdrop.triggered.connect(self._cycle_backdrop)
        menu.addAction(self.act_backdrop)

        act_font_up = QAction("放大字體", self)
        act_font_up.triggered.connect(lambda: self._adjust_font_size(+2))
        menu.addAction(act_font_up)

        act_font_down = QAction("縮小字體", self)
        act_font_down.triggered.connect(lambda: self._adjust_font_size(-2))
        menu.addAction(act_font_down)

        self.act_hover = QAction("懸停自動查詞", self)
        self.act_hover.setCheckable(True)
        self.act_hover.setChecked(True)
        self.act_hover.triggered.connect(self._toggle_hover_lookup)
        menu.addAction(self.act_hover)

        menu.addSeparator()

        act_merge = QAction("合併上一句", self)
        act_merge.triggered.connect(self.merge_prev_requested.emit)
        menu.addAction(act_merge)

        act_freeflow = QAction("縮成無阻膠囊", self)
        act_freeflow.triggered.connect(self.toggle_freeflow_mode)
        menu.addAction(act_freeflow)

        menu.aboutToHide.connect(self._on_options_menu_closed)
        return menu

    def _open_options_menu(self) -> None:
        self._chrome_fade_timer.stop()
        self._popup_hide_timer.stop()
        self.popup.hide()
        pos = self.options_btn.mapToGlobal(QPoint(0, -self._options_menu.sizeHint().height()))
        self._options_menu.popup(pos)

    def _on_options_menu_closed(self) -> None:
        if not self._mouse_inside_overlay:
            self._chrome_fade_timer.start()

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        self._chrome_fade_timer.stop()
        self.popup.hide()
        self._options_menu.popup(event.globalPos())
        event.accept()

    def _reposition_floating_controls(self) -> None:
        cw = self.container.width()
        ch = self.container.height()
        if cw <= 0 or ch <= 0:
            return
        self.toolbar_widget.adjustSize()
        right_margin = 22 if not self._locked else 8
        tw = min(self.toolbar_widget.sizeHint().width(), max(120, cw - right_margin - 6))
        th = self.toolbar_widget.sizeHint().height()
        self.toolbar_widget.setGeometry(max(6, cw - tw - right_margin), max(4, ch - th - 5), tw, th)
        self.toolbar_widget.raise_()

        if not self._guide_dismissed:
            self.guide_bar.adjustSize()
            gw = min(self.guide_bar.sizeHint().width(), max(220, cw - 20))
            gh = max(22, self.guide_bar.sizeHint().height())
            self.guide_bar.setGeometry(10, 4, gw, gh)
            self.guide_bar.raise_()

        self.size_grip.move(cw - self.size_grip.width() - 2, ch - self.size_grip.height() - 2)
        self.size_grip.raise_()

    def apply_theme(self, theme_name: str) -> None:
        self._theme_name = theme_name
        eff_theme = self._effective_token_theme()
        p = get_palette(eff_theme)
        self._update_container_style(active=self._mouse_inside_overlay)
        self.drag_hint.setStyleSheet(
            f"font-size: 12px; color: {p.accent_pink}; font-weight: 700; border: none; background: transparent;"
        )
        self.capsule_label.setStyleSheet(
            f"font-size: 12px; color: {p.accent_pink}; font-weight: 700; border: none; background: transparent;"
        )
        self.toast_label.setStyleSheet(
            f"font-size: 11px; color: {p.accent_mint}; font-weight: 600; border: none; background: transparent;"
        )
        self.inline_trans_label.setStyleSheet(
            f"font-size: 13.5px; color: {p.text_secondary}; background: transparent; padding: 2px 4px;"
        )
        self.guide_label.setStyleSheet(
            f"font-size: 11px; color: {p.text_muted}; background: transparent;"
        )
        self.sentence_widget.set_display_options(self._font_size, self._show_furigana, theme_name=eff_theme)
        self.popup.set_theme_name(eff_theme)

    def _update_container_style(self, active: bool) -> None:
        preset = BACKDROP_PRESETS.get(self._backdrop, BACKDROP_PRESETS["vn_dark"])
        eff_theme = preset["token_theme"]
        p = get_palette(eff_theme)

        if self._replace_mode and not self._freeflow_mode:
            # 遮蓋替換模式：100% 實色遮住遊戲原字，絕不雙字幕重影！
            self.setWindowOpacity(1.0)
            bg = preset["opaque_bg"]
            border = preset["active_border"] if active else preset["idle_border"]
            radius = 10
        else:
            self.setWindowOpacity(max(0.45, min(1.0, self._translucent_opacity)))
            bg = preset["translucent_bg"] if not active else preset["opaque_bg"]
            border = preset["active_border"] if active else preset["idle_border"]
            radius = 18 if self._freeflow_mode else 12

        bar_bg = "rgba(18, 22, 36, 0.94)" if eff_theme == "starlight" else "rgba(255, 245, 250, 0.95)"
        btn_bg = "rgba(255, 255, 255, 0.09)" if eff_theme == "starlight" else "rgba(255, 255, 255, 0.90)"
        btn_checked_bg = "rgba(244, 114, 182, 0.28)" if eff_theme == "starlight" else p.token_selected_bg

        self.container.setStyleSheet(
            f"""
            QFrame#OverlayContainer {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: {radius}px;
            }}
            QFrame#OverlayToolbar, QFrame#OverlayGuide {{
                background-color: {bar_bg};
                border: 1px solid {p.overlay_border};
                border-radius: 10px;
            }}
            QFrame#OverlayToolbar QPushButton, QWidget#OverlayToolbar QPushButton, QPushButton#OverlayDismissBtn {{
                background-color: {btn_bg};
                color: {p.text_primary};
                border: 1px solid {p.border};
                border-radius: 8px;
                padding: 0px 5px;
                font-size: 11px;
                font-weight: 600;
            }}
            QFrame#OverlayToolbar QPushButton:hover, QWidget#OverlayToolbar QPushButton:hover, QPushButton#OverlayDismissBtn:hover {{
                border-color: {p.accent_pink};
                color: {p.accent_pink};
            }}
            QFrame#OverlayToolbar QPushButton:checked {{
                background-color: {btn_checked_bg};
                border-color: {p.accent_pink};
                color: {p.accent_pink};
            }}
            """
        )

    def _set_chrome_active(self, active: bool) -> None:
        if self._freeflow_mode:
            self.toolbar_widget.hide()
            self.guide_bar.hide()
            self.size_grip.hide()
            self._update_container_style(True)
            return
        self.toolbar_widget.setVisible(active)
        self.size_grip.setVisible(active and not self._locked)
        self.guide_bar.setVisible(active and not self._guide_dismissed)
        self._update_container_style(active)
        if active:
            self._reposition_floating_controls()

    def _fade_chrome_idle(self) -> None:
        if not self._mouse_inside_overlay and not self._mouse_inside_popup and not self._options_menu.isVisible():
            self._set_chrome_active(False)

    def restore_saved_state(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        locked: bool,
        hover_lookup: bool,
        guide_dismissed: bool,
        replace_mode: bool = True,
        backdrop: str = "vn_dark",
        font_size: int = 23,
        always_on_top: bool = True,
    ) -> None:
        w = max(460, min(1920, width or 820))
        h = max(84, min(460, height or 128))
        self._saved_normal_size = (w, h)
        self.resize(w, h)
        if x >= 0 and y >= 0:
            self._initial_saved_geom = (int(x), int(y), w, h)
            self.move(x, y)

        self._locked = bool(locked)
        self.lock_btn.blockSignals(True)
        self.lock_btn.setChecked(self._locked)
        self.lock_btn.setText("🔒" if self._locked else "🔓")
        self.lock_btn.blockSignals(False)

        self._hover_lookup = bool(hover_lookup)
        self.act_hover.blockSignals(True)
        self.act_hover.setChecked(self._hover_lookup)
        self.act_hover.blockSignals(False)
        self.sentence_widget.set_hover_lookup_enabled(self._hover_lookup)

        self._always_on_top = bool(always_on_top)
        self.act_pin.blockSignals(True)
        self.act_pin.setChecked(self._always_on_top)
        self.act_pin.blockSignals(False)
        self._apply_qt_stay_on_top_flag(self._always_on_top, reshow=False)
        self._pinner.apply_pin(self._always_on_top, initial_geometry=self._initial_saved_geom)

        self._replace_mode = bool(replace_mode)
        self.act_replace.blockSignals(True)
        self.act_replace.setChecked(self._replace_mode)
        self.act_replace.blockSignals(False)

        self._backdrop = backdrop if backdrop in BACKDROP_PRESETS else "vn_dark"
        self.act_backdrop.setText(f"切換底色（{BACKDROP_PRESETS[self._backdrop]['label']}）")

        self._font_size = max(15, min(38, int(font_size or 23)))
        self._guide_dismissed = bool(guide_dismissed)
        self.guide_bar.setVisible(not self._guide_dismissed and self._mouse_inside_overlay)
        self.apply_theme(self._theme_name)

    def _emit_state(self) -> None:
        w, h = self._saved_normal_size if self._freeflow_mode else (self.width(), self.height())
        self.state_saved.emit(
            {
                "x": self.x(),
                "y": self.y(),
                "width": w,
                "height": h,
                "locked": self._locked,
                "hover_lookup": self._hover_lookup,
                "always_on_top": self._always_on_top,
                "replace_mode": self._replace_mode,
                "backdrop": self._backdrop,
                "font_size": self._font_size,
                "guide_dismissed": self._guide_dismissed,
            }
        )

    def set_context(
        self,
        dict_preference: str,
        known_words: set[str],
        font_size: int,
        show_furigana: bool,
        opacity: float,
        theme_name: str = "sakura",
    ) -> None:
        self._dict_preference = dict_preference
        self._known_words = known_words
        self._font_size = max(15, min(38, int(font_size or self._font_size)))
        self._show_furigana = show_furigana
        self._translucent_opacity = opacity
        self.apply_theme(theme_name)

    def show_record(self, record: SentenceRecord) -> None:
        """更新覆蓋層顯示的台詞——嚴格維持固定外框尺寸，絕不自動跳動變大變小！"""
        self._current_record = record
        self.sentence_widget.set_sentence(record.tokenized)
        self.scroll_area.verticalScrollBar().setValue(0)
        self.popup.hide()

        if self._show_inline_trans and record.sentence_translation:
            self.inline_trans_label.setText(record.sentence_translation)
            self.inline_trans_label.show()
        else:
            self.inline_trans_label.hide()

    def set_inline_translation(self, record: SentenceRecord, text: str) -> None:
        if self._current_record is record and self._show_inline_trans and text:
            self.inline_trans_label.setText(text)
            self.inline_trans_label.show()

    def _apply_qt_stay_on_top_flag(self, pinned: bool, reshow: bool = True) -> None:
        was_visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(pinned))
        self.popup.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(pinned))
        if was_visible and reshow:
            self.show()
            self.raise_()

    def _toggle_always_on_top(self, checked: bool) -> None:
        self._always_on_top = bool(checked)
        self.act_pin.blockSignals(True)
        self.act_pin.setChecked(self._always_on_top)
        self.act_pin.blockSignals(False)
        if self._pinner.is_supported:
            # KDE Plasma Wayland：直接透過 KWin DBus 即時切換 keepAbove（零閃爍、不重建視窗！）
            self._pinner.apply_pin(checked, force_reload=True)
        else:
            # X11 / Windows / macOS：透過 Qt 原生 WindowStaysOnTopHint 切換
            self._apply_qt_stay_on_top_flag(checked, reshow=True)
        self.show_toast("已置頂" if checked else "已取消置頂")
        self.always_on_top_changed.emit(checked)
        self._emit_state()

    def shutdown_window_pin(self) -> None:
        """退出主程式時卸載 KWin 置頂腳本。"""
        self._pinner.shutdown()

    def _toggle_replace_mode(self, checked: bool) -> None:
        self._replace_mode = bool(checked)
        self.act_replace.blockSignals(True)
        self.act_replace.setChecked(self._replace_mode)
        self.act_replace.blockSignals(False)
        self._update_container_style(active=self._mouse_inside_overlay)
        self.show_toast("遮蓋模式" if checked else "輕透模式")
        self.replace_mode_changed.emit(checked)
        self._emit_state()

    def _cycle_backdrop(self) -> None:
        idx = BACKDROP_ORDER.index(self._backdrop) if self._backdrop in BACKDROP_ORDER else 0
        self._backdrop = BACKDROP_ORDER[(idx + 1) % len(BACKDROP_ORDER)]
        self.act_backdrop.setText(f"切換底色（{BACKDROP_PRESETS[self._backdrop]['label']}）")
        self.apply_theme(self._theme_name)
        self.show_toast(f"底色：{BACKDROP_PRESETS[self._backdrop]['label']}")
        self._emit_state()

    def _adjust_font_size(self, delta: int) -> None:
        self._font_size = max(15, min(38, self._font_size + delta))
        self.sentence_widget.set_display_options(
            self._font_size,
            self._show_furigana,
            theme_name=self._effective_token_theme(),
        )
        self.show_toast(f"字體 {self._font_size}px")
        self._emit_state()

    def _toggle_lock(self, checked: bool) -> None:
        self._locked = bool(checked)
        self.lock_btn.blockSignals(True)
        self.lock_btn.setChecked(self._locked)
        self.lock_btn.setText("🔒" if self._locked else "🔓")
        self.lock_btn.blockSignals(False)
        if checked and not self._guide_dismissed:
            self._guide_dismissed = True
            self.guide_bar.hide()
        self.size_grip.setVisible(not checked and self._mouse_inside_overlay and not self._freeflow_mode)
        self._reposition_floating_controls()
        self.show_toast("已鎖定位置" if checked else "已解鎖，可拖曳對齊")
        self._emit_state()

    def _toggle_hover_lookup(self, checked: bool) -> None:
        self._hover_lookup = bool(checked)
        self.act_hover.blockSignals(True)
        self.act_hover.setChecked(self._hover_lookup)
        self.act_hover.blockSignals(False)
        self.sentence_widget.set_hover_lookup_enabled(self._hover_lookup)
        self.show_toast("懸停查詞" if checked else "點擊查詞")
        self._emit_state()

    def _toggle_inline_trans(self, checked: bool) -> None:
        self._show_inline_trans = checked
        if not checked:
            self.inline_trans_label.hide()
            return
        if self._current_record is None:
            return
        if self._current_record.sentence_translation:
            self.inline_trans_label.setText(self._current_record.sentence_translation)
            self.inline_trans_label.show()
        else:
            self.inline_trans_label.setText("取得譯文中…")
            self.inline_trans_label.show()
            self.translate_requested.emit(self._current_record)

    def _dismiss_guide(self) -> None:
        self._guide_dismissed = True
        self.guide_bar.hide()
        self._emit_state()

    def toggle_freeflow_mode(self) -> None:
        """無阻沉浸：縮成迷你角落膠囊；展開：精準恢復使用者設定的固定對話框尺寸。"""
        if not self._freeflow_mode:
            self._saved_normal_size = (self.width(), self.height())
            self._freeflow_mode = True
            self.scroll_area.hide()
            self.inline_trans_label.hide()
            self.guide_bar.hide()
            self.toolbar_widget.hide()
            self.size_grip.hide()
            self.popup.hide()
            self.capsule_bar.show()
            self.setMinimumSize(210, 38)
            self.resize(230, 42)
            self._set_chrome_active(True)
        else:
            target_w, target_h = self._saved_normal_size
            self.capsule_bar.hide()
            self.setMinimumSize(460, 84)
            self.scroll_area.show()
            self.resize(target_w, target_h)
            self._freeflow_mode = False
            if self._current_record is not None:
                self.show_record(self._current_record)
            self._set_chrome_active(self._mouse_inside_overlay)

    def show_toast(self, message: str, ms: int = 3000) -> None:
        self.toast_label.setText(f" {message}")
        if not self._freeflow_mode:
            self.toolbar_widget.show()
            self._reposition_floating_controls()
        self._toast_timer.start(ms)

    def _clear_toast(self) -> None:
        self.toast_label.setText("")
        if not self._mouse_inside_overlay and not self._freeflow_mode:
            self._fade_chrome_idle()

    def _position_and_show_popup(self, token: Token, anchor_top: QPoint) -> None:
        already_known = token.lemma in self._known_words or token.surface in self._known_words
        particle_info = (
            self._grammar_engine.get_particle_info(token.surface)
            if self._grammar_engine is not None
            else None
        )
        self.popup.popup_for_token(token, self._dict_preference, already_known, particle_info=particle_info)

        # 優先彈在覆蓋層上方（絕不擋住覆蓋框內台詞或下方畫面）
        screen = QGuiApplication.primaryScreen()
        screen_rect = screen.availableGeometry() if screen else None
        px = anchor_top.x()
        py = self.y() - self.popup.height() - 6
        if screen_rect is not None:
            if py < screen_rect.top() + 8:
                py = self.y() + self.height() + 6
            px = max(screen_rect.left() + 8, min(px, screen_rect.right() - self.popup.width() - 8))
        else:
            py = max(12, py)
        self.popup.move(px, py)

    def _on_token_clicked(self, token: Token, anchor_top: QPoint) -> None:
        self._popup_hide_timer.stop()
        self._position_and_show_popup(token, anchor_top)

    def _on_token_hovered(self, token: Token, anchor_top: QPoint) -> None:
        self._popup_hide_timer.stop()
        self._position_and_show_popup(token, anchor_top)

    def _on_token_hover_left(self) -> None:
        if self._hover_lookup:
            self._popup_hide_timer.start()

    def _on_popup_hover_enter(self) -> None:
        self._mouse_inside_popup = True
        self._popup_hide_timer.stop()
        self._chrome_fade_timer.stop()

    def _on_popup_hover_leave(self) -> None:
        self._mouse_inside_popup = False
        if self._hover_lookup:
            self._popup_hide_timer.start()
        if not self._mouse_inside_overlay:
            self._chrome_fade_timer.start()

    def _maybe_hide_popup(self) -> None:
        if not self._mouse_inside_popup:
            self.popup.hide()

    def _on_popup_mine(self, token: Token) -> None:
        if self._current_record is not None:
            self.mine_requested.emit(self._current_record, token)

    def _on_popup_copy_prompt(self, token: Token | None) -> None:
        if self._current_record is not None:
            self.copy_prompt_requested.emit(self._current_record, token)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._mouse_inside_overlay = True
        self._chrome_fade_timer.stop()
        self._set_chrome_active(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._mouse_inside_overlay = False
        self._chrome_fade_timer.start()
        if self._hover_lookup:
            self._popup_hide_timer.start()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self._locked:
            win = self.windowHandle()
            if win is not None and win.startSystemMove():
                event.accept()
                return
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None and not self._locked and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None:
            self._drag_offset = None
            self._emit_state()
        super().mouseReleaseEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if not self._freeflow_mode:
            self._saved_normal_size = (self.width(), self.height())
            self._reposition_floating_controls()

    def hideEvent(self, event) -> None:  # noqa: N802
        self.popup.hide()
        self._emit_state()
        self.visibility_changed.emit(False)
        super().hideEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._reposition_floating_controls()
        self._set_chrome_active(self._mouse_inside_overlay)
        self._pinner.ensure_active(initial_geometry=self._initial_saved_geom)
        self.visibility_changed.emit(True)
