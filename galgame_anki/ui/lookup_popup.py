"""輕量查詞詳情面板（自適應長方形格子自動排版、外來語原詞、結構化語法邏輯與精簡關聯詞）與懸停浮動查詞卡。"""

from __future__ import annotations

import ctypes
import html
import re
from functools import lru_cache

from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QRegion
from typing import Callable

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from galgame_anki.core.study_map import CONCEPTS, focus_for
from galgame_anki.core.verb_lesson import FORMS, VerbLesson, lesson_for_token
from galgame_anki.core.context_meaning import context_gloss_is_chinese, explain_lookup_in_sentence
from galgame_anki.core.dictionary import (
    DictEntry,
    LookupMatch,
    SenseItem,
    _senses_semantically_overlap,
    extract_concise_senses,
    extract_loanword_origins,
    is_katakana_loanword,
)
from galgame_anki.core.web_lookup import WEB_LOOKUP_SITES, build_web_lookup_url
from galgame_anki.core.grammar_engine import (
    ClauseChunk,
    GrammarHit,
    ParticleInfo,
    SentenceGrammarAnalysis,
    SyntaxRelation,
    explain_pos_friendly,
)
from galgame_anki.core.tokenizer import Token
from galgame_anki.core.word_associations import RelatedWordNode, WordAssociationTree
from galgame_anki.ui.pitch_widget import PitchGraphWidget
from galgame_anki.ui.sentence_widget import FlowLayout
from galgame_anki.ui.web_dictionary import show_web_dictionary
from galgame_anki.ui.theme import ThemePalette, get_palette


def _frequency_hint(freq: int | None) -> str:
    if freq is None:
        return ""
    if freq <= 5000:
        return f"#{freq} 高頻"
    if freq <= 15000:
        return f"#{freq} 常用"
    return f"#{freq}"


def _format_particle_card_html(particle: ParticleInfo, theme_name: str) -> str:
    p = get_palette(theme_name)
    return (
        f'<div style="margin-bottom:8px; padding:8px 11px; background:{p.pill_ok_bg}; '
        f'border-left:3px solid {p.accent_pink}; border-radius:8px; color:{p.text_primary};">'
        f'<div style="font-size:12px; font-weight:700; color:{p.accent_pink}; margin-bottom:2px;">'
        f"助詞 [{html.escape(particle.level)}] · {html.escape(particle.role)}</div>"
        f'<div style="font-size:12px; color:{p.text_muted}; margin-bottom:3px;">接續：{html.escape(particle.formula)}</div>'
        f'<div style="font-size:14px; line-height:1.55;">{html.escape(particle.meaning)}</div>'
        f"</div>"
    )


def _format_entries_html(
    entries: list[DictEntry],
    empty_hint: str,
    theme_name: str,
    particle: ParticleInfo | None = None,
    context_meaning: str = "",
    term: str = "",
) -> str:
    """產生去重後的精簡釋義 HTML（保留給純 HTML 消費者與外來語原詞輸出）。"""
    p = get_palette(theme_name)
    blocks: list[str] = []
    if particle is not None:
        blocks.append(_format_particle_card_html(particle, theme_name))

    if not entries:
        if not blocks:
            return f'<p style="color:{p.text_muted}; margin:4px 0;">{html.escape(empty_hint)}</p>'
        return "".join(blocks)

    origins = extract_loanword_origins(entries, term, max_items=3) if term else []
    if origins:
        origin_str = " · ".join(html.escape(o) for o in origins)
        blocks.append(
            f'<div style="margin-bottom:6px; padding:5px 10px; background:{p.pill_info_bg}; '
            f'border:1px solid {p.accent_lilac}; border-radius:7px; color:{p.pill_info_fg}; font-size:12.5px;">'
            f"<b>EN</b> · {origin_str}</div>"
        )

    focused = extract_concise_senses(entries, max_senses=5)
    primary = ""
    is_contextual_override = False
    if particle is None:
        if context_meaning:
            primary = context_meaning
            is_contextual_override = not focused or (
                context_meaning != focused[0] and not _senses_semantically_overlap(context_meaning, focused[0])
            )
        elif focused:
            primary = focused[0]

    other_senses = [
        s
        for s in focused
        if s and (not primary or (s != primary and not _senses_semantically_overlap(s, primary)))
    ]

    if primary:
        eyebrow = (
            f'<div style="font-size:11px; font-weight:700; color:{p.accent_pink}; margin-bottom:2px;">本句義</div>'
            if is_contextual_override
            else ""
        )
        blocks.append(
            f'<div style="margin-bottom:8px; padding:9px 12px; background:{p.pill_ok_bg}; '
            f'border-left:3px solid {p.accent_pink}; border-radius:8px; color:{p.text_primary};">'
            f"{eyebrow}"
            f'<div style="font-size:15px; font-weight:600; line-height:1.55;">{html.escape(primary)}</div>'
            f"</div>"
        )

    if other_senses:
        start_idx = 2 if primary else 1
        bullet_items = "".join(
            f'<div style="margin:2px 0; line-height:1.55;">'
            f'<span style="color:{p.text_muted}; font-weight:600;">{idx}.</span> {html.escape(s)}</div>'
            for idx, s in enumerate(other_senses, start=start_idx)
        )
        blocks.append(
            f'<div style="margin-bottom:8px; padding:8px 11px; background:{p.bg_input}; '
            f'border:1px solid {p.border}; border-radius:8px; font-size:13.5px; color:{p.text_primary};">'
            f"{bullet_items}</div>"
        )

    return "".join(blocks)


class _AdaptiveFlowLayout(FlowLayout):
    """自適應長方形格子流式排版：短格子依字長並排換行，長格子自動貼合容器寬度折行不溢出。"""

    def __init__(self, parent: QWidget | None = None, margin: int = 0, h_spacing: int = 7, v_spacing: int = 7) -> None:
        super().__init__(parent, margin=margin, h_spacing=h_spacing, v_spacing=v_spacing)

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        max_w = max(80, effective.width())
        x = effective.x()
        y = effective.y()
        line_height = 0

        for item in self._items:
            hint = item.sizeHint()
            item_w = min(hint.width(), max_w)
            if hint.width() > max_w and item.hasHeightForWidth():
                item_h = item.heightForWidth(item_w)
            else:
                item_h = hint.height()

            if x > effective.x() and x + item_w > effective.right() + 1 and line_height > 0:
                x = effective.x()
                y += line_height + self._v_spacing
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), QSize(item_w, item_h)))

            x += item_w + self._h_spacing
            line_height = max(line_height, item_h)

        return y + line_height - rect.y() + m.bottom()


