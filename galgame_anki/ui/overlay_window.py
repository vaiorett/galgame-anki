"""原生級「覆蓋替換」沉浸式對話框覆蓋層（直接蓋住遊戲原對話文字不看兩份字幕、固定字幕區、右下角快捷切換與自訂視窗大小、Yomitan 懸停查詞）。"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QGuiApplication, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from galgame_anki.adapters.window_pin import (
    OVERLAY_WINDOW_TITLE,
    POPUP_WINDOW_TITLE,
    KWinOverlayPinner,
)
from galgame_anki.core.dictionary import DictionaryEngine
from galgame_anki.core.grammar_engine import OfflineGrammarEngine
from galgame_anki.core.phrase_matcher import build_phrase_token, find_phrase_for_token
from galgame_anki.core.sentence_store import SentenceRecord
from galgame_anki.core.tokenizer import Token
from galgame_anki.ui.lookup_popup import OverlayLookupPopup
from galgame_anki.ui.sentence_widget import InteractiveSentenceWidget
from galgame_anki.ui.theme import build_stylesheet, get_palette

OVERLAY_MIN_WIDTH = 220
OVERLAY_MAX_WIDTH = 2560
OVERLAY_MIN_HEIGHT = 60
OVERLAY_MAX_HEIGHT = 600
OVERLAY_DEFAULT_WIDTH = 820
OVERLAY_DEFAULT_HEIGHT = 128

OVERLAY_SIZE_PRESETS: tuple[tuple[str, int, int], ...] = (
    ("精簡單行", 520, 88),
    ("標準", 820, 128),
    ("寬幅字幕", 1080, 136),
    ("雙行精讀", 920, 184),
)

# 三套精調視覺小說／動畫底色預設（100% 遮蓋原對話文字，不產生雙字重影）
BACKDROP_PRESETS: dict[str, dict[str, str]] = {
    "transparent": {
        "label": "透明字幕",
        "opaque_bg": "rgba(0, 0, 0, 0.0)",
        "translucent_bg": "rgba(0, 0, 0, 0.0)",
        "idle_border": "transparent",
        "active_border": "transparent",
        "token_theme": "starlight",
    },
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
BACKDROP_ORDER = ("transparent", "vn_dark", "vn_paper", "cinema_black")


def _lookup_popup_geometry(area: QRect, subtitles: QRect, size: QSize, anchor_x: int) -> QRect:
    """優先用字幕上方，再試下方與左右。每種位置都從可用區扣掉字幕。"""
    area = area.adjusted(8, 8, -8, -8)
    if area.isEmpty():
        return QRect()
    blocks = (
        QRect(area.left(), area.top(), area.width(), min(area.bottom() + 1, subtitles.top() - 6) - area.top()),
        QRect(area.left(), max(area.top(), subtitles.bottom() + 7), area.width(), area.bottom() + 1 - max(area.top(), subtitles.bottom() + 7)),
        QRect(area.left(), area.top(), min(area.right() + 1, subtitles.left() - 6) - area.left(), area.height()),
        QRect(max(area.left(), subtitles.right() + 7), area.top(), area.right() + 1 - max(area.left(), subtitles.right() + 7), area.height()),
    )
    candidates = [b for b in blocks if b.width() >= min(size.width(), 300) and b.height() >= min(size.height(), 132)]
    if not candidates:
        return QRect()
    block = next((b for b in candidates if b.width() >= size.width() and b.height() >= size.height()),
                 max(candidates, key=lambda b: min(b.width(), size.width()) * min(b.height(), size.height())))
    index = blocks.index(block)
    width, height = min(block.width(), size.width()), min(block.height(), size.height())
    x = min(max(anchor_x, block.left()), block.right() + 1 - width)
    y = min(max(subtitles.top(), block.top()), block.bottom() + 1 - height)
    if index == 0:
        y = block.bottom() + 1 - height
    elif index == 1:
        y = block.top()
    elif index == 2:
        x = block.right() + 1 - width
    elif index == 3:
        x = block.left()
    return QRect(x, y, width, height)


def _wayland_lookup_anchor(subtitles: QSize, cursor_x: int) -> QRect:
    return QRect(max(0, min(cursor_x, subtitles.width() - 1)), 0, 1, subtitles.height())


class _CornerMark(QWidget):
    """右下角縮放圓弧提示（平時極細低調，滑鼠移至右下角縮放區時亮起）。"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFixedSize(7, 7)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._highlighted = False
        self._highlight_color = QColor("#F472B6")

    def set_highlight_color(self, color: str) -> None:
        self._highlight_color = QColor(color)
        if self._highlighted:
            self.update()

    def set_highlighted(self, highlighted: bool) -> None:
        flag = bool(highlighted)
        if self._highlighted != flag:
            self._highlighted = flag
            self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._highlighted:
            pen = QPen(self._highlight_color, 1.8)
        else:
            pen = QPen(self.palette().windowText().color(), 1.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(QRectF(-3, -3, 9, 9), 0, -90 * 16)


class OverlaySizeDialog(QDialog):
    """自訂覆蓋層視窗大小對話框（即時預覽、預設尺寸、寬高滑桿與數值微調）。"""

    size_changed = Signal(int, int)

    def __init__(
        self,
        current_width: int,
        current_height: int,
        *,
        theme_name: str = "sakura",
        max_width: int = OVERLAY_MAX_WIDTH,
        max_height: int = OVERLAY_MAX_HEIGHT,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setWindowTitle("自訂覆蓋層視窗大小")
        self.setMinimumWidth(420)
        self._theme_name = theme_name
        self._max_width = max(OVERLAY_MIN_WIDTH, min(OVERLAY_MAX_WIDTH, int(max_width)))
        self._max_height = max(OVERLAY_MIN_HEIGHT, min(OVERLAY_MAX_HEIGHT, int(max_height)))
        self._orig_width = max(OVERLAY_MIN_WIDTH, min(self._max_width, int(current_width)))
        self._orig_height = max(OVERLAY_MIN_HEIGHT, min(self._max_height, int(current_height)))

        self.setStyleSheet(build_stylesheet(theme_name))
        self._build_ui()
        self._set_values(self._orig_width, self._orig_height, emit=False)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        hint = QLabel("調整時會即時預覽覆蓋層邊框；未鎖定時也可直接拖曳覆蓋層四邊或右下角縮放。")
        hint.setObjectName("MutedLabel")
        hint.setWordWrap(True)
        root.addWidget(hint)

        # 常用預設尺寸
        p = get_palette(self._theme_name)
        preset_style = (
            f"QPushButton {{ background-color: {p.bg_card}; color: {p.text_primary}; "
            f"border: 1px solid {p.border}; border-radius: 8px; padding: 5px 8px; "
            f"font-size: 12px; font-weight: 600; }}"
            f"QPushButton:hover {{ border-color: {p.accent_pink}; color: {p.accent_pink}; }}"
            f"QPushButton:pressed {{ background-color: {p.pill_info_bg}; }}"
        )
        preset_wrap = QHBoxLayout()
        preset_wrap.setSpacing(6)
        self.preset_buttons: list[QPushButton] = []
        for label, pw, ph in OVERLAY_SIZE_PRESETS:
            clamped_w = min(pw, self._max_width)
            clamped_h = min(ph, self._max_height)
            btn = QPushButton(f"{label}\n{clamped_w}×{clamped_h}")
            btn.setMinimumHeight(44)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(preset_style)
            btn.clicked.connect(lambda _c=False, w=clamped_w, h=clamped_h: self._set_values(w, h, emit=True))
            preset_wrap.addWidget(btn, 1)
            self.preset_buttons.append(btn)
        root.addLayout(preset_wrap)

        # 精準寬高滑桿 + 數值輸入
        form = QFormLayout()
        form.setSpacing(10)

        w_row = QHBoxLayout()
        w_row.setSpacing(8)
        self.width_slider = QSlider(Qt.Orientation.Horizontal)
        self.width_slider.setRange(OVERLAY_MIN_WIDTH, self._max_width)
        self.width_slider.setSingleStep(10)
        self.width_slider.setPageStep(40)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(OVERLAY_MIN_WIDTH, self._max_width)
        self.width_spin.setSingleStep(10)
        self.width_spin.setSuffix(" px")
        self.width_spin.setFixedWidth(104)
        self.width_slider.valueChanged.connect(self._on_width_slider)
        self.width_spin.valueChanged.connect(self._on_width_spin)
        w_row.addWidget(self.width_slider, 1)
        w_row.addWidget(self.width_spin)
        form.addRow("寬度：", w_row)

        h_row = QHBoxLayout()
        h_row.setSpacing(8)
        self.height_slider = QSlider(Qt.Orientation.Horizontal)
        self.height_slider.setRange(OVERLAY_MIN_HEIGHT, self._max_height)
        self.height_slider.setSingleStep(4)
        self.height_slider.setPageStep(16)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(OVERLAY_MIN_HEIGHT, self._max_height)
        self.height_spin.setSingleStep(4)
        self.height_spin.setSuffix(" px")
        self.height_spin.setFixedWidth(104)
        self.height_slider.valueChanged.connect(self._on_height_slider)
        self.height_spin.valueChanged.connect(self._on_height_spin)
        h_row.addWidget(self.height_slider, 1)
        h_row.addWidget(self.height_spin)
        form.addRow("高度：", h_row)

        root.addLayout(form)

        # 底部操作列
        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.reset_btn = QPushButton("恢復預設")
        self.reset_btn.setObjectName("GhostBtn")
        self.reset_btn.clicked.connect(
            lambda: self._set_values(
                min(OVERLAY_DEFAULT_WIDTH, self._max_width),
                min(OVERLAY_DEFAULT_HEIGHT, self._max_height),
                emit=True,
            )
        )
        footer.addWidget(self.reset_btn)
        footer.addStretch()

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)
        footer.addWidget(self.cancel_btn)

        self.apply_btn = QPushButton("套用")
        self.apply_btn.setObjectName("PrimaryBtn")
        self.apply_btn.setDefault(True)
        self.apply_btn.clicked.connect(self.accept)
        footer.addWidget(self.apply_btn)
        root.addLayout(footer)

    def _set_values(self, width: int, height: int, *, emit: bool = True) -> None:
        w = max(OVERLAY_MIN_WIDTH, min(self._max_width, int(width)))
        h = max(OVERLAY_MIN_HEIGHT, min(self._max_height, int(height)))
        for widget, val in (
            (self.width_slider, w),
            (self.width_spin, w),
            (self.height_slider, h),
            (self.height_spin, h),
        ):
            widget.blockSignals(True)
            widget.setValue(val)
            widget.blockSignals(False)
        if emit:
            self.size_changed.emit(w, h)

    def _on_width_slider(self, value: int) -> None:
        self.width_spin.blockSignals(True)
        self.width_spin.setValue(value)
        self.width_spin.blockSignals(False)
        self.size_changed.emit(value, self.height_spin.value())

    def _on_width_spin(self, value: int) -> None:
        self.width_slider.blockSignals(True)
        self.width_slider.setValue(value)
        self.width_slider.blockSignals(False)
        self.size_changed.emit(value, self.height_spin.value())

    def _on_height_slider(self, value: int) -> None:
        self.height_spin.blockSignals(True)
        self.height_spin.setValue(value)
        self.height_spin.blockSignals(False)
        self.size_changed.emit(self.width_spin.value(), value)

    def _on_height_spin(self, value: int) -> None:
        self.height_slider.blockSignals(True)
        self.height_slider.setValue(value)
        self.height_slider.blockSignals(False)
        self.size_changed.emit(self.width_spin.value(), value)

    def selected_size(self) -> tuple[int, int]:
        return self.width_spin.value(), self.height_spin.value()


class OverlayWindow(QWidget):
    """直接覆蓋替換遊戲對話框／mpv 字幕的沉浸式覆蓋層：右下角支援上／下一句快捷切換、可拖曳或自訂視窗大小、懸停查詞不搶焦點。"""

    mine_requested = Signal(object, object)          # (SentenceRecord, Token)
    copy_prompt_requested = Signal(object, object)   # (SentenceRecord, Token | None)
    translate_requested = Signal(object)             # SentenceRecord
    replay_requested = Signal()
    loop_requested = Signal(bool)
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
        dictionary: DictionaryEngine | None = None,
    ) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setWindowTitle(OVERLAY_WINDOW_TITLE)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("OverlayWindow { background: transparent; }")
        self._suppress_resize_save = True
        self.setMinimumSize(OVERLAY_MIN_WIDTH, OVERLAY_MIN_HEIGHT)
        self.resize(OVERLAY_DEFAULT_WIDTH, OVERLAY_DEFAULT_HEIGHT)

        self._theme_name = theme_name
        self._grammar_engine = grammar_engine
        self._dictionary = dictionary
        self._drag_offset: QPoint | None = None
        self._resize_edges: Qt.Edge = Qt.Edge(0)
        self._resize_origin_global: QPoint = QPoint()
        self._resize_origin_geom: QRect = QRect()
        self._resizing_active = False
        self._size_preview_active = False
        self._overlay_hovered = False
        self._dock_hovered = False
        self._freeflow_mode = False
        self._saved_normal_size = (OVERLAY_DEFAULT_WIDTH, OVERLAY_DEFAULT_HEIGHT)
        self._initial_saved_geom: tuple[int, int, int, int] | None = None
        self._mpv_connected = False
        self._locked = False
        self._hover_lookup = True
        self._always_on_top = True
        self._replace_mode = True
        self._backdrop = "vn_dark"
        self._text_align = "left"
        self._font_size = 23
        self._show_furigana = False
        self._translucent_opacity = 0.90
        self._guide_dismissed = False
        self._mouse_inside_popup = False
        self._show_inline_trans = False
        self._dict_preference = "bilingual"
        self._known_words: set[str] = set()
        self._current_record: SentenceRecord | None = None
        self._pinner = KWinOverlayPinner(OVERLAY_WINDOW_TITLE, POPUP_WINDOW_TITLE)

        self.popup = OverlayLookupPopup(theme_name=self._effective_token_theme(), parent=self)
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

        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._clear_toast)

        self._resize_save_timer = QTimer(self)
        self._resize_save_timer.setSingleShot(True)
        self._resize_save_timer.setInterval(380)
        self._resize_save_timer.timeout.connect(self._finish_interactive_resize)

        self._build_ui()
        self.apply_theme(theme_name)
        self._update_layout_margins()
        self._position_corner_and_toast()
        self._suppress_resize_save = False

    def _effective_token_theme(self) -> str:
        preset = BACKDROP_PRESETS.get(self._backdrop, BACKDROP_PRESETS["vn_dark"])
        return preset["token_theme"]

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(2, 2, 2, 2)

        self.container = QFrame()
        self.container.setObjectName("OverlayContainer")
        box = QVBoxLayout(self.container)
        box.setContentsMargins(16, 10, 16, 32)
        box.setSpacing(4)
        self._container_layout = box

        # === 主台詞區佔滿整個對話框（像原生遊戲文字框一樣自然，底部保留給右下角精簡控制列） ===
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
        self.scroll_area.viewport().setStyleSheet("background: transparent; border: none;")

        self.sentence_widget = InteractiveSentenceWidget(
            font_size=self._font_size,
            theme_name=self._effective_token_theme(),
        )
        self.sentence_widget.set_hover_lookup_enabled(True)
        self.sentence_widget.token_clicked.connect(self._on_token_clicked)
        self.sentence_widget.token_hovered.connect(self._on_token_hovered)
        self.sentence_widget.token_hover_left.connect(self._on_token_hover_left)
        content = QWidget()
        # 讓捲動範圍依目前寬度計算換行高度，避免用最窄單詞的寬度估算整句。
        content.setMinimumHeight(1)
        self.sentence_widget.setMinimumHeight(1)
        subtitles = QVBoxLayout(content)
        subtitles.setContentsMargins(0, 0, 0, 0)
        subtitles.setSpacing(4)
        subtitles.setAlignment(Qt.AlignmentFlag.AlignTop)
        subtitles.addWidget(self.sentence_widget)
        box.addWidget(self.scroll_area, 1)

        # === 按需行內語境譯文列（開關後出現，平時隱藏不劇透） ===
        self.inline_trans_label = QLabel("")
        self.inline_trans_label.setWordWrap(True)
        self.inline_trans_label.hide()
        subtitles.addWidget(self.inline_trans_label)
        self.scroll_area.setWidget(content)

        # === 迷你無阻膠囊列（僅在無阻收合模式顯示） ===
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
        expand_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        expand_btn.clicked.connect(self.toggle_freeflow_mode)
        cl.addWidget(expand_btn)
        cap_close = QPushButton("✕")
        cap_close.setFixedSize(22, 22)
        cap_close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        cap_close.clicked.connect(self.hide)
        cl.addWidget(cap_close)
        self.capsule_bar.hide()
        box.addWidget(self.capsule_bar)

        root.addWidget(self.container)

        # === 右下角快捷控制列（上一句 / 重播或原聲 / 下一句 / 設定） ===
        self.corner_controls = QFrame(self.container)
        self.corner_controls.setObjectName("OverlayCornerDock")
        self.corner_controls.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        dock_layout = QHBoxLayout(self.corner_controls)
        dock_layout.setContentsMargins(4, 2, 4, 2)
        dock_layout.setSpacing(2)

        def _make_dock_btn(text: str, name: str, tooltip: str, object_name: str) -> QPushButton:
            btn = QPushButton(text, self.corner_controls)
            btn.setObjectName(object_name)
            btn.setAccessibleName(name)
            btn.setToolTip(tooltip)
            btn.setFixedSize(24, 24)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.installEventFilter(self)
            return btn

        self.prev_button = _make_dock_btn("‹", "上一句", "上一句", "OverlayStepBtn")
        self.prev_button.clicked.connect(self._on_prev_clicked)
        dock_layout.addWidget(self.prev_button)

        self.replay_button = _make_dock_btn("↺", "重播本句", "試聽本句聲優原聲", "OverlayReplayBtn")
        self.replay_button.clicked.connect(self._on_replay_clicked)
        dock_layout.addWidget(self.replay_button)

        self.next_button = _make_dock_btn("›", "下一句", "下一句", "OverlayStepBtn")
        self.next_button.clicked.connect(self._on_next_clicked)
        dock_layout.addWidget(self.next_button)

        self._dock_divider = QFrame(self.corner_controls)
        self._dock_divider.setObjectName("OverlayDockDivider")
        self._dock_divider.setFixedSize(1, 14)
        dock_layout.addWidget(self._dock_divider)

        self.settings_button = _make_dock_btn("⚙", "覆蓋層設定", "覆蓋層設定與視窗大小（可右鍵開啟）", "OverlaySettingsBtn")
        self.settings_button.clicked.connect(self._open_settings)
        dock_layout.addWidget(self.settings_button)

        self.corner_controls.adjustSize()
        self.corner_controls.installEventFilter(self)
        self.corner_controls.hide()

        self.corner_mark = _CornerMark(self.container)
        self.toast_label = QLabel("", self.container)
        self.toast_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.toast_label.hide()

        self.corner_controls.raise_()
        self.corner_mark.raise_()
        self.toast_label.raise_()

        self._options_menu = self._build_options_menu()
        self._options_menu.aboutToHide.connect(self._on_options_menu_hidden)

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

        self.act_center_text = QAction("文字居中對齊", self)
        self.act_center_text.setCheckable(True)
        self.act_center_text.setChecked(self._text_align == "center")
        self.act_center_text.triggered.connect(self._toggle_center_text)
        menu.addAction(self.act_center_text)

        act_font_up = QAction("放大字體", self)
        act_font_up.triggered.connect(lambda: self._adjust_font_size(+2))
        menu.addAction(act_font_up)

        act_font_down = QAction("縮小字體", self)
        act_font_down.triggered.connect(lambda: self._adjust_font_size(-2))
        menu.addAction(act_font_down)

        self.act_resize = QAction(f"自訂視窗大小（{self.width()} × {self.height()}）…", self)
        self.act_resize.triggered.connect(self.open_size_dialog)
        menu.addAction(self.act_resize)

        self.act_hover = QAction("懸停自動查詞", self)
        self.act_hover.setCheckable(True)
        self.act_hover.setChecked(True)
        self.act_hover.triggered.connect(self._toggle_hover_lookup)
        menu.addAction(self.act_hover)

        menu.addSeparator()

        playback = menu.addMenu("播放與字幕")
        playback.addAction("試聽聲優原聲", self.audio_preview_requested.emit)
        playback.addAction("上一句", self.prev_requested.emit)
        playback.addAction("下一句", self.next_requested.emit)
        self.replay_action = playback.addAction("重播本句", self.replay_requested.emit)
        self.replay_action.setVisible(False)
        self.translation_action = playback.addAction("顯示中文譯文")
        self.translation_action.setCheckable(True)
        self.translation_action.triggered.connect(self._toggle_inline_trans)
        self.lock_action = menu.addAction("鎖定覆蓋層位置與大小")
        self.lock_action.setCheckable(True)
        self.lock_action.triggered.connect(self._toggle_lock)
        menu.addAction("隱藏覆蓋層", self.hide)

        self.loop_action = menu.addAction("循環播放本句")
        self.loop_action.setCheckable(True)
        self.loop_action.setVisible(False)
        self.loop_action.triggered.connect(self.loop_requested.emit)

        act_merge = QAction("合併上一句", self)
        act_merge.triggered.connect(self.merge_prev_requested.emit)
        menu.addAction(act_merge)

        act_freeflow = QAction("縮成無阻膠囊", self)
        act_freeflow.triggered.connect(self.toggle_freeflow_mode)
        menu.addAction(act_freeflow)

        return menu

    def _on_prev_clicked(self) -> None:
        self._popup_hide_timer.stop()
        self.popup.hide()
        self.prev_requested.emit()

    def _on_next_clicked(self) -> None:
        self._popup_hide_timer.stop()
        self.popup.hide()
        self.next_requested.emit()

    def _on_replay_clicked(self) -> None:
        self._popup_hide_timer.stop()
        self.popup.hide()
        if self._uses_mpv_transport():
            self.replay_requested.emit()
        else:
            self.audio_preview_requested.emit()

    def _uses_mpv_transport(self) -> bool:
        return self._mpv_connected and (
            self._current_record is None or self._current_record.source in ("mpv", "demo")
        )

    def _open_settings(self) -> None:
        self._show_options(self.settings_button.mapToGlobal(self.settings_button.rect().bottomLeft()))

    def _show_options(self, pos: QPoint) -> None:
        self._popup_hide_timer.stop()
        self.popup.hide()
        self._update_size_action_label()
        self._options_menu.popup(pos)
        self._sync_corner_controls_visibility()
        self._refresh_dock_style()

    def _on_options_menu_hidden(self) -> None:
        inside = self.underMouse()
        if not inside:
            self._overlay_hovered = False
            self._dock_hovered = False
        else:
            self._dock_hovered = self.corner_controls.underMouse()
        self._sync_corner_controls_visibility()
        self._refresh_dock_style()

    def set_text_align(
        self,
        align: str,
        *,
        save: bool = True,
        toast: bool = False,
    ) -> None:
        """設定覆蓋層字幕與行內譯文的水平對齊方式（left / center）。"""
        normalized = "center" if align == "center" else "left"
        self._text_align = normalized
        if hasattr(self, "act_center_text"):
            self.act_center_text.blockSignals(True)
            self.act_center_text.setChecked(normalized == "center")
            self.act_center_text.blockSignals(False)
        self.sentence_widget.set_text_align(normalized)
        self.inline_trans_label.setAlignment(
            (Qt.AlignmentFlag.AlignHCenter if normalized == "center" else Qt.AlignmentFlag.AlignLeft)
            | Qt.AlignmentFlag.AlignVCenter
        )
        self._update_layout_margins()
        if toast:
            self.show_toast("文字對齊：居中" if normalized == "center" else "文字對齊：左對齊", 1600)
        if save:
            self._emit_state()

    def _toggle_center_text(self, checked: bool) -> None:
        self.set_text_align("center" if checked else "left", save=True, toast=True)

    def _update_size_action_label(self) -> None:
        w, h = self._saved_normal_size if self._freeflow_mode else (self.width(), self.height())
        if hasattr(self, "act_resize"):
            self.act_resize.setText(f"自訂視窗大小（{w} × {h}）…")

    def set_overlay_size(
        self,
        width: int,
        height: int,
        *,
        save: bool = True,
        toast: bool = False,
    ) -> None:
        """以像素精準設定覆蓋層視窗尺寸，並同步更新持久化設定。"""
        w = max(OVERLAY_MIN_WIDTH, min(OVERLAY_MAX_WIDTH, int(width)))
        h = max(OVERLAY_MIN_HEIGHT, min(OVERLAY_MAX_HEIGHT, int(height)))
        self._saved_normal_size = (w, h)
        if not self._freeflow_mode:
            prev_suppress = self._suppress_resize_save
            self._suppress_resize_save = True
            try:
                self.resize(w, h)
            finally:
                self._suppress_resize_save = prev_suppress
        self._update_size_action_label()
        if toast:
            self.show_toast(f"視窗大小：{w} × {h}", 1800)
        if save:
            self._emit_state()

    def open_size_dialog(self) -> None:
        """開啟自訂覆蓋層視窗大小對話框（即時預覽，取消可還原）。"""
        self._popup_hide_timer.stop()
        self.popup.hide()
        if self._freeflow_mode:
            self.toggle_freeflow_mode()

        orig_w, orig_h = self.width(), self.height()
        screen = self.screen() or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else QRect(0, 0, 1920, 1080)
        max_w = max(OVERLAY_MIN_WIDTH, min(OVERLAY_MAX_WIDTH, avail.width()))
        max_h = max(OVERLAY_MIN_HEIGHT, min(OVERLAY_MAX_HEIGHT, max(520, avail.height() // 2)))

        dlg = OverlaySizeDialog(
            orig_w,
            orig_h,
            theme_name=self._theme_name,
            max_width=max_w,
            max_height=max_h,
            parent=None,
        )
        dlg.size_changed.connect(
            lambda w, h: self.set_overlay_size(w, h, save=False, toast=False)
        )

        # 將調整視窗放在覆蓋層上方（空間不足則放下方），避免擋住即時預覽
        dlg.adjustSize()
        ov_rect = QRect(self.mapToGlobal(QPoint()), self.size())
        dx = max(avail.left() + 12, min(ov_rect.center().x() - dlg.width() // 2, avail.right() - dlg.width() - 12))
        if ov_rect.top() - dlg.height() - 14 >= avail.top():
            dy = ov_rect.top() - dlg.height() - 14
        else:
            dy = min(avail.bottom() - dlg.height() - 12, ov_rect.bottom() + 14)
        dlg.move(dx, dy)

        self._size_preview_active = True
        self._update_container_style()
        try:
            if dlg.exec() == QDialog.DialogCode.Accepted:
                final_w, final_h = dlg.selected_size()
                self.set_overlay_size(final_w, final_h, save=True, toast=True)
            else:
                self.set_overlay_size(orig_w, orig_h, save=False, toast=False)
        finally:
            self._size_preview_active = False
            self._update_container_style()
            dlg.deleteLater()

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        self._show_options(event.globalPos())
        event.accept()

    def set_mpv_connected(self, connected: bool) -> None:
        self._mpv_connected = connected
        self._update_transport_visibility()

    def _update_transport_visibility(self) -> None:
        enabled = self._uses_mpv_transport()
        self.replay_action.setVisible(enabled)
        self.loop_action.setVisible(enabled)
        if hasattr(self, "replay_button"):
            self.replay_button.setToolTip("重播本句" if enabled else "試聽本句聲優原聲")
            self.prev_button.setToolTip("上一條字幕" if enabled else "上一句台詞")
            self.next_button.setToolTip("下一條字幕" if enabled else "下一句台詞")

    def _update_layout_margins(self) -> None:
        if not hasattr(self, "_container_layout"):
            return
        if self._freeflow_mode:
            self._container_layout.setContentsMargins(12, 6, 12, 6)
            return
        h = self.height()
        if h >= 92:
            self._container_layout.setContentsMargins(16, 10, 16, 36)
        elif h >= 82:
            self._container_layout.setContentsMargins(16, 6, 16, 32)
        elif self._text_align == "center":
            self._container_layout.setContentsMargins(14, 6, 14, 6)
        else:
            dock_w = self.corner_controls.sizeHint().width() if hasattr(self, "corner_controls") else 118
            right_pad = min(dock_w + 20, max(44, self.width() // 2))
            self._container_layout.setContentsMargins(14, 6, right_pad, 6)

    def _position_corner_and_toast(self) -> None:
        dock_size = self.corner_controls.sizeHint()
        self.corner_controls.resize(dock_size)
        dock_w = dock_size.width()
        dock_h = dock_size.height()
        self.corner_controls.move(
            max(4, self.container.width() - dock_w - 12),
            max(2, self.container.height() - dock_h - 4),
        )
        self.corner_mark.move(
            self.container.width() - self.corner_mark.width() - 1,
            self.container.height() - self.corner_mark.height() - 1,
        )
        self.toast_label.adjustSize()
        self.toast_label.move(12, max(4, self.container.height() - self.toast_label.height() - 6))
        self.corner_controls.raise_()
        self.corner_mark.raise_()
        self.toast_label.raise_()

    def _sync_corner_controls_visibility(self) -> None:
        if not hasattr(self, "corner_controls"):
            return
        should_show = (not self._freeflow_mode) and (
            self._overlay_hovered or self._dock_hovered or self._options_menu.isVisible()
        )
        if self.corner_controls.isVisible() != should_show:
            self.corner_controls.setVisible(should_show)
            if should_show:
                self._position_corner_and_toast()

    def _refresh_dock_style(self) -> None:
        if not hasattr(self, "corner_controls"):
            return
        self._sync_corner_controls_visibility()
        eff_theme = self._effective_token_theme()
        p = get_palette(eff_theme)
        is_dark = eff_theme == "starlight"
        dock_active = self._dock_hovered or self._options_menu.isVisible()

        if dock_active:
            dock_bg = "rgba(15, 23, 42, 0.90)" if is_dark else "rgba(255, 250, 253, 0.96)"
            dock_border = "rgba(244, 114, 182, 0.48)" if is_dark else "rgba(236, 72, 153, 0.45)"
            btn_fg = p.text_primary
            div_bg = "rgba(148, 163, 184, 0.28)" if is_dark else "rgba(148, 163, 184, 0.35)"
        else:
            dock_bg = "rgba(15, 23, 42, 0.62)" if is_dark else "rgba(255, 250, 253, 0.82)"
            dock_border = "rgba(148, 163, 184, 0.24)" if is_dark else "rgba(236, 72, 153, 0.24)"
            btn_fg = "rgba(241, 245, 249, 0.84)" if is_dark else "rgba(51, 65, 85, 0.84)"
            div_bg = "rgba(148, 163, 184, 0.22)" if is_dark else "rgba(148, 163, 184, 0.26)"

        hover_bg = "rgba(244, 114, 182, 0.24)" if is_dark else "rgba(236, 72, 153, 0.14)"
        pressed_bg = "rgba(244, 114, 182, 0.38)" if is_dark else "rgba(236, 72, 153, 0.26)"

        self.corner_controls.setStyleSheet(
            f"""
            QFrame#OverlayCornerDock {{
                background-color: {dock_bg};
                border: 1px solid {dock_border};
                border-radius: 10px;
            }}
            QFrame#OverlayDockDivider {{
                background-color: {div_bg};
                border: none;
            }}
            QFrame#OverlayCornerDock QPushButton {{
                background: transparent;
                color: {btn_fg};
                border: none;
                border-radius: 6px;
                padding: 0px;
                font-weight: 700;
            }}
            QFrame#OverlayCornerDock QPushButton#OverlayStepBtn {{
                font-size: 20px;
                padding-bottom: 2px;
            }}
            QFrame#OverlayCornerDock QPushButton#OverlayReplayBtn {{
                font-size: 14px;
            }}
            QFrame#OverlayCornerDock QPushButton#OverlaySettingsBtn {{
                font-size: 14px;
            }}
            QFrame#OverlayCornerDock QPushButton:hover {{
                background-color: {hover_bg};
                color: {p.accent_pink};
            }}
            QFrame#OverlayCornerDock QPushButton:pressed {{
                background-color: {pressed_bg};
                color: {p.text_primary};
            }}
            """
        )

    def apply_theme(self, theme_name: str) -> None:
        self._theme_name = theme_name
        eff_theme = self._effective_token_theme()
        p = get_palette(eff_theme)
        self._update_container_style()
        self._refresh_dock_style()
        self.corner_mark.set_highlight_color(p.accent_pink)
        self.corner_mark.setStyleSheet(
            f"color: {p.text_muted}; background: transparent; border: none;"
        )
        self.capsule_label.setStyleSheet(
            f"font-size: 12px; color: {p.accent_pink}; font-weight: 700; border: none; background: transparent;"
        )
        toast_bg = "rgba(15, 23, 42, 0.78)" if eff_theme == "starlight" else "rgba(255, 250, 253, 0.92)"
        self.toast_label.setStyleSheet(
            f"font-size: 11px; color: {p.accent_mint}; font-weight: 600; "
            f"background: {toast_bg}; border-radius: 6px; padding: 2px 7px; border: none;"
        )
        self.inline_trans_label.setStyleSheet(
            f"font-size: 13.5px; color: {p.text_secondary}; background: transparent; padding: 2px 4px;"
        )
        is_transparent = self._backdrop == "transparent"
        self.sentence_widget.set_display_options(
            self._font_size,
            self._show_furigana,
            theme_name=eff_theme,
            cinema_shadow=is_transparent,
        )
        self.popup.set_theme_name(eff_theme)

    def _update_container_style(self) -> None:
        preset = BACKDROP_PRESETS.get(self._backdrop, BACKDROP_PRESETS["vn_dark"])
        eff_theme = preset["token_theme"]
        p = get_palette(eff_theme)

        if self._backdrop == "transparent":
            self.setWindowOpacity(1.0)
            bg = "transparent"
            radius = 10
        elif self._replace_mode and not self._freeflow_mode:
            # 遮蓋替換模式：100% 實色遮住遊戲原字，絕不雙字幕重影！
            self.setWindowOpacity(1.0)
            bg = preset["opaque_bg"]
            radius = 10
        else:
            self.setWindowOpacity(max(0.45, min(1.0, self._translucent_opacity)))
            bg = preset["opaque_bg"] if self._freeflow_mode else preset["translucent_bg"]
            radius = 18 if self._freeflow_mode else 12

        btn_bg = "rgba(255, 255, 255, 0.09)" if eff_theme == "starlight" else "rgba(255, 255, 255, 0.90)"

        if self._resizing_active or self._size_preview_active:
            border_rule = f"1px dashed {p.accent_pink}"
        elif self._freeflow_mode:
            border_rule = f"1px solid {preset['active_border']}"
        else:
            border_rule = "1px solid transparent"

        self.container.setStyleSheet(
            f"""
            QFrame#OverlayContainer {{
                background-color: {bg};
                border: {border_rule};
                border-radius: {radius}px;
            }}
            QWidget#OverlayToolbar QPushButton {{
                background-color: {btn_bg};
                color: {p.text_primary};
                border: 1px solid {p.border};
                border-radius: 8px;
                padding: 0px 5px;
                font-size: 11px;
                font-weight: 600;
            }}
            QWidget#OverlayToolbar QPushButton:hover {{
                border-color: {p.accent_pink};
                color: {p.accent_pink};
            }}
            """
        )

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
        text_align: str = "left",
    ) -> None:
        w = max(OVERLAY_MIN_WIDTH, min(OVERLAY_MAX_WIDTH, int(width or OVERLAY_DEFAULT_WIDTH)))
        h = max(OVERLAY_MIN_HEIGHT, min(OVERLAY_MAX_HEIGHT, int(height or OVERLAY_DEFAULT_HEIGHT)))
        self._saved_normal_size = (w, h)
        prev_suppress = self._suppress_resize_save
        self._suppress_resize_save = True
        try:
            self.resize(w, h)
        finally:
            self._suppress_resize_save = prev_suppress
        self._update_size_action_label()
        if (x, y) != (-1, -1) and any(screen.geometry().contains(x, y) for screen in QGuiApplication.screens()):
            self._initial_saved_geom = (int(x), int(y), w, h)
            self.move(x, y)

        self._locked = bool(locked)
        self.lock_action.setChecked(self._locked)

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

        self.set_text_align(text_align, save=False, toast=False)

        self._font_size = max(15, min(38, int(font_size or 23)))
        self._guide_dismissed = bool(guide_dismissed)
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
                "text_align": self._text_align,
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
        """在目前的視窗範圍內更新字幕。"""
        self._current_record = record
        self._update_transport_visibility()
        self.sentence_widget.set_sentence(record.tokenized)
        self.scroll_area.verticalScrollBar().setValue(0)
        self.popup.hide()

        if self._show_inline_trans and (record.pack_translation or record.sentence_translation):
            self.inline_trans_label.setText(record.pack_translation or record.sentence_translation)
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
        self._update_container_style()
        self.show_toast("遮蓋模式" if checked else "輕透模式")
        self.replace_mode_changed.emit(checked)
        self._emit_state()

    def set_backdrop(self, backdrop: str, save: bool = True) -> None:
        if backdrop not in BACKDROP_PRESETS:
            return
        self._backdrop = backdrop
        self.act_backdrop.setText(f"切換底色（{BACKDROP_PRESETS[self._backdrop]['label']}）")
        self.apply_theme(self._theme_name)
        if save:
            self._emit_state()

    def _cycle_backdrop(self) -> None:
        idx = BACKDROP_ORDER.index(self._backdrop) if self._backdrop in BACKDROP_ORDER else 0
        self.set_backdrop(BACKDROP_ORDER[(idx + 1) % len(BACKDROP_ORDER)], save=True)
        self.show_toast(f"底色：{BACKDROP_PRESETS[self._backdrop]['label']}")

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
        self.lock_action.setChecked(self._locked)
        if self._locked:
            self._resize_edges = Qt.Edge(0)
            self._resizing_active = False
            self.unsetCursor()
            self.corner_mark.set_highlighted(False)
            self._update_container_style()
        if checked and not self._guide_dismissed:
            self._guide_dismissed = True
        self.show_toast("已鎖定位置與大小" if checked else "已解鎖，可拖曳移動或縮放")
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
        self.translation_action.setChecked(checked)
        if not checked:
            self.inline_trans_label.hide()
            return
        if self._current_record is None:
            return
        if self._current_record.pack_translation or self._current_record.sentence_translation:
            self.inline_trans_label.setText(self._current_record.pack_translation or self._current_record.sentence_translation)
            self.inline_trans_label.show()
        else:
            self.inline_trans_label.setText("取得譯文中…")
            self.inline_trans_label.show()
            self.translate_requested.emit(self._current_record)

    def toggle_freeflow_mode(self) -> None:
        """無阻沉浸：縮成迷你角落膠囊；展開：恢復原字幕框大小。"""
        prev_suppress = self._suppress_resize_save
        self._suppress_resize_save = True
        try:
            if not self._freeflow_mode:
                self._saved_normal_size = (self.width(), self.height())
                self._freeflow_mode = True
                self._resize_edges = Qt.Edge(0)
                self._resizing_active = False
                self.unsetCursor()
                self.corner_mark.set_highlighted(False)
                self.corner_controls.hide()
                self.corner_mark.hide()
                self.scroll_area.hide()
                self.inline_trans_label.hide()
                self.popup.hide()
                self.capsule_bar.show()
                self.setMinimumSize(210, 38)
                self.resize(230, 42)
            else:
                target_w, target_h = self._saved_normal_size
                self.capsule_bar.hide()
                self.setMinimumSize(OVERLAY_MIN_WIDTH, OVERLAY_MIN_HEIGHT)
                self.scroll_area.show()
                self.resize(target_w, target_h)
                self._freeflow_mode = False
                self._sync_corner_controls_visibility()
                self.corner_mark.show()
                if self._current_record is not None:
                    self.show_record(self._current_record)
        finally:
            self._suppress_resize_save = prev_suppress
        self._update_layout_margins()
        self._update_container_style()
        self._position_corner_and_toast()

    def show_toast(self, message: str, ms: int = 3000) -> None:
        self.toast_label.setText(message)
        if not self._freeflow_mode:
            self.toast_label.show()
            self._position_corner_and_toast()
        self._toast_timer.start(ms)

    def _clear_toast(self) -> None:
        self.toast_label.hide()
        self.toast_label.clear()

    def _position_and_show_popup(self, token: Token, anchor_top: QPoint) -> None:
        record = self._current_record
        phrase_tok: Token | None = None
        if record is not None and self._dictionary is not None:
            pm = find_phrase_for_token(record.raw_text, record.tokenized.tokens, token, self._dictionary)
            if pm is not None:
                phrase_tok = build_phrase_token(pm)
        active_for_known = phrase_tok if phrase_tok is not None else token
        already_known = active_for_known.lemma in self._known_words or active_for_known.surface in self._known_words
        particle_info = (
            self._grammar_engine.get_particle_info(token.surface)
            if self._grammar_engine is not None
            else None
        )
        if self._options_menu.isVisible():
            return
        self.popup.hide()
        self._mouse_inside_popup = False
        self.popup.reset_lookup_area()
        self.popup.popup_for_token(
            token,
            self._dict_preference,
            already_known,
            particle_info,
            record.raw_text if record else "",
            (record.pack_translation or record.sentence_translation) if record else "",
            show=False,
            phrase_token=phrase_tok,
        )
        if QGuiApplication.platformName().startswith("wayland"):
            self._show_wayland_lookup(anchor_top)
            return

        screen = QGuiApplication.screenAt(anchor_top) or self.screen()
        subtitles = QRect(self.mapToGlobal(QPoint()), self.size())
        rect = _lookup_popup_geometry(screen.availableGeometry(), subtitles, self.popup.size(), anchor_top.x())
        if not rect.isValid():
            self.popup.hide()
            return
        self.popup.fit_to_lookup_area(rect.size())
        rect = _lookup_popup_geometry(screen.availableGeometry(), subtitles, self.popup.size(), anchor_top.x())
        if not rect.isValid() or rect.size() != self.popup.size():
            self.popup.hide()
            return
        self.popup.move(rect.topLeft())
        self.popup.show()

    def _show_wayland_lookup(self, anchor_top: QPoint) -> None:
        # Wayland 頂層視窗的全域座標可能是合成值。用 xdg_popup 錨定父視窗的整塊字幕。
        area = self.screen().availableGeometry()
        max_height = (area.height() - self.height() - 16) // 2
        if max_height < min(self.popup.height(), 132) or area.width() <= 16:
            return
        self.popup.fit_to_lookup_area(QSize(min(self.popup.width(), area.width() - 16),
                                            min(self.popup.height(), max_height)))
        relative_x = self.mapFromGlobal(anchor_top).x()
        window = self.popup.windowHandle()
        if window is None:
            # winId() 會連帶替字幕容器建立原生子視窗；浮窗只需要自己的 QWindow。
            self.popup.create()
            window = self.popup.windowHandle()
        if window is None:
            return
        window.setTransientParent(self.windowHandle())
        window.setProperty("_q_waylandPopupAnchorRect", _wayland_lookup_anchor(self.size(), relative_x))
        window.setProperty("_q_waylandPopupGravity", (Qt.Edge.TopEdge | Qt.Edge.RightEdge).value)
        # xdg_positioner slide_x + flip_y：允許左右平移，垂直方向只准整塊翻到字幕下方。
        window.setProperty("_q_waylandPopupConstraintAdjustment", 1 | 8)
        self.popup.show()

    def _on_token_clicked(self, token: Token, anchor_top: QPoint) -> None:
        self._popup_hide_timer.stop()
        self._position_and_show_popup(token, anchor_top)

    def _on_token_hovered(self, token: Token, anchor_top: QPoint) -> None:
        if QApplication.mouseButtons() & Qt.MouseButton.LeftButton:
            return
        if self._dock_hovered or self._resizing_active:
            return
        self._popup_hide_timer.stop()
        self._position_and_show_popup(token, anchor_top)

    def _on_token_hover_left(self) -> None:
        if self._hover_lookup:
            self._popup_hide_timer.start()

    def _on_popup_hover_enter(self) -> None:
        self._mouse_inside_popup = True
        self._popup_hide_timer.stop()

    def _on_popup_hover_leave(self) -> None:
        self._mouse_inside_popup = False
        self._popup_hide_timer.start()

    def _maybe_hide_popup(self) -> None:
        if not self._mouse_inside_popup:
            self.popup.hide()

    def _on_popup_mine(self, token: Token) -> None:
        if self._current_record is not None:
            self.mine_requested.emit(self._current_record, token)

    def _on_popup_copy_prompt(self, token: Token | None) -> None:
        if self._current_record is not None:
            self.copy_prompt_requested.emit(self._current_record, token)

    def _set_hover_flags(
        self,
        *,
        overlay_hovered: bool | None = None,
        dock_hovered: bool | None = None,
    ) -> None:
        changed = False
        if overlay_hovered is not None and self._overlay_hovered != overlay_hovered:
            self._overlay_hovered = overlay_hovered
            changed = True
        if dock_hovered is not None and self._dock_hovered != dock_hovered:
            self._dock_hovered = dock_hovered
            changed = True
        if changed:
            self._refresh_dock_style()

    def _resize_edges_at(self, pos: QPoint) -> Qt.Edge:
        """偵測游標是否位於覆蓋層可拖曳縮放的四邊或右下角縮放熱區。"""
        if self._locked or self._freeflow_mode:
            return Qt.Edge(0)
        if self.corner_controls.isVisible():
            dock_rect = QRect(
                self.corner_controls.mapTo(self, QPoint()),
                self.corner_controls.size(),
            )
            if dock_rect.contains(pos):
                return Qt.Edge(0)

        w, h = self.width(), self.height()
        corner_m = 14
        if pos.x() >= w - corner_m and pos.y() >= h - corner_m:
            return Qt.Edge.RightEdge | Qt.Edge.BottomEdge

        edge_m = 6
        edges = Qt.Edge(0)
        if pos.x() < edge_m:
            edges |= Qt.Edge.LeftEdge
        elif pos.x() >= w - edge_m:
            edges |= Qt.Edge.RightEdge
        if pos.y() < edge_m:
            edges |= Qt.Edge.TopEdge
        elif pos.y() >= h - edge_m:
            edges |= Qt.Edge.BottomEdge
        return edges

    @staticmethod
    def _cursor_for_edges(edges: Qt.Edge) -> Qt.CursorShape | None:
        if edges in (
            Qt.Edge.LeftEdge | Qt.Edge.TopEdge,
            Qt.Edge.RightEdge | Qt.Edge.BottomEdge,
        ):
            return Qt.CursorShape.SizeFDiagCursor
        if edges in (
            Qt.Edge.RightEdge | Qt.Edge.TopEdge,
            Qt.Edge.LeftEdge | Qt.Edge.BottomEdge,
        ):
            return Qt.CursorShape.SizeBDiagCursor
        if edges in (Qt.Edge.LeftEdge, Qt.Edge.RightEdge):
            return Qt.CursorShape.SizeHorCursor
        if edges in (Qt.Edge.TopEdge, Qt.Edge.BottomEdge):
            return Qt.CursorShape.SizeVerCursor
        return None

    def _update_hover_state(self, pos: QPoint) -> None:
        dock_hit = False
        if not self._freeflow_mode:
            dock_rect = QRect(
                self.corner_controls.mapTo(self, QPoint()),
                self.corner_controls.size(),
            )
            dock_hit = dock_rect.contains(pos)
        self._set_hover_flags(overlay_hovered=True, dock_hovered=dock_hit)

        if self._resize_edges != Qt.Edge(0):
            return
        edges = self._resize_edges_at(pos)
        shape = self._cursor_for_edges(edges)
        if shape is not None:
            self.setCursor(shape)
        else:
            self.unsetCursor()
        self.corner_mark.set_highlighted(edges == (Qt.Edge.RightEdge | Qt.Edge.BottomEdge))

    def _clear_hover_state(self) -> None:
        self._set_hover_flags(overlay_hovered=False, dock_hovered=False)
        if self._resize_edges == Qt.Edge(0):
            self.unsetCursor()
            if not self._resizing_active:
                self.corner_mark.set_highlighted(False)

    def _apply_manual_resize(self, global_pos: QPoint) -> None:
        dx = global_pos.x() - self._resize_origin_global.x()
        dy = global_pos.y() - self._resize_origin_global.y()
        orig = self._resize_origin_geom
        left, top, right, bottom = orig.left(), orig.top(), orig.right(), orig.bottom()

        if self._resize_edges & Qt.Edge.LeftEdge:
            new_w = max(OVERLAY_MIN_WIDTH, min(OVERLAY_MAX_WIDTH, orig.width() - dx))
            left = right - new_w + 1
        elif self._resize_edges & Qt.Edge.RightEdge:
            new_w = max(OVERLAY_MIN_WIDTH, min(OVERLAY_MAX_WIDTH, orig.width() + dx))
            right = left + new_w - 1

        if self._resize_edges & Qt.Edge.TopEdge:
            new_h = max(OVERLAY_MIN_HEIGHT, min(OVERLAY_MAX_HEIGHT, orig.height() - dy))
            top = bottom - new_h + 1
        elif self._resize_edges & Qt.Edge.BottomEdge:
            new_h = max(OVERLAY_MIN_HEIGHT, min(OVERLAY_MAX_HEIGHT, orig.height() + dy))
            bottom = top + new_h - 1

        self.setGeometry(QRect(QPoint(left, top), QPoint(right, bottom)))

    def _finish_interactive_resize(self) -> None:
        self._resize_save_timer.stop()
        was_resizing = self._resizing_active
        self._resizing_active = False
        if was_resizing:
            self.corner_mark.set_highlighted(False)
            self._update_container_style()
        if not self._freeflow_mode:
            self._saved_normal_size = (self.width(), self.height())
            self._update_size_action_label()
            self._emit_state()

    def eventFilter(self, watched, ev: QEvent) -> bool:  # noqa: N802
        if hasattr(self, "corner_controls") and (
            watched is self.corner_controls or watched.parent() is self.corner_controls
        ):
            etype = ev.type()
            if etype in (QEvent.Type.Enter, QEvent.Type.HoverEnter):
                self._popup_hide_timer.stop()
                self.popup.hide()
                self.unsetCursor()
                self.corner_mark.set_highlighted(False)
                self._set_hover_flags(overlay_hovered=True, dock_hovered=True)
            elif etype in (QEvent.Type.Leave, QEvent.Type.HoverLeave) and watched is self.corner_controls:
                self._set_hover_flags(dock_hovered=False)
        return super().eventFilter(watched, ev)

    def event(self, ev: QEvent) -> bool:  # noqa: N802
        etype = ev.type()
        if etype in (QEvent.Type.HoverEnter, QEvent.Type.HoverMove):
            self._update_hover_state(ev.position().toPoint())
        elif etype == QEvent.Type.HoverLeave:
            self._clear_hover_state()
        return super().event(ev)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._set_hover_flags(overlay_hovered=True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._clear_hover_state()
        self._popup_hide_timer.start()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        # 透明像素需要覆寫上一幀；子元件稍後才繪製本幀的字幕與圓角。
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(event.rect(), Qt.GlobalColor.transparent)
        painter.end()

    def moveEvent(self, event) -> None:  # noqa: N802
        # 拖曳時舊錨點已失效。下一次懸停依新位置重新計算。
        if hasattr(self, "popup"):
            self.popup.hide()
            self._mouse_inside_popup = False
        super().moveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self._locked:
            self.popup.hide()
            edges = self._resize_edges_at(event.position().toPoint())
            if edges != Qt.Edge(0):
                self._resizing_active = True
                self.corner_mark.set_highlighted(edges == (Qt.Edge.RightEdge | Qt.Edge.BottomEdge))
                self._update_container_style()
                self.show_toast(f"{self.width()} × {self.height()}", 1500)
                win = self.windowHandle()
                if win is not None and win.startSystemResize(edges):
                    self._resize_save_timer.start()
                    event.accept()
                    return
                self._resize_edges = edges
                self._resize_origin_global = event.globalPosition().toPoint()
                self._resize_origin_geom = self.geometry()
                event.accept()
                return

            win = self.windowHandle()
            if win is not None and win.startSystemMove():
                event.accept()
                return
            if not QGuiApplication.platformName().startswith("wayland"):
                self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._resize_edges != Qt.Edge(0) and not self._locked:
            self._apply_manual_resize(event.globalPosition().toPoint())
            event.accept()
            return
        if self._drag_offset is not None and not self._locked:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._resize_edges != Qt.Edge(0) and event.button() == Qt.MouseButton.LeftButton:
            self._resize_edges = Qt.Edge(0)
            self._finish_interactive_resize()
            event.accept()
            return
        if self._drag_offset is not None and event.button() == Qt.MouseButton.LeftButton:
            offset, self._drag_offset = self._drag_offset, None
            if not self._locked:
                self.move(event.globalPosition().toPoint() - offset)
            self._emit_state()
        super().mouseReleaseEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if not self._freeflow_mode:
            self._saved_normal_size = (self.width(), self.height())
            self._update_layout_margins()
            self._position_corner_and_toast()
            self._update_size_action_label()
            if self._resizing_active:
                self.show_toast(f"{self.width()} × {self.height()}", 1500)
                if self._resize_edges == Qt.Edge(0):
                    self._resize_save_timer.start()
            elif not self._suppress_resize_save and not self._size_preview_active and self.isVisible():
                self._resize_save_timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._resize_save_timer.stop()
        self._drag_offset = None
        self._resize_edges = Qt.Edge(0)
        self._resizing_active = False
        self._overlay_hovered = False
        self._dock_hovered = False
        self.corner_controls.hide()
        self.popup.hide()
        self._emit_state()
        self.visibility_changed.emit(False)
        super().hideEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._update_layout_margins()
        self._position_corner_and_toast()
        self._overlay_hovered = self.underMouse()
        self._dock_hovered = False
        self._sync_corner_controls_visibility()
        self._refresh_dock_style()
        self._pinner.ensure_active(initial_geometry=self._initial_saved_geom)
        self._initial_saved_geom = None
        self.visibility_changed.emit(True)
