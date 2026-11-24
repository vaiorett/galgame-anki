"""輕量二次元查詞詳情面板與 Yomitan 風格懸停浮動查詞彈窗（語境詞義、多候選切換、助詞骨架、原聲試聽與精煉製卡）。"""

from __future__ import annotations

import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from galgame_anki.core.context_meaning import format_contextual_meaning
from galgame_anki.core.dictionary import DictEntry, LookupMatch
from galgame_anki.core.grammar_engine import (
    ParticleInfo,
    SentenceGrammarAnalysis,
    explain_pos_friendly,
)
from galgame_anki.core.tokenizer import Token
from galgame_anki.ui.theme import get_palette


def _frequency_hint(freq: int | None) -> str:
    if freq is None:
        return ""
    if freq <= 5000:
        return f"#{freq}（🔥高頻必收）"
    if freq <= 15000:
        return f"#{freq}（✨常用詞）"
    return f"#{freq}（📖冷門詞看懂即可）"


def _format_particle_card_html(particle: ParticleInfo, theme_name: str) -> str:
    p = get_palette(theme_name)
    return (
        f'<div style="margin-bottom:8px; padding:8px 11px; background:{p.pill_ok_bg}; '
        f'border-left:3.5px solid {p.accent_pink}; border-radius:9px; color:{p.text_primary};">'
        f'<div style="font-size:12px; font-weight:700; color:{p.accent_pink}; margin-bottom:2px;">'
        f'🔗 核心助詞 [{html.escape(particle.level)}] — {html.escape(particle.role)}</div>'
        f'<div style="font-size:12px; color:{p.text_muted}; margin-bottom:3px;">接續公式：{html.escape(particle.formula)}</div>'
        f'<div style="font-size:14px; line-height:1.6;">{html.escape(particle.meaning)}</div>'
        f'</div>'
    )


def _format_context_banner_html(ctx_meaning: str, theme_name: str) -> str:
    p = get_palette(theme_name)
    return (
        f'<div style="margin-bottom:8px; padding:8px 11px; background:{p.pill_ok_bg}; '
        f'border-left:3.5px solid {p.accent_pink}; border-radius:9px; color:{p.text_primary};">'
        f'<div style="font-size:11px; font-weight:700; color:{p.accent_pink}; margin-bottom:2px;">🎯 本句語境意思（非死搬原形詞典）</div>'
        f'<div style="font-size:15px; font-weight:600; line-height:1.55;">{html.escape(ctx_meaning)}</div>'
        f'</div>'
    )


def _format_entries_html(
    entries: list[DictEntry],
    empty_hint: str,
    theme_name: str,
    particle: ParticleInfo | None = None,
    context_meaning: str = "",
) -> str:
    p = get_palette(theme_name)
    blocks: list[str] = []
    if particle is not None:
        blocks.append(_format_particle_card_html(particle, theme_name))
    elif context_meaning:
        blocks.append(_format_context_banner_html(context_meaning, theme_name))

    if not entries:
        if not blocks:
            return f'<p style="color:{p.text_muted}; margin:4px 0;">{html.escape(empty_hint)}</p>'
        return "".join(blocks)

    for entry in entries[:6]:
        dict_tag = html.escape(entry.dict_name)
        friendly_pos = explain_pos_friendly(entry.pos)
        pos_display = friendly_pos or entry.pos
        pos_tag = (
            f' <span style="color:{p.accent_lilac};">〔{html.escape(pos_display)}〕</span>'
            if pos_display
            else ""
        )
        gloss_lines = "<br>".join(
            html.escape(g).replace("\n", "<br>") for g in entry.glosses[:5]
        )
        blocks.append(
            f'<div style="margin-bottom:8px; padding:8px 10px; background:{p.bg_input}; '
            f'border:1px solid {p.border}; border-radius:10px;">'
            f'<div style="font-size:11px; color:{p.accent_pink}; margin-bottom:3px; font-weight:600;">{dict_tag}{pos_tag}</div>'
            f'<div style="font-size:14px; line-height:1.65; color:{p.text_primary};">{gloss_lines}</div>'
            f"</div>"
        )
    return "".join(blocks)