class _FlowRowWidget(QWidget):
    """承載自適應長方形格子的流式列元件，精準回報 heightForWidth 讓捲動區高度完全貼合。"""

    def __init__(self, h_spacing: int = 7, v_spacing: int = 7, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._flow = _AdaptiveFlowLayout(self, margin=0, h_spacing=h_spacing, v_spacing=v_spacing)
        self.setLayout(self._flow)
        self.setStyleSheet("background: transparent;")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def add_tile(self, widget: QWidget) -> None:
        self._flow.addWidget(widget)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._flow.heightForWidth(max(120, width))

    def sizeHint(self) -> QSize:  # noqa: N802
        w = self.width() if self.width() > 80 else 420
        return QSize(w, self.heightForWidth(w))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(120, max(24, self._flow.minimumSize().height()))


class _TileScrollView(QScrollArea):
    """清爽垂直捲動長方形格子容器，用於雙語、單語、句型拆解與關聯詞四個分頁。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.viewport().setStyleSheet("background: transparent; border: none;")
        self.setMinimumHeight(120)

        self._content = QWidget()
        self._content.setStyleSheet("background: transparent; border: none;")
        self._vbox = QVBoxLayout(self._content)
        self._vbox.setContentsMargins(8, 6, 8, 8)
        self._vbox.setSpacing(6)
        self._vbox.addStretch(1)
        self.setWidget(self._content)
        self._plain_parts: list[str] = []

    def clear(self) -> None:
        self._plain_parts.clear()
        while self._vbox.count():
            item = self._vbox.takeAt(0)
            if item is None:
                continue
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self._vbox.addStretch(1)

    def _insert_widget(self, widget: QWidget) -> None:
        idx = max(0, self._vbox.count() - 1)
        self._vbox.insertWidget(idx, widget)

    def add_section_header(self, title: str, p: ThemePalette, subtitle: str = "") -> None:
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        hl = QHBoxLayout(row)
        hl.setContentsMargins(2, 2, 2, 0)
        hl.setSpacing(6)
        lbl = QLabel(title)
        lbl.setStyleSheet(
            f"font-size: 11.5px; font-weight: 700; color: {p.accent_pink}; background: transparent;"
        )
        hl.addWidget(lbl)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setStyleSheet(f"font-size: 11px; color: {p.text_muted}; background: transparent;")
            hl.addWidget(sub)
        hl.addStretch()
        self._insert_widget(row)
        self._plain_parts.append(f"{title} {subtitle}".strip())

    def add_flow_tiles(self, widgets: list[QWidget], plain_texts: list[str] | None = None, h_spacing: int = 7, v_spacing: int = 7) -> None:
        if not widgets:
            return
        flow = _FlowRowWidget(h_spacing=h_spacing, v_spacing=v_spacing)
        for w in widgets:
            flow.add_tile(w)
        self._insert_widget(flow)
        if plain_texts:
            self._plain_parts.extend(plain_texts)

    def add_block(self, widget: QWidget, plain_text: str = "") -> None:
        self._insert_widget(widget)
        if plain_text:
            self._plain_parts.append(plain_text)

    def add_muted_hint(self, text: str, p: ThemePalette) -> None:
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"font-size: 13px; color: {p.text_muted}; background: transparent; padding: 4px 2px;")
        self._insert_widget(lbl)
        self._plain_parts.append(text)

    def toPlainText(self) -> str:  # noqa: N802
        return "\n".join(t for t in self._plain_parts if t)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._content.updateGeometry()


def _make_origin_tile(origin: str, p: ThemePalette) -> QFrame:
    """片假名外來語的英文／原語長方形格子。"""
    is_other_lang = "(" in origin and origin.endswith(")")
    badge_txt = "原語" if is_other_lang else "EN"
    box = QFrame()
    box.setStyleSheet(
        f"QFrame {{ background: {p.pill_info_bg}; border: 1px solid {p.accent_lilac}; border-radius: 7px; }}"
    )
    hl = QHBoxLayout(box)
    hl.setContentsMargins(9, 5, 10, 5)
    hl.setSpacing(6)

    tag = QLabel(badge_txt)
    tag.setStyleSheet(
        f"font-size: 10.5px; font-weight: 700; color: {p.accent_lilac}; "
        f"background: transparent; border: none;"
    )
    hl.addWidget(tag)

    val = QLabel(origin)
    val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    val.setStyleSheet(
        f"font-size: 13.5px; font-weight: 700; color: {p.text_primary}; "
        f"background: transparent; border: none;"
    )
    hl.addWidget(val)
    return box


def _make_primary_sense_tile(text: str, p: ThemePalette, eyebrow: str = "") -> QFrame:
    """核心首選釋義長方形主卡（左亮條強調，自動依字數撐開或換行）。"""
    box = QFrame()
    box.setStyleSheet(
        f"QFrame {{ background: {p.pill_ok_bg}; border: 1px solid {p.border}; "
        f"border-left: 3px solid {p.accent_pink}; border-radius: 7px; }}"
    )
    vl = QVBoxLayout(box)
    vl.setContentsMargins(11, 7, 11, 7)
    vl.setSpacing(2)

    if eyebrow:
        eb = QLabel(eyebrow)
        eb.setStyleSheet(
            f"font-size: 10.5px; font-weight: 700; color: {p.accent_pink}; "
            f"background: transparent; border: none;"
        )
        vl.addWidget(eb)

    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    lbl.setStyleSheet(
        f"font-size: 14.5px; font-weight: 600; color: {p.text_primary}; "
        f"background: transparent; border: none;"
    )
    vl.addWidget(lbl)
    return box


def _make_sense_tile(text: str, p: ThemePalette) -> QFrame:
    """自適應文字長度的長方形釋義格子。"""
    box = QFrame()
    box.setStyleSheet(
        f"QFrame {{ background: {p.bg_input}; border: 1px solid {p.border}; border-radius: 7px; }}"
    )
    vl = QVBoxLayout(box)
    vl.setContentsMargins(10, 6, 10, 6)
    vl.setSpacing(0)

    lbl = QLabel(text)
    lbl.setWordWrap(len(text) > 22)
    lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    lbl.setStyleSheet(
        f"font-size: 13px; color: {p.text_primary}; background: transparent; border: none;"
    )
    vl.addWidget(lbl)
    return box


def _make_particle_card(particle: ParticleInfo, p: ThemePalette) -> QFrame:
    box = QFrame()
    box.setStyleSheet(
        f"QFrame {{ background: {p.pill_ok_bg}; border: 1px solid {p.border}; "
        f"border-left: 3px solid {p.accent_pink}; border-radius: 7px; }}"
    )
    vl = QVBoxLayout(box)
    vl.setContentsMargins(11, 7, 11, 7)
    vl.setSpacing(3)

    head = QLabel(f"助詞 [{particle.level}] · {particle.role}")
    head.setStyleSheet(
        f"font-size: 12px; font-weight: 700; color: {p.accent_pink}; background: transparent; border: none;"
    )
    vl.addWidget(head)

    formula = QLabel(f"接續：{particle.formula}")
    formula.setWordWrap(True)
    formula.setStyleSheet(
        f"font-size: 11.5px; color: {p.text_muted}; background: transparent; border: none;"
    )
    vl.addWidget(formula)

    meaning = QLabel(particle.meaning)
    meaning.setWordWrap(True)
    meaning.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    meaning.setStyleSheet(
        f"font-size: 13.5px; color: {p.text_primary}; background: transparent; border: none;"
    )
    vl.addWidget(meaning)
    return box


def _make_assoc_tile(node: RelatedWordNode, p: ThemePalette) -> QFrame:
    """精簡聚焦的關聯詞長方形卡片：只留詞＋核心重點＋熟詞錨點（零 #詞頻／零重複標籤）。"""
    border_col = p.accent_mint if node.is_known else p.border
    box = QFrame()
    box.setStyleSheet(
        f"QFrame {{ background: {p.bg_input}; border: 1px solid {border_col}; border-radius: 7px; }}"
    )
    vl = QVBoxLayout(box)
    vl.setContentsMargins(10, 6, 10, 6)
    vl.setSpacing(2)

    top = QHBoxLayout()
    top.setContentsMargins(0, 0, 0, 0)
    top.setSpacing(5)

    if node.is_known:
        chk = QLabel("✓")
        chk.setStyleSheet(
            f"font-size: 11.5px; font-weight: 700; color: {p.accent_mint}; background: transparent; border: none;"
        )
        top.addWidget(chk)

    term_lbl = QLabel(node.term)
    term_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    term_lbl.setStyleSheet(
        f"font-size: 13.5px; font-weight: 700; color: {p.text_primary}; background: transparent; border: none;"
    )
    top.addWidget(term_lbl)

    if node.reading and node.reading != node.term and node.relation != "homophone":
        rd_lbl = QLabel(node.reading)
        rd_lbl.setStyleSheet(
            f"font-size: 11px; color: {p.text_muted}; background: transparent; border: none;"
        )
        top.addWidget(rd_lbl)

    if node.relation == "homophone" and node.pitch:
        pt_lbl = QLabel(f"[{node.pitch}]")
        pt_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 700; color: {p.accent_lilac}; background: transparent; border: none;"
        )
        top.addWidget(pt_lbl)

    if node.tag:
        tg_lbl = QLabel(node.tag)
        tg_lbl.setStyleSheet(
            f"font-size: 10.5px; font-weight: 600; color: {p.accent_lilac}; background: transparent; border: none;"
        )
        top.addWidget(tg_lbl)

    top.addStretch()
    vl.addLayout(top)

    if node.meaning:
        mg = QLabel(node.meaning)
        mg.setWordWrap(len(node.meaning) > 16)
        mg.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        mg.setStyleSheet(
            f"font-size: 12px; color: {p.text_secondary}; background: transparent; border: none;"
        )
        vl.addWidget(mg)

    return box


