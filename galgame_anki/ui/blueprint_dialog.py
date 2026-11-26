"""系統化日語學習工作台（收斂為 3 個核心非模態視圖：邏輯拆解工作台 · N5→N1 系統圖譜 · 離線速查表）。"""

from __future__ import annotations

import html
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from galgame_anki.core.grammar_engine import OfflineGrammarEngine
from galgame_anki.core.learning_progress import LearningProgressStore
from galgame_anki.ui.japanese_reference import JapaneseReference
from galgame_anki.ui.study_map import LogicWorkbench
from galgame_anki.ui.theme import apply_soft_bold_document, get_palette, soften_rich_html


class LearningBlueprintDialog(QDialog):
    """非模態 3 視圖日語系統學習工作台（與主視窗／覆蓋層並排，絕不凍結主程式）。"""

    mine_grammar_requested = Signal(dict, str)  # (pattern_dict, real_example_sentence)
    audio_preview_requested = Signal(str)       # sentence
    mine_sentence_requested = Signal(str, str)  # (sentence, lemma)
    queue_sentence_selected = Signal(str, str)  # (sentence, focus_surface)

    def __init__(
        self,
        grammar: OfflineGrammarEngine,
        progress_store: LearningProgressStore,
        known_words_count: int,
        stage_mode: str,
        theme_name: str = "sakura",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("語法與詞彙地圖")
        self.resize(880, 620)
        self.setModal(False)

        self.grammar = grammar
        self.progress_store = progress_store
        self.known_words_count = known_words_count
        self.stage_mode = stage_mode
        self.theme_name = theme_name
        self._progress_map = self.progress_store.get_all_grammar_progress()
        self._selected_pattern: dict[str, Any] | None = None

        self._build_ui()
        self._refresh_overview()
        self._refresh_grammar_list()

    def refresh_progress(
        self,
        known_words_count: int | None = None,
        stage_mode: str | None = None,
    ) -> None:
        """非模態重開或背景進度更新時，即時刷新統計列、句型進度與待拆解隊列。"""
        if known_words_count is not None:
            self.known_words_count = known_words_count
        if stage_mode is not None:
            self.stage_mode = stage_mode
        self.study_map.refresh_queue()
        self._refresh_overview()
        cur_row = self.grammar_list.currentRow()
        self._refresh_grammar_list()
        if 0 <= cur_row < self.grammar_list.count():
            self.grammar_list.setCurrentRow(cur_row)

    def select_pattern(self, pattern_id: str) -> bool:
        """跳轉並選取指定語法句型（從「句型拆解」卡片或工作台點擊進入時使用）。"""
        if not pattern_id:
            return False
        self.tabs.setCurrentWidget(self.grammar_tab)
        self.level_filter.setCurrentIndex(0)
        self.domain_filter.setCurrentIndex(0)
        self.search_edit.clear()
        for row in range(self.grammar_list.count()):
            item = self.grammar_list.item(row)
            pat = item.data(Qt.ItemDataRole.UserRole) if item else None
            if isinstance(pat, dict) and pat.get("id") == pattern_id:
                self.grammar_list.setCurrentRow(row)
                self.grammar_list.scrollToItem(item)
                return True
        return False

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        self.tabs = QTabWidget()

        # 視圖 1：邏輯拆解工作台（整合原「知識地圖」＋「動詞變化比較」＋ 待拆解隊列 ＋ 4 步輕互動 ＋ 單變因實驗室）
        self.study_map = LogicWorkbench(self.progress_store, self.theme_name, grammar=self.grammar)
        self.tabs.addTab(self.study_map, "邏輯拆解工作台")
        self.study_map.pattern_requested.connect(self.select_pattern)
        self.study_map.reference_requested.connect(self.find_reference)
        self.study_map.audio_requested.connect(self.audio_preview_requested.emit)
        self.study_map.mine_sentence_requested.connect(self.mine_sentence_requested.emit)
        self.study_map.queue_sentence_selected.connect(self.queue_sentence_selected.emit)

        # 視圖 2：N5→N1 系統圖譜（整合原「句型索引」＋ 頂部精簡進度總覽列）
        self.grammar_tab = self._build_grammar_tab()
        self.tabs.addTab(self.grammar_tab, "N5→N1 系統圖譜")

        # 視圖 3：離線速查表（整合原「日語總索引」＋「上手與速查」＋ 羅馬音五十音表）
        self.reference = JapaneseReference(self.grammar, self.theme_name)
        self.tabs.addTab(self.reference, "離線速查表")
        self.reference.pattern_requested.connect(self.select_pattern)
        self.reference.lesson_requested.connect(self._open_concept)

        self.reference_shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        self.reference_shortcut.activated.connect(self.find_reference)

        layout.addWidget(self.tabs, 1)

        bottom = QHBoxLayout()
        hint_lbl = QLabel("提示：本視窗為非模態工作台，可放在螢幕旁與覆蓋層同步使用（Ctrl+F 離線速查）。")
        hint_lbl.setObjectName("MutedLabel")
        bottom.addWidget(hint_lbl)
        bottom.addStretch()
        close_btn = QPushButton("關閉")
        close_btn.setObjectName("PrimaryBtn")
        close_btn.clicked.connect(self.close)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

    def find_reference(self, query=None) -> None:
        self.tabs.setCurrentWidget(self.reference)
        if query is not None:
            self.reference.search.setText(query)
        self.reference.search.setFocus()
        self.reference.search.selectAll()

    def _open_concept(self, key: str) -> None:
        self.study_map.select(key)
        self.tabs.setCurrentWidget(self.study_map)

    def _build_grammar_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # 頂部精簡進度總覽列（取代獨立的「學習總覽」分頁，進度與句型學習合一）
        overview_box = QFrame()
        overview_box.setObjectName("CardPanel")
        ol = QVBoxLayout(overview_box)
        ol.setContentsMargins(12, 8, 12, 8)
        ol.setSpacing(6)

        stats_row = QHBoxLayout()
        self.lbl_vocab = QLabel("")
        self.lbl_sentences = QLabel("")
        self.lbl_grammar_total = QLabel("")
        self.lbl_stage_tip = QLabel("")
        self.lbl_stage_tip.setObjectName("MutedLabel")
        for lbl in (self.lbl_vocab, self.lbl_sentences, self.lbl_grammar_total):
            lbl.setStyleSheet("font-size: 12.5px; background: transparent;")
            stats_row.addWidget(lbl)
            stats_row.addSpacing(12)
        stats_row.addStretch()
        stats_row.addWidget(self.lbl_stage_tip)
        ol.addLayout(stats_row)

        bars_row = QHBoxLayout()
        bars_row.setSpacing(8)
        self.level_bars: dict[str, tuple[QLabel, QProgressBar]] = {}
        for lv in ("N5", "N4", "N3", "N2", "N1", "口語"):
            cell = QVBoxLayout()
            cell.setSpacing(2)
            top = QHBoxLayout()
            lv_lbl = QLabel(lv)
            lv_lbl.setStyleSheet("font-weight: 500; font-size: 11px; background: transparent;")
            cnt_lbl = QLabel("")
            cnt_lbl.setObjectName("MutedLabel")
            cnt_lbl.setStyleSheet("font-size: 10.5px; background: transparent;")
            top.addWidget(lv_lbl)
            top.addStretch()
            top.addWidget(cnt_lbl)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setMaximumHeight(8)
            bar.setTextVisible(False)
            cell.addLayout(top)
            cell.addWidget(bar)
            self.level_bars[lv] = (cnt_lbl, bar)
            bars_row.addLayout(cell, 1)
        ol.addLayout(bars_row)
        layout.addWidget(overview_box)

        # 篩選列
        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)

        self.level_filter = QComboBox()
        self.level_filter.addItem("全部等級", "ALL")
        for lv in ("N5", "N4", "N3", "N2", "N1", "口語"):
            self.level_filter.addItem(lv, lv)
        self.level_filter.addItem("已遇見", "SEEN")
        self.level_filter.addItem("未掌握", "UNMASTERED")
        self.level_filter.currentIndexChanged.connect(self._refresh_grammar_list)
        filter_row.addWidget(self.level_filter)

        self.domain_filter = QComboBox()
        self.domain_filter.addItem("全部領域", "ALL")
        for dom in self.grammar.domains:
            self.domain_filter.addItem(dom, dom)
        self.domain_filter.currentIndexChanged.connect(self._refresh_grammar_list)
        filter_row.addWidget(self.domain_filter)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜尋句型、接續或中文關鍵字…")
        self.search_edit.textChanged.connect(self._refresh_grammar_list)
        filter_row.addWidget(self.search_edit, 1)

        self.match_count_label = QLabel("")
        self.match_count_label.setObjectName("MutedLabel")
        filter_row.addWidget(self.match_count_label)
        layout.addLayout(filter_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.grammar_list = QListWidget()
        self.grammar_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.grammar_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.grammar_list.currentRowChanged.connect(self._on_grammar_selected)
        splitter.addWidget(self.grammar_list)

        detail_box = QFrame()
        detail_box.setObjectName("CardPanel")
        dl = QVBoxLayout(detail_box)
        dl.setContentsMargins(14, 12, 14, 12)
        dl.setSpacing(8)

        self.grammar_detail = QTextBrowser()
        apply_soft_bold_document(self.grammar_detail)
        dl.addWidget(self.grammar_detail, 1)

        act_row = QHBoxLayout()
        self.mark_mastered_btn = QPushButton("標記已掌握")
        self.mark_mastered_btn.setEnabled(False)
        self.mark_mastered_btn.clicked.connect(self._toggle_mastered)
        act_row.addWidget(self.mark_mastered_btn)

        self.mine_grammar_btn = QPushButton("收錄語法卡")
        self.mine_grammar_btn.setObjectName("MineBtn")
        self.mine_grammar_btn.setEnabled(False)
        self.mine_grammar_btn.clicked.connect(self._mine_selected_grammar)
        act_row.addWidget(self.mine_grammar_btn)
        act_row.addStretch()
        dl.addLayout(act_row)

        splitter.addWidget(detail_box)
        splitter.setSizes([330, 490])
        layout.addWidget(splitter, 1)
        return tab

    def _refresh_overview(self) -> None:
        self._progress_map = self.progress_store.get_all_grammar_progress()
        sentences_read = self.progress_store.get_sentences_read()
        seen_grammar = sum(1 for v in self._progress_map.values() if v.get("seen_count", 0) > 0)
        mastered_grammar = sum(1 for v in self._progress_map.values() if v.get("status") == "mastered")
        total_grammar = len(self.grammar.patterns)

        self.lbl_vocab.setText(soften_rich_html(f"Anki 已知詞：<b>{self.known_words_count:,}</b>"))
        self.lbl_sentences.setText(soften_rich_html(f"已讀台詞：<b>{sentences_read:,}</b> 句"))
        self.lbl_grammar_total.setText(soften_rich_html(f"語法進度：<b>{seen_grammar}/{total_grammar}</b>（掌握 {mastered_grammar}）"))

        stage_short = {
            "lv1": "Lv.1 破冰期：主攻 N5＋口語縮略",
            "lv2": "Lv.2 沉浸期：主攻 1T 句卡＋N4/N3",
            "lv3": "Lv.3 單語期：主攻日日釋義＋N2/N1",
        }
        self.lbl_stage_tip.setText(stage_short.get(self.stage_mode, stage_short["lv2"]))

        for lv, (count_lbl, bar) in self.level_bars.items():
            lv_pats = [p for p in self.grammar.patterns if p["level"] == lv]
            tot = max(1, len(lv_pats))
            seen = sum(
                1 for p in lv_pats
                if p["id"] in self._progress_map and self._progress_map[p["id"]].get("seen_count", 0) > 0
            )
            mast = sum(
                1 for p in lv_pats
                if p["id"] in self._progress_map and self._progress_map[p["id"]].get("status") == "mastered"
            )
            bar.setValue(int((seen * 0.5 + mast * 0.5) * 100 / tot))
            count_lbl.setText(f"{seen}/{tot}·懂{mast}")

    def _refresh_grammar_list(self) -> None:
        self._progress_map = self.progress_store.get_all_grammar_progress()
        lv_filt = str(self.level_filter.currentData() or "ALL")
        dom_filt = str(self.domain_filter.currentData() or "ALL")
        kw = self.search_edit.text().strip().lower()
        self.grammar_list.clear()

        shown = 0
        for pat in self.grammar.patterns:
            prog = self._progress_map.get(pat["id"], {})
            seen_cnt = int(prog.get("seen_count", 0))
            status = str(prog.get("status", "new"))

            if lv_filt == "SEEN" and seen_cnt == 0:
                continue
            if lv_filt == "UNMASTERED" and status == "mastered":
                continue
            if lv_filt in ("N5", "N4", "N3", "N2", "N1", "口語") and pat["level"] != lv_filt:
                continue
            if dom_filt != "ALL" and pat.get("category") != dom_filt:
                continue
            if kw:
                hay = f"{pat['name']} {pat['formula']} {pat['meaning']} {pat['example']} {pat['category']}".lower()
                if kw not in hay:
                    continue

            if status == "mastered":
                mark = "✓"
            elif seen_cnt > 0:
                mark = f"●{seen_cnt}"
            else:
                mark = "○"

            item = QListWidgetItem(f"{mark:3s} [{pat['level']}] {pat['name']}")
            item.setData(Qt.ItemDataRole.UserRole, pat)
            self.grammar_list.addItem(item)
            shown += 1

        self.match_count_label.setText(f"{shown} / {len(self.grammar.patterns)}")
        if self.grammar_list.count() > 0:
            self.grammar_list.setCurrentRow(0)

    def _on_grammar_selected(self, row: int) -> None:
        if row < 0:
            return
        item = self.grammar_list.item(row)
        if not item:
            return
        pat = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(pat, dict):
            return
        self._selected_pattern = pat
        self.mark_mastered_btn.setEnabled(True)
        self.mine_grammar_btn.setEnabled(True)

        prog = self._progress_map.get(pat["id"], {})
        status = prog.get("status", "new")
        seen_cnt = int(prog.get("seen_count", 0))
        last_ex = str(prog.get("last_example") or "")
        last_src = str(prog.get("last_source") or "")

        self.mark_mastered_btn.setText("取消已掌握" if status == "mastered" else "標記已掌握")

        p = get_palette(self.theme_name)
        seen_html = ""
        if last_ex:
            src_txt = f" · {html.escape(last_src)}" if last_src else ""
            seen_html = (
                f'<div style="margin-top:10px; padding:9px 12px; background:{p.pill_ok_bg}; '
                f'color:{p.pill_ok_fg}; border-radius:8px;">'
                f'<div style="font-size:12px; font-weight:500; margin-bottom:2px;">實戰遇見 {seen_cnt} 次{src_txt}</div>'
                f'<div style="font-size:14.5px;">{html.escape(last_ex)}</div>'
                f"</div>"
            )

        self.grammar_detail.setHtml(
            f'<h3 style="color:{p.accent_pink}; margin:0 0 4px 0;">[{html.escape(pat["level"])}] {html.escape(pat["name"])}</h3>'
            f'<div style="font-size:12px; color:{p.text_muted}; margin-bottom:10px;">{html.escape(pat["category"])}</div>'
            f'<div style="padding:8px 12px; background:{p.bg_input}; border-radius:8px; margin-bottom:8px;">'
            f'<b>接續：</b>{html.escape(pat["formula"])}</div>'
            f'<div style="line-height:1.7; margin-bottom:8px;"><b>用法：</b>{html.escape(pat["meaning"])}</div>'
            f'<div style="line-height:1.7;"><b>例句：</b>{html.escape(pat["example"])}</div>'
            f"{seen_html}"
        )

    def _toggle_mastered(self) -> None:
        if not self._selected_pattern:
            return
        pid = self._selected_pattern["id"]
        lv = self._selected_pattern["level"]
        cur = self._progress_map.get(pid, {}).get("status", "new")
        new_status = "seen" if cur == "mastered" else "mastered"
        self.progress_store.set_grammar_status(pid, lv, new_status)
        row = self.grammar_list.currentRow()
        self._refresh_overview()
        self._refresh_grammar_list()
        if 0 <= row < self.grammar_list.count():
            self.grammar_list.setCurrentRow(row)

    def _mine_selected_grammar(self) -> None:
        if not self._selected_pattern:
            return
        prog = self._progress_map.get(self._selected_pattern["id"], {})
        example = str(prog.get("last_example") or self._selected_pattern["example"])
        self.mine_grammar_requested.emit(self._selected_pattern, example)