class LookupDetailPanel(QFrame):
    """嵌入輕量主視窗下方的即時查詞 + 語境詞義 + 多候選切換 + 一鍵精煉製卡面板。"""

    mine_requested = Signal(object)         # Token
    copy_prompt_requested = Signal(object)  # Token | None
    audio_preview_requested = Signal()

    def __init__(self, theme_name: str = "sakura", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CardPanel")
        self._theme_name = theme_name
        self._current_token: Token | None = None
        self._current_analysis: SentenceGrammarAnalysis | None = None
        self._last_args: tuple | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        # 單行標題＋動作列
        top = QHBoxLayout()
        top.setSpacing(8)

        self.word_label = QLabel("點擊上方台詞中的單詞或助詞")
        self.word_label.setStyleSheet("font-size: 20px; font-weight: 700; background: transparent;")
        top.addWidget(self.word_label)

        self.reading_label = QLabel("")
        self.reading_label.setStyleSheet("font-size: 14px; background: transparent;")
        top.addWidget(self.reading_label)

        self.meta_label = QLabel("")
        self.meta_label.setObjectName("MutedLabel")
        top.addWidget(self.meta_label)
        top.addStretch()

        self.audio_btn = QPushButton("🔊 試聽")
        self.audio_btn.setToolTip("試聽這句台詞從遊戲／影片錄下的原聲語音 (Ctrl+P)")
        self.audio_btn.clicked.connect(self.audio_preview_requested.emit)
        top.addWidget(self.audio_btn)

        self.prompt_btn = QPushButton("📋 問 Gemini/GPT")
        self.prompt_btn.setObjectName("AccentBtn")
        self.prompt_btn.setToolTip("一鍵複製結構化日語拆解提問詞，直接貼給 Google AI Pro 或 ChatGPT Plus")
        self.prompt_btn.clicked.connect(self._emit_copy_prompt)
        top.addWidget(self.prompt_btn)

        self.mine_btn = QPushButton("✨ 精煉收錄")
        self.mine_btn.setObjectName("MineBtn")
        self.mine_btn.setToolTip("開啟 1 秒 Refold 精煉製卡：選 1 條本句核心意思＋聲優原聲＋畫面 CG (Ctrl+Enter)")
        self.mine_btn.setEnabled(False)
        self.mine_btn.clicked.connect(self._emit_mine)
        top.addWidget(self.mine_btn)

        layout.addLayout(top)

        self.trace_label = QLabel("")
        self.trace_label.setObjectName("MutedLabel")
        self.trace_label.setWordWrap(True)
        self.trace_label.hide()
        layout.addWidget(self.trace_label)

        # 多候選原形 1 點切換列（僅當一詞有多種可能原型如 あった -> ある/会う/合う 時出現）
        self.alt_bar = QWidget()
        self.alt_bar.setStyleSheet("background: transparent;")
        self.alt_layout = QHBoxLayout(self.alt_bar)
        self.alt_layout.setContentsMargins(0, 0, 0, 0)
        self.alt_layout.setSpacing(5)
        self.alt_bar.hide()
        layout.addWidget(self.alt_bar)

        # 雙語 / 日日單語 / 離線本句理解與語法
        self.tabs = QTabWidget()

        self.bi_browser = QTextBrowser()
        self.bi_browser.setOpenExternalLinks(False)
        self.bi_browser.setMinimumHeight(110)
        self.tabs.addTab(self.bi_browser, "雙語釋義")

        self.mono_browser = QTextBrowser()
        self.mono_browser.setOpenExternalLinks(False)
        self.mono_browser.setMinimumHeight(110)
        self.tabs.addTab(self.mono_browser, "日日單語")

        self.grammar_browser = QTextBrowser()
        self.grammar_browser.setOpenExternalLinks(False)
        self.grammar_browser.setMinimumHeight(110)
        self.tabs.addTab(self.grammar_browser, "🧭 理解本句與語法")

        layout.addWidget(self.tabs, 1)
        self._apply_palette_styles()

    def set_theme_name(self, theme_name: str) -> None:
        self._theme_name = theme_name
        self._apply_palette_styles()
        if self._last_args is not None:
            self.show_token(*self._last_args)
        if self._current_analysis is not None:
            self.set_sentence_analysis(self._current_analysis)

    def _apply_palette_styles(self) -> None:
        p = get_palette(self._theme_name)
        self.word_label.setStyleSheet(
            f"font-size: 20px; font-weight: 700; color: {p.accent_pink}; background: transparent;"
        )
        self.reading_label.setStyleSheet(
            f"font-size: 14px; color: {p.accent_lilac}; font-weight: 600; background: transparent;"
        )

    def _emit_mine(self) -> None:
        if self._current_token is not None:
            self.mine_requested.emit(self._current_token)

    def _emit_copy_prompt(self) -> None:
        self.copy_prompt_requested.emit(self._current_token)

    def _clear_alt_bar(self) -> None:
        while self.alt_layout.count():
            item = self.alt_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self.alt_bar.hide()

    def _switch_to_alt_match(self, alt: LookupMatch) -> None:
        if self._current_token is None or self._last_args is None:
            return
        old = self._current_token.lookup
        # 交換主候選與備選，保留來回切換能力
        new_alts = [a for a in (old.alt_matches if old else []) if a.lemma != alt.lemma]
        if old is not None and old.lemma != alt.lemma:
            old_copy = LookupMatch(
                surface=old.surface,
                lemma=old.lemma,
                reading=old.reading,
                reasons=old.reasons,
                bilingual_entries=old.bilingual_entries,
                monolingual_entries=old.monolingual_entries,
                pitches=old.pitches,
                frequency=old.frequency,
            )
            new_alts.insert(0, old_copy)
        alt.alt_matches = new_alts[:3]
        self._current_token.lookup = alt
        self._current_token.lemma = alt.lemma
        self._current_token.reading = alt.reading
        _, dict_pref, known, p_info = self._last_args
        self.show_token(self._current_token, dict_pref, known, p_info)

    def set_sentence_analysis(self, analysis: SentenceGrammarAnalysis | None) -> None:
        """更新第三分頁「🧭 理解本句與語法」（完全離線，每句自動執行）。"""
        self._current_analysis = analysis
        p = get_palette(self._theme_name)
        if analysis is None:
            self.tabs.setTabText(2, "🧭 理解本句與語法")
            self.grammar_browser.setHtml(
                f'<p style="color:{p.text_muted};">收到日語台詞或字幕後，這裡會自動生成日語語序直譯骨架與 N5→N1 核心句型拆解。</p>'
            )
            return

        hit_cnt = len(analysis.grammar_hits)
        tab_title = f"🧭 理解本句與語法 ({hit_cnt})" if hit_cnt else "🧭 理解本句與語法"
        self.tabs.setTabText(2, tab_title)
        self.grammar_browser.setHtml(
            analysis.to_compact_html(accent_color=p.accent_pink, muted_color=p.text_muted)
        )

    def show_token(
        self,
        token: Token,
        dict_preference: str = "bilingual",
        already_known: bool = False,
        particle_info: ParticleInfo | None = None,
    ) -> None:
        self._current_token = token
        self._last_args = (token, dict_preference, already_known, particle_info)
        self.mine_btn.setEnabled(True)

        p = get_palette(self._theme_name)
        match: LookupMatch | None = token.lookup
        lemma = match.lemma if match else token.lemma
        reading = match.reading if match else token.reading

        self.word_label.setText(lemma)
        self.reading_label.setText(f"「{reading}」" if reading and reading != lemma else "")

        meta_parts: list[str] = []
        if match and match.pitches:
            meta_parts.append("[" + "/".join(match.pitches[:3]) + "]")
        if match and match.frequency is not None:
            meta_parts.append(_frequency_hint(match.frequency))
        if already_known:
            meta_parts.append("✓ 已知")
        elif token.is_1t_candidate:
            meta_parts.append("★ 1T 推薦")
        self.meta_label.setText(" · ".join(meta_parts))

        trace_parts: list[str] = []
        if particle_info is not None:
            trace_parts.append(f"🔗 助詞 [{particle_info.level}] {particle_info.role}")
        elif match and match.all_entries:
            pos_friendly = explain_pos_friendly(match.all_entries[0].pos)
            if pos_friendly:
                trace_parts.append(f"🏷 {pos_friendly}")

        if token.surface != lemma:
            trace_parts.append(f"活用還原：{token.surface} → {lemma}")
        if match and match.reasons:
            trace_parts.append(" → ".join(match.reasons))

        if trace_parts:
            self.trace_label.setText("　｜　".join(trace_parts))
            self.trace_label.show()
        else:
            self.trace_label.hide()

        # 顯示多候選 1 點切換按鈕（若存在歧義詞形）
        self._clear_alt_bar()
        if match and match.alt_matches and particle_info is None:
            lbl = QLabel("🔄 其他可能原形：")
            lbl.setObjectName("MutedLabel")
            self.alt_layout.addWidget(lbl)
            for alt in match.alt_matches[:3]:
                b = QPushButton(f"{alt.lemma}" + (f"({alt.reading})" if alt.reading and alt.reading != alt.lemma else ""))
                b.setFixedHeight(23)
                b.clicked.connect(lambda _c=False, a=alt: self._switch_to_alt_match(a))
                self.alt_layout.addWidget(b)
            self.alt_layout.addStretch()
            self.alt_bar.show()

        self.mine_btn.setText("✨ 追加例句" if already_known else "✨ 精煉收錄")

        if match:
            base_bi = match.best_concise_meaning("bilingual", strict=True)
            ctx_meaning = format_contextual_meaning(token.surface, lemma, base_bi, match.reasons) if base_bi else ""
            self.bi_browser.setHtml(
                _format_entries_html(
                    match.bilingual_entries,
                    "目前啟用的雙語詞典未收錄此詞，可在「☰ → 📚 詞典管理」調整開啟的詞典。",
                    self._theme_name,
                    particle=particle_info,
                    context_meaning=ctx_meaning,
                )
            )
            self.mono_browser.setHtml(
                _format_entries_html(
                    match.monolingual_entries,
                    "目前未啟用日日單語詞典或未收錄此詞（Lv.3 階段可在「☰ → 📚 詞典管理」開啟日日單語）。",
                    self._theme_name,
                    particle=particle_info,
                )
            )
        elif particle_info is not None:
            p_html = _format_particle_card_html(particle_info, self._theme_name)
            self.bi_browser.setHtml(p_html)
            self.mono_browser.setHtml(p_html)
        else:
            no_hit = (
                f'<p style="color:{p.accent_gold};">目前啟用的詞典暫無「{html.escape(token.surface)}」詞條。</p>'
                f'<p style="color:{p.text_muted};">可點「📋 問 Gemini/GPT」一鍵複製到網頁版 AI，或在「📚 詞典管理」確認已啟用的詞典。</p>'
            )
            self.bi_browser.setHtml(no_hit)
            self.mono_browser.setHtml(no_hit)

        if dict_preference == "monolingual" and match and match.monolingual_entries:
            self.tabs.setCurrentIndex(1)
        else:
            self.tabs.setCurrentIndex(0)


class OverlayLookupPopup(QFrame):
    """附屬在電影級透明覆蓋層上方的 Yomitan 風格懸停浮動查詞卡（永不遮擋下方遊戲對話框！）。"""

    mine_requested = Signal(object)
    copy_prompt_requested = Signal(object)
    audio_preview_requested = Signal()
    hover_entered = Signal()
    hover_left = Signal()

    def __init__(self, theme_name: str = "sakura", parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedWidth(410)
        self._theme_name = theme_name
        self._token: Token | None = None
        self._last_popup_args: tuple | None = None
        self._build_ui()
        self.set_theme_name(theme_name)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 10, 13, 10)
        layout.setSpacing(6)

        top = QHBoxLayout()
        self.title_label = QLabel("")
        top.addWidget(self.title_label)

        self.reading_label = QLabel("")
        top.addWidget(self.reading_label)
        top.addStretch()

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(22, 22)
        close_btn.clicked.connect(self.hide)
        top.addWidget(close_btn)
        layout.addLayout(top)

        self.reason_label = QLabel("")
        self.reason_label.setWordWrap(True)
        layout.addWidget(self.reason_label)

        # 多候選 1 點切換列
        self.alt_bar = QWidget()
        self.alt_bar.setStyleSheet("background: transparent;")
        self.alt_layout = QHBoxLayout(self.alt_bar)
        self.alt_layout.setContentsMargins(0, 0, 0, 0)
        self.alt_layout.setSpacing(4)
        self.alt_bar.hide()
        layout.addWidget(self.alt_bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(145)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent;")
        self.def_label = QLabel("")
        self.def_label.setWordWrap(True)
        self.def_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        scroll.setWidget(self.def_label)
        layout.addWidget(scroll)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self.mine_btn = QPushButton("✨ 精煉收錄 Anki（音畫）")
        self.mine_btn.setObjectName("MineBtn")
        self.mine_btn.clicked.connect(self._on_mine)
        btn_row.addWidget(self.mine_btn, 1)

        self.audio_btn = QPushButton("🔊")
        self.audio_btn.setFixedWidth(34)
        self.audio_btn.setToolTip("試聽本句聲優原聲")
        self.audio_btn.clicked.connect(self.audio_preview_requested.emit)
        btn_row.addWidget(self.audio_btn)

        self.prompt_btn = QPushButton("📋 問AI")
        self.prompt_btn.setToolTip("複製提問詞給 Gemini Pro / ChatGPT Plus")
        self.prompt_btn.clicked.connect(self._on_copy_prompt)
        btn_row.addWidget(self.prompt_btn)

        layout.addLayout(btn_row)

    def enterEvent(self, event) -> None:  # noqa: N802
        self.hover_entered.emit()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.hover_left.emit()
        super().leaveEvent(event)

    def set_theme_name(self, theme_name: str) -> None:
        self._theme_name = theme_name
        p = get_palette(theme_name)
        self.setStyleSheet(
            f"""
            OverlayLookupPopup {{
                background-color: {p.popup_bg};
                border: 1.5px solid {p.overlay_border};
                border-radius: 14px;
            }}
            """
        )
        self.title_label.setStyleSheet(
            f"font-size: 18px; font-weight: 700; color: {p.accent_pink}; background: transparent;"
        )
        self.reading_label.setStyleSheet(
            f"font-size: 13px; color: {p.accent_lilac}; background: transparent;"
        )
        self.reason_label.setStyleSheet(
            f"font-size: 11px; color: {p.text_muted}; background: transparent;"
        )
        self.def_label.setStyleSheet(
            f"font-size: 13.5px; line-height: 1.55; color: {p.text_primary}; background: transparent; padding: 2px;"
        )

    def _on_mine(self) -> None:
        if self._token is not None:
            self.mine_requested.emit(self._token)
            self.hide()

    def _on_copy_prompt(self) -> None:
        self.copy_prompt_requested.emit(self._token)
        self.hide()

    def _clear_alts(self) -> None:
        while self.alt_layout.count():
            item = self.alt_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self.alt_bar.hide()

    def _swap_alt(self, alt: LookupMatch) -> None:
        if self._token is None or self._last_popup_args is None:
            return
        old = self._token.lookup
        new_alts = [a for a in (old.alt_matches if old else []) if a.lemma != alt.lemma]
        if old is not None and old.lemma != alt.lemma:
            new_alts.insert(
                0,
                LookupMatch(
                    surface=old.surface,
                    lemma=old.lemma,
                    reading=old.reading,
                    reasons=old.reasons,
                    bilingual_entries=old.bilingual_entries,
                    monolingual_entries=old.monolingual_entries,
                    pitches=old.pitches,
                    frequency=old.frequency,
                ),
            )
        alt.alt_matches = new_alts[:3]
        self._token.lookup = alt
        self._token.lemma = alt.lemma
        self._token.reading = alt.reading
        _, pref, known, p_info = self._last_popup_args
        self.popup_for_token(self._token, pref, known, p_info)

    def popup_for_token(
        self,
        token: Token,
        dict_preference: str,
        already_known: bool,
        particle_info: ParticleInfo | None = None,
    ) -> None:
        self._token = token
        self._last_popup_args = (token, dict_preference, already_known, particle_info)
        match = token.lookup
        lemma = match.lemma if match else token.lemma
        reading = match.reading if match else token.reading

        self.title_label.setText(lemma)
        pitch_txt = f" [{'/'.join(match.pitches[:2])}]" if (match and match.pitches) else ""
        freq_txt = f" {_frequency_hint(match.frequency)}" if (match and match.frequency) else ""
        self.reading_label.setText(f"{reading}{pitch_txt}{freq_txt}")

        meta_lines: list[str] = []
        if particle_info is not None:
            meta_lines.append(f"🔗 助詞 [{particle_info.level}] {particle_info.role}（{particle_info.formula}）")
        elif match and match.all_entries:
            pos_friendly = explain_pos_friendly(match.all_entries[0].pos)
            if pos_friendly:
                meta_lines.append(f"🏷 {pos_friendly}")
        if match and match.reasons and token.surface != lemma:
            meta_lines.append(f"{token.surface} → {lemma}（{' → '.join(match.reasons)}）")

        if meta_lines:
            self.reason_label.setText(" ｜ ".join(meta_lines))
            self.reason_label.show()
        else:
            self.reason_label.hide()

        self._clear_alts()
        if match and match.alt_matches and particle_info is None:
            lbl = QLabel("其他：")
            lbl.setStyleSheet("font-size: 11px; opacity: 0.7; background: transparent;")
            self.alt_layout.addWidget(lbl)
            for alt in match.alt_matches[:3]:
                b = QPushButton(alt.lemma)
                b.setFixedHeight(21)
                b.clicked.connect(lambda _c=False, a=alt: self._swap_alt(a))
                self.alt_layout.addWidget(b)
            self.alt_layout.addStretch()
            self.alt_bar.show()

        parts: list[str] = []
        if particle_info is not None:
            parts.append(f"【助詞用法】{particle_info.meaning}")
        elif match:
            base = match.best_concise_meaning(dict_preference)
            ctx = format_contextual_meaning(token.surface, lemma, base, match.reasons)
            if ctx:
                parts.append(f"🎯 {ctx}")
            extra = [s for s in match.concise_senses(dict_preference, max_senses=3) if s not in ctx]
            if extra:
                parts.append("其他義項：" + " ／ ".join(extra[:2]))
        if not parts:
            parts.append("詞典暫無此詞條（可按「📋 問AI」複製給 Gemini/GPT，或直接收錄音畫句卡）。")

        self.def_label.setText("\n".join(parts))
        self.mine_btn.setText("✨ 追加例句卡（音畫）" if already_known else "✨ 精煉收錄 Anki（音畫）")
        self.adjustSize()
        self.show()