def _role_accent(role_key: str, p: ThemePalette) -> tuple[str, str]:
    """為不同語法角色分配直覺區隔的框線／標籤色（不刺眼、雙主題和諧）。"""
    if role_key == "predicate":
        return p.accent_pink, p.pill_info_bg
    if role_key in ("topic", "subject"):
        return p.accent_pink, p.bg_input
    if role_key == "object":
        return p.accent_mint, p.bg_input
    if role_key in ("concession", "condition", "reason"):
        return p.accent_gold, p.pill_warn_bg
    if role_key in ("content_link", "simile", "clause_mod", "modifier", "adverb"):
        return p.accent_lilac, p.bg_input
    if role_key in ("place", "target", "time_place"):
        return p.accent_gold, p.bg_input
    return p.border, p.bg_input


def _make_chunk_tile(chunk: ClauseChunk, p: ThemePalette) -> QFrame:
    """日語文節長方形結構方塊：頂部角色標籤 ＋ 中間日語塊 ＋ 底部中文核心意。"""
    accent, bg = _role_accent(chunk.role_key, p)
    border_w = "1.5px" if chunk.role_key in ("predicate", "topic", "concession", "condition") else "1px"
    box = QFrame()
    box.setStyleSheet(
        f"QFrame {{ background: {bg}; border: {border_w} solid {accent}; border-radius: 7px; }}"
    )
    vl = QVBoxLayout(box)
    vl.setContentsMargins(9, 5, 9, 5)
    vl.setSpacing(1)

    role_lbl = QLabel(chunk.role_label)
    role_lbl.setStyleSheet(
        f"font-size: 10px; font-weight: 700; color: {accent}; background: transparent; border: none;"
    )
    vl.addWidget(role_lbl)

    txt_lbl = QLabel(chunk.text)
    txt_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    txt_lbl.setStyleSheet(
        f"font-size: 13.5px; font-weight: 700; color: {p.text_primary}; background: transparent; border: none;"
    )
    vl.addWidget(txt_lbl)

    if chunk.gloss:
        gl_lbl = QLabel(chunk.gloss)
        gl_lbl.setWordWrap(len(chunk.gloss) > 14)
        gl_lbl.setStyleSheet(
            f"font-size: 11px; color: {p.text_secondary}; background: transparent; border: none;"
        )
        vl.addWidget(gl_lbl)
    return box


def _make_relation_card(rel: SyntaxRelation, p: ThemePalette) -> QFrame:
    """修飾與邏輯關係箭頭列：清楚呈現「誰修飾誰／前件如何導向結論」。"""
    box = QFrame()
    box.setStyleSheet(
        f"QFrame {{ background: {p.bg_input}; border: 1px solid {p.border}; border-radius: 7px; }}"
    )
    vl = QVBoxLayout(box)
    vl.setContentsMargins(10, 6, 10, 6)
    vl.setSpacing(2)

    arrow_html = (
        f"<b>{html.escape(rel.source)}</b> "
        f'<span style="color:{p.accent_pink}; font-weight:700;">── {html.escape(rel.relation)} ──▶</span> '
        f"<b>{html.escape(rel.target)}</b>"
    )
    lbl = QLabel(arrow_html)
    lbl.setWordWrap(True)
    lbl.setTextFormat(Qt.TextFormat.RichText)
    lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    lbl.setStyleSheet(f"font-size: 13px; color: {p.text_primary}; background: transparent; border: none;")
    vl.addWidget(lbl)

    if rel.explanation:
        exp = QLabel(rel.explanation)
        exp.setWordWrap(True)
        exp.setStyleSheet(f"font-size: 11.5px; color: {p.text_muted}; background: transparent; border: none;")
        vl.addWidget(exp)
    return box


def _make_pattern_card(
    hit: GrammarHit,
    p: ThemePalette,
    on_click: Callable[[str], None],
) -> QFrame:
    """可點擊的命中句型卡片：點擊直接跳轉至語法技能樹對應條目。"""
    box = QFrame()
    box.setStyleSheet(
        f"QFrame {{ background: {p.bg_input}; border: 1px solid {p.border}; "
        f"border-left: 3px solid {p.accent_pink}; border-radius: 7px; }}"
    )
    vl = QVBoxLayout(box)
    vl.setContentsMargins(10, 7, 10, 7)
    vl.setSpacing(3)

    top = QHBoxLayout()
    top.setContentsMargins(0, 0, 0, 0)
    top.setSpacing(6)

    title = QLabel(f"[{hit.level}] {hit.name}")
    title.setStyleSheet(
        f"font-size: 13px; font-weight: 700; color: {p.text_primary}; background: transparent; border: none;"
    )
    top.addWidget(title)

    meta = QLabel(f"{hit.category} · 「{hit.matched_text}」")
    meta.setStyleSheet(f"font-size: 11px; color: {p.text_muted}; background: transparent; border: none;")
    top.addWidget(meta)
    top.addStretch()

    jump_btn = QPushButton("技能樹")
    jump_btn.setFixedHeight(22)
    jump_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    jump_btn.setStyleSheet(
        f"QPushButton {{ font-size: 11px; padding: 1px 8px; border-radius: 6px; "
        f"background: {p.pill_info_bg}; color: {p.pill_info_fg}; border: none; }}"
        f"QPushButton:hover {{ background: {p.accent_pink}; color: #ffffff; }}"
    )
    jump_btn.clicked.connect(lambda _c=False, pid=hit.pattern_id: on_click(pid))
    top.addWidget(jump_btn)
    vl.addLayout(top)

    formula = QLabel(f"接續：{hit.formula}")
    formula.setWordWrap(True)
    formula.setStyleSheet(f"font-size: 11.5px; color: {p.text_muted}; background: transparent; border: none;")
    vl.addWidget(formula)

    meaning = QLabel(hit.meaning)
    meaning.setWordWrap(True)
    meaning.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    meaning.setStyleSheet(f"font-size: 12.5px; color: {p.text_primary}; background: transparent; border: none;")
    vl.addWidget(meaning)
    return box


def _lesson_label(lesson: VerbLesson, link_color: str, preview: bool = False) -> str:
    heading = "試換一個詞尾" if preview else FORMS[lesson.form][0]
    if lesson.expanded:
        heading = FORMS[lesson.form][2]
    return (f"<b>{heading}</b>" + lesson.diagram("示範接法" if preview else "台詞裡") + html.escape(lesson.rule) +
            f'　<a href="compare" style="color:{link_color}">比較其他變法</a>')


class StudyLink(QLabel):
    requested = Signal(object, str, str)

    def __init__(self):
        super().__init__()
        self.setWordWrap(True)
        self.hide()
        self.linkActivated.connect(self._open)

    def bind(self, token, sentence, tokens, phrase, particle, translation, color):
        self.focus = focus_for(token, tokens=tokens, phrase=phrase, particle=particle, sentence=sentence, translation=translation)
        self.sentence, self.lemma = sentence, token.lemma
        concept = CONCEPTS[self.focus.key]
        collocation = self.focus.vocabulary
        prefix = f"{html.escape(collocation)}　·　" if collocation not in (token.surface, token.lemma) and len(collocation) <= 22 else ""
        self.setText(f'{prefix}<a style="color:{color}" href="map">逐步看懂「{concept.title}」</a>')
        self.setToolTip(self.focus.evidence)
        self.show()

    def _open(self, _url):
        self.requested.emit(self.focus, self.sentence, self.lemma)


class LookupDetailPanel(QFrame):
    """嵌入主視窗下方的即時查詞面板（自適應格子釋義、外來語原詞、繁中/日日/日英分流、結構化語法邏輯與按需網頁查證）。"""

    lesson_requested = Signal(object)
    mine_requested = Signal(object)             # Token (or phrase Token)
    copy_prompt_requested = Signal(object)      # Token | None
    audio_preview_requested = Signal()
    blueprint_requested = Signal(str)           # pattern_id (empty string = open overview/tree)
    web_lookup_requested = Signal(str, str, str)  # (site_id, query, url)

    def __init__(self, theme_name: str = "sakura", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CardPanel")
        self._theme_name = theme_name
        self._current_token: Token | None = None
        self._single_token: Token | None = None
        self._phrase_token: Token | None = None
        self._showing_phrase: bool = False
        self._current_analysis: SentenceGrammarAnalysis | None = None
        self._current_assoc: WordAssociationTree | None = None
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

        self.audio_btn = QPushButton("試聽")
        self.audio_btn.setToolTip("試聽這句台詞的原聲語音 (Ctrl+P)")
        self.audio_btn.clicked.connect(self.audio_preview_requested.emit)
        top.addWidget(self.audio_btn)

        self.more_btn = QPushButton("⋯")
        self.more_btn.setObjectName("GhostBtn")
        self.more_btn.setToolTip("網頁辭典與複製提問")
        self.more_btn.setEnabled(False)
        self.more_menu = QMenu(self.more_btn)
        web_menu = self.more_menu.addMenu("在 GalAnki 查網頁辭典")
        for site_id, site_label, _tpl in WEB_LOOKUP_SITES:
            act = web_menu.addAction(site_label)
            act.triggered.connect(lambda _checked=False, sid=site_id: self.open_web_lookup(sid))
        self.more_menu.addAction("複製 AI 提問", self._emit_copy_prompt)
        self.more_btn.setMenu(self.more_menu)
        top.addWidget(self.more_btn)

        self.mine_btn = QPushButton("一鍵收錄")
        self.mine_btn.setObjectName("MineBtn")
        self.mine_btn.setToolTip(
            "左鍵直接寫入 Anki（右下角可撤銷）；按住 Shift 點擊可先編輯 (Ctrl+Enter)"
        )
        self.mine_btn.setEnabled(False)
        self.mine_btn.clicked.connect(self._emit_mine)
        top.addWidget(self.mine_btn)

        layout.addLayout(top)

        self.trace_line = QHBoxLayout()
        self.trace_label = QLabel("")
        self.trace_label.setObjectName("MutedLabel")
        self.trace_label.setWordWrap(True)
        self.trace_label.hide()
        self.trace_line.addWidget(self.trace_label, 1)
        self.study_link = StudyLink()
        self.trace_line.addWidget(self.study_link)
        self.pitch_button = QPushButton("音調")
        self.pitch_button.setObjectName("GhostBtn")
        self.pitch_button.setCheckable(True)
        self.pitch_button.hide()
        self.trace_line.addWidget(self.pitch_button)
        layout.addLayout(self.trace_line)

        self.pitch_widget = PitchGraphWidget(compact=True, dark_mode=(self._theme_name == "starlight"))
        self.pitch_widget.hide()
        self.pitch_button.toggled.connect(self.pitch_widget.setVisible)
        layout.addWidget(self.pitch_widget)

        # 語塊／獨立詞切換與多候選原形列
        self.alt_bar = QWidget()
        self.alt_bar.setStyleSheet("background: transparent;")
        self.alt_layout = _AdaptiveFlowLayout(self.alt_bar, margin=0, h_spacing=5, v_spacing=4)
        self.alt_layout.setContentsMargins(0, 0, 0, 0)
        self.alt_layout.setSpacing(5)
        self.alt_bar.hide()
        layout.addWidget(self.alt_bar)

        self.tabs = QTabWidget()

        self.bi_view = _TileScrollView()
        self.tabs.addTab(self.bi_view, "本句義")

        self.mono_view = _TileScrollView()
        self.tabs.addTab(self.mono_view, "日日")

        self.en_view = _TileScrollView()
        self.tabs.addTab(self.en_view, "日英")

        self.grammar_view = _TileScrollView()
        self.tabs.addTab(self.grammar_view, "句型拆解")

        self.assoc_view = _TileScrollView()
        self.tabs.addTab(self.assoc_view, "關聯詞")

        layout.addWidget(self.tabs, 1)
        self._apply_palette_styles()

    def open_web_lookup(self, site_id: str = "kotobank") -> str:
        """僅於使用者手動點擊時在程式內開啟原形或語塊的網頁辭典（不自動觸發、不傳整句）。"""
        tok = self._current_token
        if tok is None:
            return ""
        query = (tok.lookup.lemma if tok.lookup else tok.lemma) or tok.surface
        url = build_web_lookup_url(site_id, query)
        if not url:
            return ""
        self.web_lookup_requested.emit(site_id, query, url)
        show_web_dictionary(query, self, site_id)
        return url

    def _toggle_phrase_mode(self, use_phrase: bool) -> None:
        if self._last_args is None or self._single_token is None or self._phrase_token is None:
            return
        self._showing_phrase = bool(use_phrase)
        active = self._phrase_token if self._showing_phrase else self._single_token
        _, dict_pref, already_known, particle_info, sentence, translation = self._last_args
        self._render_active_token(
            active,
            dict_pref,
            already_known,
            particle_info if not self._showing_phrase else None,
            sentence,
            translation,
        )

    def set_theme_name(self, theme_name: str) -> None:
        self._theme_name = theme_name
        self._apply_palette_styles()
        if self._last_args is not None and self._current_token is not None:
            _, dict_pref, already_known, particle_info, sentence, translation = self._last_args
            self._render_active_token(
                self._current_token,
                dict_pref,
                already_known,
                None if self._showing_phrase else particle_info,
                sentence,
                translation,
            )
        if self._current_analysis is not None:
            self.set_sentence_analysis(self._current_analysis)
        if self._current_assoc is not None:
            self.set_word_associations(self._current_assoc)

    def _apply_palette_styles(self) -> None:
        p = get_palette(self._theme_name)
        self.word_label.setStyleSheet(
            f"font-size: 20px; font-weight: 700; color: {p.accent_pink}; background: transparent;"
        )
        self.reading_label.setStyleSheet(
            f"font-size: 14px; color: {p.accent_lilac}; font-weight: 600; background: transparent;"
        )
        self.pitch_widget.set_dark_mode(self._theme_name == "starlight")

    def _emit_mine(self) -> None:
        if self._current_token is not None:
            self.mine_requested.emit(self._current_token)

    def _emit_copy_prompt(self) -> None:
        self.copy_prompt_requested.emit(self._current_token)

    def _clear_alt_bar(self) -> None:
        while self.alt_layout.count():
            item = self.alt_layout.takeAt(0)
            if item and item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self.alt_bar.hide()

    def _switch_to_alt_match(self, alt: LookupMatch) -> None:
        if self._current_token is None or self._last_args is None:
            return
        old = self._current_token.lookup
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
        _, dict_pref, already_known, particle_info, sentence, translation = self._last_args
        self._render_active_token(
            self._current_token,
            dict_pref,
            already_known,
            None if self._showing_phrase else particle_info,
            sentence,
            translation,
        )

    def _populate_entries_view(
        self,
        view: _TileScrollView,
        entries: list[DictEntry],
        empty_hint: str,
        particle: ParticleInfo | None = None,
        context_meaning: str = "",
        term: str = "",
        references: tuple[tuple[str, str], ...] = (),
        lesson: VerbLesson | None = None,
        preview: bool = False,
        senses_items: list[SenseItem] | None = None,
        fallback_label: str = "",
        collapsible_more: bool = False,
    ) -> None:
        view.clear()
        p = get_palette(self._theme_name)

        if particle is not None:
            view.add_block(
                _make_particle_card(particle, p),
                f"助詞 [{particle.level}] {particle.role} {particle.meaning}",
            )

        # 1) 片假名外來語：優先以清晰長方形格子顯示英文／外文原詞
        origins = extract_loanword_origins(entries, term, max_items=3) if (term and entries) else []
        if origins:
            origin_widgets = [_make_origin_tile(o, p) for o in origins]
            view.add_flow_tiles(origin_widgets, [f"EN: {o}" for o in origins])

        if senses_items is not None:
            focused = [s.text for s in senses_items if s.text]
            item_by_text = {s.text: s for s in senses_items if s.text}
        else:
            focused = extract_concise_senses(entries, max_senses=6)
            item_by_text = {}

        primary = ""
        is_contextual_override = False
        if particle is None:
            if context_meaning:
                primary = context_meaning
                is_contextual_override = not focused or (
                    context_meaning != focused[0] and not _senses_semantically_overlap(context_meaning, focused[0])
                )
            elif focused:
                primary = focused[0]

        other_senses = [
            s
            for s in focused
            if s and (not primary or (s != primary and not _senses_semantically_overlap(s, primary)))
        ]

        if primary:
            if fallback_label:
                eyebrow = fallback_label
            elif references:
                eyebrow = "本句義"
            elif is_contextual_override:
                eyebrow = "本句義"
            else:
                eyebrow = ""
            view.add_block(
                _make_primary_sense_tile(primary, p, eyebrow=eyebrow),
                f"{eyebrow} {primary}".strip(),
            )

        elif not entries and not senses_items and particle is None and empty_hint:
            view.add_muted_hint(empty_hint, p)

        if lesson:
            card = QLabel(_lesson_label(lesson, p.pill_info_fg, preview))
            card.setWordWrap(True)
            card.linkActivated.connect(lambda _url: self.lesson_requested.emit(lesson))
            view.add_block(card, f"{lesson.lemma} → {lesson.surface}　{lesson.rule}")
        for label, text in references:
            if text:
                if label:
                    view.add_section_header(label, p)
                view.add_muted_hint(text, p)

        # 來源與其他義項：雙語詳情頁預設收折，展開才看次義、來源詞典與繁簡原文
        source_rows: list[str] = []
        seen_src: set[str] = set()
        for s_item in (senses_items or []):
            src_key = s_item.dict_name
            if src_key and src_key not in seen_src:
                seen_src.add(src_key)
                reg_note = f"（{s_item.register}）" if s_item.register else ""
                raw_note = f"｜原文：{s_item.raw_text}" if (s_item.raw_text and s_item.raw_text != s_item.text) else ""
                source_rows.append(f"{s_item.dict_name}{reg_note}{raw_note}")

        if collapsible_more and (other_senses or source_rows):
            extra_count = len(other_senses)
            btn_text = (
                f"展開其他義項與來源（+{extra_count}）"
                if extra_count
                else "展開詞典來源"
            )
            toggle_btn = QPushButton(btn_text)
            toggle_btn.setObjectName("GhostBtn")
            toggle_btn.setCheckable(True)
            toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)

            drawer = QWidget()
            drawer.setStyleSheet("background: transparent;")
            dl = QVBoxLayout(drawer)
            dl.setContentsMargins(0, 2, 0, 0)
            dl.setSpacing(6)
            if other_senses:
                flow = _FlowRowWidget(h_spacing=7, v_spacing=7)
                for s in other_senses[:6]:
                    item = item_by_text.get(s)
                    label_txt = f"{s}（{item.register}）" if (item and item.register and any(t in item.register for t in ("arch", "obs", "hist"))) else s
                    flow.add_tile(_make_sense_tile(label_txt, p))
                dl.addWidget(flow)
            if source_rows:
                src_lbl = QLabel("來源：" + " ／ ".join(source_rows[:3]))
                src_lbl.setWordWrap(True)
                src_lbl.setStyleSheet(f"font-size: 11.5px; color: {p.text_muted}; background: transparent;")
                dl.addWidget(src_lbl)
            drawer.hide()

            def _toggle_drawer(checked: bool, b: QPushButton = toggle_btn, d: QWidget = drawer, base_txt: str = btn_text) -> None:
                d.setVisible(checked)
                b.setText("收合其他義項與來源" if checked else base_txt)

            toggle_btn.toggled.connect(_toggle_drawer)
            view.add_block(toggle_btn)
            view.add_block(drawer)
        elif other_senses:
            if primary:
                view.add_section_header("其他釋義", p)
            tiles = [_make_sense_tile(s, p) for s in other_senses[:5]]
            view.add_flow_tiles(tiles, other_senses[:5])
            if source_rows:
                view.add_muted_hint("來源：" + " ／ ".join(source_rows[:2]), p)

    def set_sentence_analysis(self, analysis: SentenceGrammarAnalysis | None) -> None:
        """更新「句型拆解」分頁：主幹 → 結構分塊格子 → 修飾邏輯鏈 → 命中句型 → 活用拆解。"""
        self._current_analysis = analysis
        self.grammar_view.clear()
        p = get_palette(self._theme_name)
        g_idx = self.tabs.indexOf(self.grammar_view)

        if analysis is None:
            if g_idx >= 0:
                self.tabs.setTabText(g_idx, "句型拆解")
            self.grammar_view.add_muted_hint("收到日語台詞後會自動拆解句子主幹、修飾邏輯與 N5→N1 句型。", p)
            return

        hit_cnt = len(analysis.grammar_hits)
        if g_idx >= 0:
            self.tabs.setTabText(g_idx, f"句型拆解 ({hit_cnt})" if hit_cnt else "句型拆解")

        # 1) 句子主幹（1 秒看懂主語／前件 → 核心結論）
        if analysis.skeleton_predicate:
            spine = QFrame()
            spine.setStyleSheet(
                f"QFrame {{ background: {p.pill_ok_bg}; border: 1px solid {p.border}; "
                f"border-left: 3px solid {p.accent_pink}; border-radius: 7px; }}"
            )
            sv = QVBoxLayout(spine)
            sv.setContentsMargins(11, 8, 11, 8)
            sv.setSpacing(3)

            head = QLabel("句子主幹")
            head.setStyleSheet(
                f"font-size: 11px; font-weight: 700; color: {p.accent_pink}; background: transparent; border: none;"
            )
            sv.addWidget(head)

            topic_s = html.escape(analysis.skeleton_topic or "（省略主語）")
            pred_s = html.escape(analysis.skeleton_predicate)
            main_lbl = QLabel(
                f'<b>{topic_s}</b> <span style="color:{p.accent_pink}; font-weight:700;">──▶</span> <b>{pred_s}</b>'
            )
            main_lbl.setWordWrap(True)
            main_lbl.setTextFormat(Qt.TextFormat.RichText)
            main_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            main_lbl.setStyleSheet(f"font-size: 14px; color: {p.text_primary}; background: transparent; border: none;")
            sv.addWidget(main_lbl)

            if analysis.reading_order_hint:
                hint_lbl = QLabel(analysis.reading_order_hint)
                hint_lbl.setWordWrap(True)
                hint_lbl.setStyleSheet(
                    f"font-size: 12px; color: {p.text_secondary}; background: transparent; border: none;"
                )
                sv.addWidget(hint_lbl)

            self.grammar_view.add_block(
                spine,
                f"主幹: {analysis.skeleton_topic} -> {analysis.skeleton_predicate}\n{analysis.reading_order_hint}",
            )

        # 2) 結構分塊（自適應長方形文節格子）
        if analysis.chunks:
            self.grammar_view.add_section_header("結構分塊", p, subtitle="依語序由左至右")
            chunk_tiles = [_make_chunk_tile(ch, p) for ch in analysis.chunks]
            chunk_plain = [f"[{ch.role_label}] {ch.text} {ch.gloss}".strip() for ch in analysis.chunks]
            self.grammar_view.add_flow_tiles(chunk_tiles, chunk_plain, h_spacing=6, v_spacing=6)

        # 3) 修飾與邏輯鏈（明確指出誰修飾誰）
        if analysis.logic_relations:
            self.grammar_view.add_section_header("修飾與邏輯鏈", p, subtitle="誰修飾誰")
            for rel in analysis.logic_relations:
                self.grammar_view.add_block(
                    _make_relation_card(rel, p),
                    f"{rel.source} --{rel.relation}--> {rel.target} {rel.explanation}".strip(),
                )

        # 4) 命中句型（點擊可跳轉至語法技能樹）
        if analysis.grammar_hits:
            self.grammar_view.add_section_header("命中句型", p)
            for hit in analysis.grammar_hits:
                self.grammar_view.add_block(
                    _make_pattern_card(hit, p, self.blueprint_requested.emit),
                    f"[{hit.level}] {hit.name} {hit.formula} {hit.meaning}",
                )

        for lesson in analysis.verb_lessons:
            card = QLabel(_lesson_label(lesson, p.pill_info_fg))
            card.setWordWrap(True)
            card.linkActivated.connect(lambda _url, target=lesson: self.lesson_requested.emit(target))
            self.grammar_view.add_block(card, f"{lesson.lemma} → {lesson.surface}　{lesson.rule}")

        # 5) 活用拆解（長方形膠囊）
        if analysis.conjugation_notes:
            self.grammar_view.add_section_header("活用拆解", p)
            conj_tiles = [_make_sense_tile(note, p) for note in analysis.conjugation_notes]
            self.grammar_view.add_flow_tiles(conj_tiles, analysis.conjugation_notes)

        # 底部快速開啟完整語法技能樹按鈕
        footer = QWidget()
        footer.setStyleSheet("background: transparent;")
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(0, 2, 0, 0)
        fl.addStretch()
        tree_btn = QPushButton("開啟語法技能樹（173 條 N5→N1）")
        tree_btn.setObjectName("GhostBtn")
        tree_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        tree_btn.clicked.connect(lambda _c=False: self.blueprint_requested.emit(""))
        fl.addWidget(tree_btn)
        self.grammar_view.add_block(footer)

    def set_word_associations(self, tree: WordAssociationTree | None) -> None:
        """更新「關聯詞」分頁：自適應長方形格子，只保留核心重點與熟詞錨點。"""
        self._current_assoc = tree
        self.assoc_view.clear()
        p = get_palette(self._theme_name)
        a_idx = self.tabs.indexOf(self.assoc_view)

        if tree is None or tree.is_empty():
            if a_idx >= 0:
                self.tabs.setTabText(a_idx, "關聯詞")
            self.assoc_view.add_muted_hint("點選單詞後顯示易混辨析、常用複合詞與同字熟詞。", p)
            return

        cnt = tree.total_nodes()
        if a_idx >= 0:
            self.tabs.setTabText(a_idx, f"關聯詞 ({cnt})" if cnt else "關聯詞")

        def _add_group(title: str, nodes: list[RelatedWordNode], subtitle: str = "") -> None:
            if not nodes:
                return
            self.assoc_view.add_section_header(title, p, subtitle=subtitle)
            tiles = [_make_assoc_tile(n, p) for n in nodes]
            plain = [f"{'✓ ' if n.is_known else ''}{n.term} {n.tag} {n.meaning}".strip() for n in nodes]
            self.assoc_view.add_flow_tiles(tiles, plain)

        _add_group("易混辨析", tree.confusables, subtitle="自他動詞／同音異字")
        _add_group("常用複合", tree.compounds)
        fam_sub = " / ".join(tree.kanji_roots[:2]) if tree.kanji_roots else ""
        _add_group("同字詞族", tree.kanji_family, subtitle=fam_sub)

        if tree.game_recurrences:
            self.assoc_view.add_section_header("劇本例句", p)
            for gr in tree.game_recurrences:
                card = QFrame()
                card.setStyleSheet(
                    f"QFrame {{ background: {p.bg_input}; border: 1px solid {p.border}; border-radius: 7px; }}"
                )
                cv = QVBoxLayout(card)
                cv.setContentsMargins(10, 6, 10, 6)
                cv.setSpacing(2)
                spk = f"[{gr.speaker}] " if gr.speaker else ""
                line_lbl = QLabel(f"{spk}{gr.text}")
                line_lbl.setWordWrap(True)
                line_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                line_lbl.setStyleSheet(
                    f"font-size: 13px; color: {p.text_primary}; background: transparent; border: none;"
                )
                cv.addWidget(line_lbl)
                if gr.translation:
                    tr_lbl = QLabel(gr.translation)
                    tr_lbl.setWordWrap(True)
                    tr_lbl.setStyleSheet(
                        f"font-size: 11.5px; color: {p.text_muted}; background: transparent; border: none;"
                    )
                    cv.addWidget(tr_lbl)
                self.assoc_view.add_block(card, f"{spk}{gr.text} {gr.translation}".strip())

    def show_token(
        self,
        token: Token,
        dict_preference: str = "bilingual",
        already_known: bool = False,
        particle_info: ParticleInfo | None = None,
        sentence: str = "",
        translation: str = "",
        phrase_token: Token | None = None,
        tokens=(),
    ) -> None:
        self._sentence_tokens = tokens
        self._single_token = token
        self._phrase_token = phrase_token
        self._showing_phrase = phrase_token is not None
        self._last_args = (token, dict_preference, already_known, particle_info, sentence, translation)
        active_tok = phrase_token if phrase_token is not None else token
        self._render_active_token(
            active_tok,
            dict_preference,
            already_known,
            None if self._showing_phrase else particle_info,
            sentence,
            translation,
        )

    def _render_active_token(
        self,
        token: Token,
        dict_preference: str,
        already_known: bool,
        particle_info: ParticleInfo | None,
        sentence: str,
        translation: str,
    ) -> None:
        self._current_token = token
        self.study_link.bind(token, sentence, self._sentence_tokens, self._showing_phrase, particle_info, translation,
                             get_palette(self._theme_name).pill_info_fg)
        self.mine_btn.setEnabled(True)
        self.more_btn.setEnabled(True)

        p = get_palette(self._theme_name)
        match: LookupMatch | None = token.lookup
        lemma = match.lemma if match else token.lemma
        reading = match.reading if match else token.reading

        self.word_label.setText(lemma)
        self.reading_label.setText(f"「{reading}」" if reading and reading != lemma else "")

        meta_parts: list[str] = []
        if self._showing_phrase:
            meta_parts.append("語塊")
        if match and match.pitches:
            meta_parts.append("[" + "/".join(match.pitches[:3]) + "]")
        if match and match.frequency is not None:
            meta_parts.append(_frequency_hint(match.frequency))
        if already_known:
            meta_parts.append("✓ 已知")
        elif token.is_1t_candidate:
            meta_parts.append("1T")
        self.meta_label.setText(" · ".join(meta_parts))
        self.pitch_button.setVisible(bool(match and match.pitches))
        self.pitch_widget.set_pitch(
            reading=reading or lemma,
            pitches=(match.pitches if match else []),
            dark_mode=(self._theme_name == "starlight"),
        )
        self.pitch_button.setChecked(False)
        self.pitch_widget.hide()

        lesson = lesson_for_token(token) if not particle_info else None
        trace_parts: list[str] = []
        if particle_info:
            trace_parts.append("助詞")
        elif match and match.all_entries:
            pos_friendly = explain_pos_friendly(match.all_entries[0].pos)
            if pos_friendly:
                trace_parts.append(pos_friendly)
        if not lesson and token.surface != lemma:
            trace_parts.append(f"{token.surface} → {lemma}")
        if not lesson and match and match.reasons:
            trace_parts.append(" → ".join(match.reasons))

        if trace_parts:
            self.trace_label.setText(" ｜ ".join(trace_parts))
            self.trace_label.show()
        else:
            self.trace_label.hide()

        self._clear_alt_bar()
        has_alt_widgets = False
        if self._phrase_token is not None and self._single_token is not None:
            if self._showing_phrase:
                sw = QPushButton(f"改看獨立詞：{self._single_token.surface}")
                sw.setStyleSheet("padding: 3px 8px;")
                sw.clicked.connect(lambda _c=False: self._toggle_phrase_mode(False))
            else:
                sw = QPushButton(f"看完整語塊：{self._phrase_token.surface}")
                sw.setStyleSheet("padding: 3px 8px;")
                sw.clicked.connect(lambda _c=False: self._toggle_phrase_mode(True))
            self.alt_layout.addWidget(sw)
            has_alt_widgets = True

        if match and match.alt_matches and particle_info is None:
            alternatives = QPushButton("其他原形")
            alternatives.setObjectName("GhostBtn")
            alternatives_menu = QMenu(alternatives)
            for alt in match.alt_matches[:3]:
                label = f"{alt.lemma}（{alt.reading}）" if alt.reading and alt.reading != alt.lemma else alt.lemma
                alternatives_menu.addAction(label, lambda _checked=False, a=alt: self._switch_to_alt_match(a))
            alternatives.setMenu(alternatives_menu)
            self.alt_layout.addWidget(alternatives)
            has_alt_widgets = True
        if has_alt_widgets:
            self.alt_bar.show()

        self.mine_btn.setText("追加例句" if already_known else "一鍵收錄")

        ctx_meaning, form_note = explain_lookup_in_sentence(token, sentence, translation)
        if match:
            by_lang = match.senses_by_language(max_per_lang=6, convert_tw=True)
            zh_items = by_lang["zh"]
            ja_items = by_lang["ja"]
            en_items = by_lang["en"]
            fallback_label = ""
            bi_display_items = zh_items
            if not zh_items and not context_gloss_is_chinese(ctx_meaning):
                if en_items:
                    bi_display_items = en_items
                    fallback_label = "日英備援"
                elif ja_items:
                    bi_display_items = ja_items
                    fallback_label = "日日備援"
                    if not ctx_meaning:
                        ctx_meaning = ja_items[0].text

            references = (("", form_note.split("\n")[-1] if lesson else form_note),)
            self._populate_entries_view(
                self.bi_view,
                match.bilingual_entries,
                "雙語詞典未收錄此詞，可在「工具 → 詞典管理」檢查。",
                particle=particle_info,
                context_meaning=ctx_meaning,
                term=lemma,
                references=references,
                lesson=lesson,
                preview=token.surface == token.lemma,
                senses_items=bi_display_items,
                fallback_label=fallback_label,
                collapsible_more=True,
            )
            self._populate_entries_view(
                self.mono_view,
                match.monolingual_entries,
                "單語詞典未收錄此詞（可在「工具 → 詞典管理」啟用日日詞典）。",
                particle=particle_info,
                term="",
                senses_items=ja_items,
            )
            self._populate_entries_view(
                self.en_view,
                match.bilingual_entries,
                "日英詞典未收錄此詞（可在「工具 → 詞典管理」設定 JMdict／Jitendex 備援）。",
                particle=None,
                term="",
                senses_items=en_items,
            )
        elif particle_info is not None:
            self._populate_entries_view(self.bi_view, [], "", particle=particle_info,
                                        references=(("", form_note),))
            self._populate_entries_view(self.mono_view, [], "", particle=particle_info)
            self.en_view.clear()
            self.en_view.add_muted_hint("助詞請參考「本句義」分頁的功能與接續說明。", p)
        else:
            hint = "" if ctx_meaning or form_note else f"詞典無「{token.surface}」詞條，可從「⋯」開啟網頁辭典。"
            self._populate_entries_view(self.bi_view, [], hint, lesson=lesson,
                                        preview=token.surface == token.lemma, context_meaning=ctx_meaning,
                                        references=(("", form_note),))
            self.mono_view.clear()
            self.mono_view.add_muted_hint(f"詞典無「{token.surface}」詞條。", p)
            self.en_view.clear()
            self.en_view.add_muted_hint(f"日英詞典無「{token.surface}」詞條。", p)

        if dict_preference == "monolingual" and match and match.monolingual_entries:
            self.tabs.setCurrentIndex(self.tabs.indexOf(self.mono_view))
        else:
            self.tabs.setCurrentIndex(self.tabs.indexOf(self.bi_view))


@lru_cache(maxsize=1)
def _kwin_blur_function():
    # Fedora 的 KF6 提供 C++ 介面；找不到相容符號時保留半透明底色。
    try:
        library = ctypes.CDLL("libKF6WindowSystem.so.6")
        blur = getattr(library, "_ZN14KWindowEffects16enableBlurBehindEP7QWindowbRK7QRegion")
    except (OSError, AttributeError):
        return None
    blur.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_void_p]
    blur.restype = None
    return blur


class OverlayLookupPopup(QFrame):
    """附屬在透明覆蓋層上方的 Yomitan 風格懸停浮動查詞卡（支援外來語英文顯示，置頂於全螢幕 mpv 之上）。"""

    lesson_requested = Signal(object)
    mine_requested = Signal(object)
    copy_prompt_requested = Signal(object)
    audio_preview_requested = Signal()
    hover_entered = Signal()
    hover_left = Signal()

    def __init__(self, theme_name: str = "sakura", parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.ToolTip
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(385)
        self._theme_name = theme_name
        self._token: Token | None = None
        self._single_token: Token | None = None
        self._phrase_token: Token | None = None
        self._showing_phrase: bool = False
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

        self.web_btn = QPushButton("線上辭典")
        self.web_btn.setToolTip("在 GalAnki 內查目前詞語的中文或英文解釋")
        self.web_btn.clicked.connect(self._open_online_dictionary)
        top.addWidget(self.web_btn)
        layout.addLayout(top)

        self.pitch_widget = PitchGraphWidget(
            compact=True,
            dark_mode=(self._theme_name == "starlight"),
        )
        layout.addWidget(self.pitch_widget)

        self.form_label = QLabel()
        self.form_label.setWordWrap(True)
        self.form_label.setTextFormat(Qt.TextFormat.RichText)
        self.form_label.linkActivated.connect(self._open_form)
        self.form_label.hide()
        self._lesson = None
        layout.addWidget(self.form_label)

        self.study_link = StudyLink()
        layout.addWidget(self.study_link)
        self.reason_label = QLabel("")
        self.reason_label.setWordWrap(True)
        layout.addWidget(self.reason_label)

        # 片假名外來語英文／原語小膠囊
        self.origin_label = QLabel("")
        self.origin_label.setWordWrap(True)
        self.origin_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.origin_label.hide()
        layout.addWidget(self.origin_label)

        # 多候選 1 點切換列
        self.alt_bar = QWidget()
        self.alt_bar.setStyleSheet("background: transparent;")
        self.alt_layout = _AdaptiveFlowLayout(self.alt_bar, margin=0, h_spacing=5, v_spacing=4)
        self.alt_layout.setContentsMargins(0, 0, 0, 0)
        self.alt_layout.setSpacing(4)
        self.alt_bar.hide()
        layout.addWidget(self.alt_bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(145)
        self._definition_scroll = scroll
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll.viewport().setStyleSheet("background: transparent; border: none;")
        self.def_label = QLabel("")
        self.def_label.setWordWrap(True)
        self.def_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        scroll.setWidget(self.def_label)
        layout.addWidget(scroll)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self.mine_btn = QPushButton("一鍵收錄")
        self.mine_btn.setObjectName("MineBtn")
        self.mine_btn.setToolTip(
            "左鍵直接寫入 Anki（原聲＋截圖，右下角可撤銷）；按住 Shift 點擊可先編輯"
        )
        self.mine_btn.clicked.connect(self._on_mine)
        btn_row.addWidget(self.mine_btn, 1)

        self.audio_btn = QPushButton("試聽")
        self.audio_btn.setFixedWidth(48)
        self.audio_btn.setToolTip("試聽本句聲優原聲")
        self.audio_btn.clicked.connect(self.audio_preview_requested.emit)
        btn_row.addWidget(self.audio_btn)

        self.prompt_btn = QPushButton("提問")
        self.prompt_btn.setFixedWidth(48)
        self.prompt_btn.setToolTip("複製提問詞給 Gemini / ChatGPT")
        self.prompt_btn.clicked.connect(self._on_copy_prompt)
        btn_row.addWidget(self.prompt_btn)

        layout.addLayout(btn_row)

    def _open_online_dictionary(self) -> None:
        if self._token is None:
            return
        token = self._token
        query = (token.lookup.lemma if token.lookup else token.lemma) or token.surface
        parent = self.parentWidget()
        self.hide()
        show_web_dictionary(query, parent, "kotobank")

    def reset_lookup_area(self) -> None:
        self.setFixedWidth(385)
        self.setMaximumHeight(16777215)
        self._definition_scroll.setMaximumHeight(145)

    def fit_to_lookup_area(self, size: QSize) -> None:
        self.setFixedWidth(size.width())
        self.layout().activate()
        if self.height() > size.height():
            available = size.height() - (self.height() - self._definition_scroll.height())
            self._definition_scroll.setMaximumHeight(max(24, available))
        self.setMaximumHeight(size.height())
        self.adjustSize()

    def _panel_path(self) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 14, 14)
        return path

    def paintEvent(self, event) -> None:
        dark = self._theme_name == "starlight"
        fill = QColor(28, 24, 46, 222) if dark else QColor(255, 252, 254, 222)
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor(get_palette(self._theme_name).accent_pink))
        painter.setBrush(fill)
        painter.drawPath(self._panel_path())
        painter.end()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._update_blur_region()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.isVisible():
            self._update_blur_region()

    def _update_blur_region(self) -> None:
        if QGuiApplication.platformName().startswith("wayland") and self.windowHandle():
            blur = _kwin_blur_function()
            if blur:
                from shiboken6 import getCppPointer
                region = QRegion(self._panel_path().toFillPolygon().toPolygon())
                blur(getCppPointer(self.windowHandle())[0], True, getCppPointer(region)[0])

    def enterEvent(self, event) -> None:  # noqa: N802
        self.hover_entered.emit()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.hover_left.emit()
        super().leaveEvent(event)

    def set_theme_name(self, theme_name: str) -> None:
        self._theme_name = theme_name
        p = get_palette(theme_name)
        self.update()
        self.title_label.setStyleSheet(
            f"font-size: 18px; font-weight: 700; color: {p.accent_pink}; background: transparent;"
        )
        self.reading_label.setStyleSheet(
            f"font-size: 13px; color: {p.accent_lilac}; background: transparent;"
        )
        self.form_label.setStyleSheet(f"background: transparent; color: {p.text_primary};")
        self.reason_label.setStyleSheet(
            f"font-size: 11px; color: {p.text_muted}; background: transparent;"
        )
        self.origin_label.setStyleSheet(
            f"font-size: 12px; font-weight: 700; color: {p.pill_info_fg}; "
            f"background: {p.pill_info_bg}; border: 1px solid {p.accent_lilac}; "
            f"border-radius: 6px; padding: 3px 8px;"
        )
        self.def_label.setStyleSheet(
            f"font-size: 13.5px; line-height: 1.55; color: {p.text_primary}; background: transparent; padding: 2px;"
        )
        self.pitch_widget.set_dark_mode(self._theme_name == "starlight")

    def _open_form(self, _url: str) -> None:
        if self._lesson is not None:
            self.lesson_requested.emit(self._lesson)

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
                item.widget().hide()
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
        _, dict_pref, already_known, particle_info, sentence, translation = self._last_popup_args
        self._render_popup_token(
            self._token,
            dict_pref,
            already_known,
            None if self._showing_phrase else particle_info,
            sentence,
            translation,
            show=self.isVisible(),
        )

    def _toggle_phrase_mode(self, use_phrase: bool) -> None:
        if self._last_popup_args is None or self._single_token is None or self._phrase_token is None:
            return
        self._showing_phrase = bool(use_phrase)
        active = self._phrase_token if self._showing_phrase else self._single_token
        _, dict_pref, already_known, particle_info, sentence, translation = self._last_popup_args
        self._render_popup_token(
            active,
            dict_pref,
            already_known,
            None if self._showing_phrase else particle_info,
            sentence,
            translation,
            show=self.isVisible(),
        )

    def popup_for_token(
        self,
        token: Token,
        dict_preference: str,
        already_known: bool,
        particle_info: ParticleInfo | None = None,
        sentence: str = "",
        translation: str = "",
        *,
        show: bool = True,
        phrase_token: Token | None = None,
        tokens=(),
    ) -> None:
        self._sentence_tokens = tokens
        self._single_token = token
        self._phrase_token = phrase_token
        self._showing_phrase = phrase_token is not None
        self._last_popup_args = (token, dict_preference, already_known, particle_info, sentence, translation)
        active = phrase_token if phrase_token is not None else token
        self._render_popup_token(
            active,
            dict_preference,
            already_known,
            None if self._showing_phrase else particle_info,
            sentence,
            translation,
            show=show,
        )

    def _render_popup_token(
        self,
        token: Token,
        dict_preference: str,
        already_known: bool,
        particle_info: ParticleInfo | None,
        sentence: str,
        translation: str,
        *,
        show: bool = True,
    ) -> None:
        self._token = token
        self.study_link.bind(token, sentence, self._sentence_tokens, self._showing_phrase, particle_info, translation,
                             get_palette(self._theme_name).pill_info_fg)
        match = token.lookup
        lemma = match.lemma if match else token.lemma
        reading = match.reading if match else token.reading

        self.title_label.setText(lemma)
        pitch_txt = f" [{'/'.join(match.pitches[:2])}]" if (match and match.pitches) else ""
        freq_txt = f" {_frequency_hint(match.frequency)}" if (match and match.frequency) else ""
        phrase_tag = "語塊 · " if self._showing_phrase else ""
        self.reading_label.setText(f"{phrase_tag}{reading}{pitch_txt}{freq_txt}")
        self.pitch_widget.set_pitch(
            reading=reading or lemma,
            pitches=(match.pitches if match else []),
            dark_mode=(self._theme_name == "starlight"),
        )

        meta_lines: list[str] = []
        if particle_info is not None:
            meta_lines.append(f"助詞 [{particle_info.level}] {particle_info.role}（{particle_info.formula}）")
        elif match and match.all_entries:
            pos_friendly = explain_pos_friendly(match.all_entries[0].pos)
            if pos_friendly:
                meta_lines.append(pos_friendly)
        if match and match.reasons and token.surface != lemma and not token.is_te_form:
            meta_lines.append(f"{token.surface} → {lemma}（{' → '.join(match.reasons)}）")

        self._lesson = lesson_for_token(token, preview=True) if not particle_info else None
        if self._lesson:
            self.form_label.setText(_lesson_label(self._lesson, get_palette(self._theme_name).pill_info_fg, token.surface == token.lemma))
        self.form_label.setVisible(self._lesson is not None)
        if meta_lines and not self._lesson:
            self.reason_label.setText(" ｜ ".join(meta_lines))
            self.reason_label.show()
        else:
            self.reason_label.hide()

        # 片假名外來語英文原詞標記
        origins = (
            extract_loanword_origins(match.bilingual_entries, lemma, max_items=2)
            if (match and particle_info is None and is_katakana_loanword(lemma))
            else []
        )
        if origins:
            self.origin_label.setText("EN · " + " · ".join(origins))
            self.origin_label.show()
        else:
            self.origin_label.hide()

        self._clear_alts()
        has_alt_widgets = False
        if self._phrase_token is not None and self._single_token is not None:
            if self._showing_phrase:
                sw = QPushButton(f"獨立詞：{self._single_token.surface}")
                sw.setStyleSheet("padding: 3px 8px;")
                sw.clicked.connect(lambda _c=False: self._toggle_phrase_mode(False))
            else:
                sw = QPushButton(f"語塊：{self._phrase_token.surface}")
                sw.setStyleSheet("padding: 3px 8px;")
                sw.clicked.connect(lambda _c=False: self._toggle_phrase_mode(True))
            self.alt_layout.addWidget(sw)
            has_alt_widgets = True

        if match and match.alt_matches and particle_info is None:
            lbl = QLabel("其他：")
            lbl.setStyleSheet("font-size: 11px; opacity: 0.7; background: transparent;")
            self.alt_layout.addWidget(lbl)
            for alt in match.alt_matches[:3]:
                b = QPushButton(alt.lemma)
                b.setStyleSheet("padding: 3px 8px;")
                b.setToolTip(alt.lemma + "（" + alt.reading + "）")
                b.clicked.connect(lambda _c=False, a=alt: self._swap_alt(a))
                self.alt_layout.addWidget(b)
            has_alt_widgets = True
        if has_alt_widgets:
            self.alt_bar.show()

        parts: list[str] = []
        if particle_info is not None:
            _, form_note = explain_lookup_in_sentence(token, sentence, translation)
            parts.append(form_note or particle_info.meaning)
        elif match:
            kind = dict_preference
            by_lang = match.senses_by_language(max_per_lang=3, convert_tw=True)
            base = match.best_concise_meaning(kind, strict=True)
            meaning, form_note = explain_lookup_in_sentence(token, sentence, translation)
            if kind == "monolingual":
                if by_lang["ja"]:
                    parts.append(by_lang["ja"][0].text)
                elif base:
                    parts.append(base)
                else:
                    fb = meaning or (by_lang["zh"][0].text if by_lang["zh"] else (by_lang["en"][0].text if by_lang["en"] else ""))
                    if fb:
                        parts.append("雙語備援")
                        parts.append(fb)
            else:
                has_zh_meaning = bool(by_lang["zh"]) or context_gloss_is_chinese(meaning)
                if has_zh_meaning:
                    parts.append(meaning or by_lang["zh"][0].text)
                elif by_lang["en"]:
                    parts.append("日英備援")
                    parts.append(meaning or by_lang["en"][0].text)
                elif by_lang["ja"]:
                    parts.append("日日備援")
                    parts.append(by_lang["ja"][0].text)
                elif base:
                    parts.append(base)
            if form_note:
                parts.append(form_note.split("\n")[-1] if self._lesson else form_note)
        else:
            meaning, form_note = explain_lookup_in_sentence(token, sentence, translation)
            parts.extend(text for text in (meaning, form_note) if text)
        if not any(parts):
            parts.append("詞典暫無此詞條，可直接收錄原句或複製提問。")

        self.def_label.setText("\n".join(parts))
        self.mine_btn.setText("追加例句（Shift 編輯）" if already_known else "一鍵收錄（Shift 編輯）")
        self.adjustSize()
        if show:
            self.show()
