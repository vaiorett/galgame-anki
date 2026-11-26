"""4 步主動邏輯拆解工作台（含待拆解難句隊列、3 秒輕互動預判、單變因實驗室與口語回應練習）。"""
from __future__ import annotations

from html import escape
from typing import Any

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from galgame_anki.core.study_map import CONCEPTS
from galgame_anki.core.study_steps import (
    EXAMPLES,
    REQUIRES,
    LogicPipeline,
    MutationVariant,
    build_logic_pipeline,
)
from galgame_anki.ui.theme import RICHTEXT_WEIGHT_CSS, get_palette, soften_rich_html
from galgame_anki.ui.web_dictionary import show_web_dictionary


PATTERNS = {
    "ability": "n4_potential",
    "state": "n5_teiru",
    "completion": "n4_teshimau",
    "prepare": "n4_teoku",
    "condition": "n4_tara",
    "connect": "n5_te_kudasai",
    "stance": "n5_noda",
    "trend": "n3_ippouda",
}


class LogicWorkbench(QWidget):
    """邏輯拆解工作台：待拆解隊列 ＋ 17 概念樹 ＋ 4 步 3 秒輕互動測驗 ＋ 單變因實驗室 ＋ 口語回應提示。"""

    pattern_requested = Signal(str)
    reference_requested = Signal(str)
    audio_requested = Signal(str)            # sentence
    mine_sentence_requested = Signal(str, str)  # (sentence, lemma)
    queue_sentence_selected = Signal(str, str)  # (sentence, focus_surface)

    def __init__(self, store, theme_name: str = "sakura", grammar=None) -> None:
        super().__init__()
        self.store = store
        self.grammar = grammar
        self.theme_name = theme_name
        self._palette = get_palette(theme_name)

        self.focus = None
        self.sentence = ""
        self.lemma = ""
        self._token = None
        self._tokens: tuple[Any, ...] = ()
        self._analysis = None
        self._pipeline: LogicPipeline | None = None
        self._active_mutation: MutationVariant | None = None
        self._quiz_answered: dict[int, int] = {}  # step_index -> chosen option idx
        self._active_queue_id: int | None = None

        self.step = 0
        self.walk = EXAMPLES["roles"]
        self.show_relations = False
        self.revealed = False
        self.review = False
        self.saved_index = 0

        self._build_ui()
        self.refresh_queue()
        self.select("roles")

    def _build_ui(self) -> None:
        p = self._palette
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        split = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(split, 1)

        # === 左側：待拆解難句隊列 ＋ 先備邏輯概念樹 ===
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        q_header = QHBoxLayout()
        q_header.setSpacing(4)
        self.queue_title_label = QLabel("待拆解難句 (0)")
        self.queue_title_label.setStyleSheet(f"font-weight: 500; font-size: 12px; color: {p.accent_pink};")
        q_header.addWidget(self.queue_title_label)
        q_header.addStretch()
        self.queue_done_btn = QPushButton("已拆完")
        self.queue_done_btn.setToolTip("將目前選中的待拆解句子標記為已完成")
        self.queue_done_btn.setStyleSheet("padding: 2px 8px; font-size: 11px;")
        self.queue_done_btn.clicked.connect(self._mark_current_queue_done)
        self.queue_done_btn.setVisible(False)
        q_header.addWidget(self.queue_done_btn)
        left_layout.addLayout(q_header)

        self.queue_list = QListWidget()
        self.queue_list.setMaximumHeight(118)
        self.queue_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.queue_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.queue_list.setAccessibleName("待拆解難句隊列")
        self.queue_list.itemClicked.connect(self._on_queue_item_clicked)
        left_layout.addWidget(self.queue_list)

        tree_lbl = QLabel("邏輯概念先備樹")
        tree_lbl.setStyleSheet(f"font-weight: 500; font-size: 12px; color: {p.text_muted}; margin-top: 2px;")
        left_layout.addWidget(tree_lbl)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.setAccessibleName("語法與詞彙的先備關係")
        self.tree.setIndentation(13)
        self.tree.setMinimumWidth(160)
        left_layout.addWidget(self.tree, 1)

        self.nodes: dict[str, QTreeWidgetItem] = {}
        branches: dict[str, QTreeWidgetItem] = {}
        for key, item in CONCEPTS.items():
            if item.branch not in branches:
                branch = QTreeWidgetItem(self.tree, [item.branch])
                branch.setFlags(branch.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                branches[item.branch] = branch
            parent = self.nodes[item.parent] if item.parent else branches[item.branch]
            node = QTreeWidgetItem(parent, [item.title])
            node.setData(0, Qt.ItemDataRole.UserRole, key)
            self.nodes[key] = node
        self.tree.expandAll()
        split.addWidget(left_panel)

        # === 右側：頂部動作列 ＋ 3 秒輕互動測驗卡 ＋ 單變因實驗室 ＋ 拆解詳解區 ===
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)

        top_bar = QHBoxLayout()
        top_bar.setSpacing(6)
        self.expand_all_btn = QPushButton("全部展開")
        self.expand_all_btn.setCheckable(True)
        self.expand_all_btn.setToolTip("一鍵展開完整 4 步推導，或切回 3 秒輕互動預判模式")
        self.expand_all_btn.toggled.connect(self._on_expand_toggled)
        top_bar.addWidget(self.expand_all_btn)

        self.audio_btn = QPushButton("聽原聲")
        self.audio_btn.setToolTip("重播目前句子的聲優原聲（mpv 或純人聲包）")
        self.audio_btn.clicked.connect(lambda: self.sentence and self.audio_requested.emit(self.sentence))
        top_bar.addWidget(self.audio_btn)

        self.mine_btn = QPushButton("收錄至 Anki")
        self.mine_btn.setObjectName("MineBtn")
        self.mine_btn.clicked.connect(lambda: self.sentence and self.mine_sentence_requested.emit(self.sentence, self.lemma))
        top_bar.addWidget(self.mine_btn)

        self.web_dict_btn = QPushButton("線上辭典")
        self.web_dict_btn.setToolTip("以目前焦點詞開啟線上辭典核對（コトバンク / 時雨 / Jisho）")
        self.web_dict_btn.clicked.connect(self._open_web_dict)
        top_bar.addWidget(self.web_dict_btn)
        top_bar.addStretch()
        right_layout.addLayout(top_bar)

        # 3 秒輕互動小測驗區（每步 3 選 1，點一下即核對，無彈窗干擾）
        self.quiz_frame = QFrame()
        self.quiz_frame.setObjectName("CardPanel")
        qf_layout = QVBoxLayout(self.quiz_frame)
        qf_layout.setContentsMargins(10, 8, 10, 8)
        qf_layout.setSpacing(5)

        self.quiz_question_label = QLabel("")
        self.quiz_question_label.setWordWrap(True)
        self.quiz_question_label.setStyleSheet(f"font-weight: 500; font-size: 12.5px; color: {p.text_primary}; background: transparent;")
        qf_layout.addWidget(self.quiz_question_label)

        opts_row = QHBoxLayout()
        opts_row.setSpacing(6)
        self.quiz_buttons: list[QPushButton] = []
        for idx in range(3):
            btn = QPushButton("")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, i=idx: self._answer_quiz(i))
            opts_row.addWidget(btn, 1)
            self.quiz_buttons.append(btn)
        qf_layout.addLayout(opts_row)

        self.quiz_feedback_label = QLabel("")
        self.quiz_feedback_label.setWordWrap(True)
        self.quiz_feedback_label.setStyleSheet(f"font-size: 12px; color: {p.text_secondary}; background: transparent;")
        self.quiz_feedback_label.setVisible(False)
        qf_layout.addWidget(self.quiz_feedback_label)
        right_layout.addWidget(self.quiz_frame)

        # 單變因實驗室（點擊任一變因按鈕，立即呈現原句 vs 只改 1 個變因後的對比）
        self.mutation_frame = QFrame()
        self.mutation_frame.setObjectName("CardPanel")
        mf_layout = QVBoxLayout(self.mutation_frame)
        mf_layout.setContentsMargins(10, 7, 10, 7)
        mf_layout.setSpacing(5)

        self.mutation_header_label = QLabel("單變因實驗室（點任一變因，只改 1 個條件比較句意位移）")
        self.mutation_header_label.setStyleSheet(
            f"font-weight: 500; font-size: 11.5px; color: {p.accent_lilac}; background: transparent;"
        )
        mf_layout.addWidget(self.mutation_header_label)

        self.mutation_grid = QGridLayout()
        self.mutation_grid.setSpacing(5)
        mf_layout.addLayout(self.mutation_grid)
        self.mutation_buttons: list[QPushButton] = []

        self.mutation_result_label = QLabel("")
        self.mutation_result_label.setWordWrap(True)
        self.mutation_result_label.setTextFormat(Qt.TextFormat.RichText)
        self.mutation_result_label.setStyleSheet(f"font-size: 12.5px; color: {p.text_primary}; background: transparent; padding-top: 2px;")
        self.mutation_result_label.setVisible(False)
        mf_layout.addWidget(self.mutation_result_label)
        right_layout.addWidget(self.mutation_frame)

        # 主要拆解詳解與口語提示閱讀器
        self.reader = QTextBrowser()
        self.reader.document().setDefaultStyleSheet(
            f"{RICHTEXT_WEIGHT_CSS} a {{ color: {p.pill_info_fg}; }} h2, h3 {{ color: {p.accent_lilac}; }}"
        )
        self.reader.document().setDocumentMargin(10)
        self.reader.setOpenLinks(False)
        self.reader.anchorClicked.connect(self.follow)
        right_layout.addWidget(self.reader, 1)

        split.addWidget(right_panel)
        split.setSizes([230, 600])
        split.setStretchFactor(1, 1)

        # 底部步驟導覽與個人例句複習列
        navigation = QHBoxLayout()
        layout.addLayout(navigation)
        self.previous_step = QPushButton("上一步")
        self.previous_step.clicked.connect(lambda: self.move_step(-1))
        navigation.addWidget(self.previous_step)
        self.step_label = QLabel()
        navigation.addWidget(self.step_label)
        navigation.addStretch()
        self.next_step = QPushButton("下一步")
        self.next_step.setObjectName("PrimaryBtn")
        self.next_step.clicked.connect(lambda: self.move_step(1))
        navigation.addWidget(self.next_step)

        bottom = QHBoxLayout()
        layout.addLayout(bottom)
        self.recall = QPushButton("核對答案")
        self.recall.clicked.connect(self.toggle_answer)
        bottom.addWidget(self.recall)
        self.save = QPushButton("留作我的例句")
        self.save.clicked.connect(self.keep)
        bottom.addWidget(self.save)
        self.review_btn = QPushButton("重溫例句")
        self.review_btn.clicked.connect(self.toggle_review)
        bottom.addWidget(self.review_btn)
        self.more = QPushButton("下一句")
        self.more.clicked.connect(self.next_example)
        bottom.addWidget(self.more)
        bottom.addStretch()

        self.tree.currentItemChanged.connect(self.changed)

    def refresh_queue(self) -> None:
        """重新載入待拆解難句隊列。"""
        self.queue_list.clear()
        if not hasattr(self.store, "list_study_queue"):
            return
        items = self.store.list_study_queue(include_done=False, limit=40)
        self.queue_title_label.setText(f"待拆解難句 ({len(items)})")
        if not items:
            ph = QListWidgetItem("Shift+點「拆解」可先暫存難句")
            ph.setFlags(Qt.ItemFlag.NoItemFlags)
            self.queue_list.addItem(ph)
            self.queue_done_btn.setVisible(False)
            self._active_queue_id = None
            return
        for row in items:
            sent = str(row.get("sentence") or "")
            focus_s = str(row.get("focus_surface") or "")
            preview = sent if len(sent) <= 18 else sent[:17] + "…"
            label = f"[{focus_s}] {preview}" if focus_s else preview
            li = QListWidgetItem(label)
            li.setToolTip(sent)
            li.setData(Qt.ItemDataRole.UserRole, row)
            self.queue_list.addItem(li)
        self.queue_done_btn.setVisible(self._active_queue_id is not None)

    def _on_queue_item_clicked(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict):
            return
        self._active_queue_id = int(data.get("id") or 0) or None
        self.queue_done_btn.setVisible(self._active_queue_id is not None)
        sent = str(data.get("sentence") or "")
        focus_s = str(data.get("focus_surface") or "")
        if sent:
            self.queue_sentence_selected.emit(sent, focus_s)

    def _mark_current_queue_done(self) -> None:
        if self._active_queue_id and hasattr(self.store, "mark_study_queue_done"):
            self.store.mark_study_queue_done(self._active_queue_id, done=True)
            self._active_queue_id = None
            self.refresh_queue()

    def set_theme_name(self, theme_name: str) -> None:
        self.theme_name = theme_name
        self._palette = get_palette(theme_name)
        p = self._palette
        self.queue_title_label.setStyleSheet(f"font-weight: 500; font-size: 12px; color: {p.accent_pink};")
        self.quiz_question_label.setStyleSheet(
            f"font-weight: 500; font-size: 12.5px; color: {p.text_primary}; background: transparent;"
        )
        self.mutation_header_label.setStyleSheet(
            f"font-weight: 500; font-size: 11.5px; color: {p.accent_lilac}; background: transparent;"
        )
        self.mutation_result_label.setStyleSheet(
            f"font-size: 12.5px; color: {p.text_primary}; background: transparent; padding-top: 2px;"
        )
        self.reader.document().setDefaultStyleSheet(
            f"{RICHTEXT_WEIGHT_CSS} a {{ color: {p.pill_info_fg}; }} h2, h3 {{ color: {p.accent_lilac}; }}"
        )
        self.render()

    def _open_web_dict(self) -> None:
        word = self.lemma or (self._token.surface if self._token is not None else "")
        if word:
            show_web_dictionary(word, self, "kotobank")

    def select(self, key: str) -> None:
        if key in self.nodes:
            self.tree.setCurrentItem(self.nodes[key])

    def selected_key(self):
        current = self.tree.currentItem()
        return current.data(0, Qt.ItemDataRole.UserRole) if current else None

    def show_focus(
        self,
        focus,
        sentence: str,
        lemma: str,
        *,
        token=None,
        tokens=(),
        analysis=None,
    ) -> None:
        self.focus = focus
        self.sentence = sentence
        self.lemma = lemma
        self._token = token
        self._tokens = tuple(tokens or ())
        self._analysis = analysis
        self._quiz_answered.clear()
        self._active_mutation = None
        self._pipeline = build_logic_pipeline(
            sentence=sentence,
            token=token,
            tokens=self._tokens,
            focus=focus,
            analysis=analysis,
            concept_key=focus.key if focus else "",
            lemma=lemma,
        )
        self.select(focus.key)
        self.changed()

    def move_step(self, direction: int) -> None:
        self.step = max(0, min(self.step + direction, len(self.walk.steps)))
        self.revealed = False
        self.render()

    def changed(self) -> None:
        self.step = 0
        self.show_relations = False
        self.revealed = self.review = False
        self.saved_index = 0
        self._quiz_answered.clear()
        self._active_mutation = None
        self.render()

    def _on_expand_toggled(self, checked: bool) -> None:
        self.expand_all_btn.setText("切回 3 秒輕互動" if checked else "全部展開")
        self.render()

    def toggle_answer(self) -> None:
        self.revealed = not self.revealed
        self.render()
        if self.revealed:
            self.reader.scrollToAnchor("answer")

    def keep(self) -> None:
        key = self.selected_key()
        if self.focus and self.sentence and key in (self.focus.key, self.focus.lexical_key):
            self.store.keep_study_example(key, self.lemma, self.sentence, self.detail(key))
            self.render()

    def detail(self, key: str) -> str:
        if self.focus.key == key:
            return self.focus.evidence + (
                "　·　" + self.focus.vocabulary if self.focus.vocabulary != self.focus.evidence else ""
            )
        return self.focus.vocabulary

    def toggle_review(self) -> None:
        self.review = not self.review
        self.saved_index = 0
        self.revealed = False
        self.render()

    def next_example(self) -> None:
        self.saved_index += 1
        self.revealed = False
        self.render()

    def _ensure_pipeline_for_key(self, key: str, matches: bool) -> LogicPipeline:
        if matches and self._pipeline is not None and self._pipeline.sentence == self.sentence:
            return self._pipeline
        item = CONCEPTS[key]
        return build_logic_pipeline(sentence=item.first, concept_key=key)

    def _update_quiz_widget(self, pipeline: LogicPipeline) -> None:
        show_quiz = (not self.review) and (not self.expand_all_btn.isChecked()) and bool(pipeline.steps)
        self.quiz_frame.setVisible(show_quiz)
        if not show_quiz:
            return
        step_idx = min(self.step, len(pipeline.steps) - 1)
        lstep = pipeline.steps[step_idx]
        self.quiz_question_label.setText(f"[{lstep.title} · 3 秒預判] {lstep.question}")
        chosen = self._quiz_answered.get(step_idx)
        p = self._palette

        for i, btn in enumerate(self.quiz_buttons):
            if i < len(lstep.options):
                opt = lstep.options[i]
                btn.setVisible(True)
                btn.setText(opt.label.replace("&", "&&"))
                if chosen is None:
                    btn.setStyleSheet("")
                elif i == chosen and opt.is_correct:
                    btn.setStyleSheet(
                        f"background-color: {p.pill_ok_bg}; color: {p.pill_ok_fg}; "
                        f"border: 1px solid {p.pill_ok_fg}; font-weight: 500;"
                    )
                elif i == chosen and not opt.is_correct:
                    btn.setStyleSheet(
                        f"background-color: {p.pill_warn_bg}; color: {p.pill_warn_fg}; "
                        f"border: 1px solid {p.pill_warn_fg}; font-weight: 500;"
                    )
                elif opt.is_correct:
                    btn.setStyleSheet(
                        f"background-color: {p.pill_ok_bg}; color: {p.pill_ok_fg}; border: 1px dashed {p.pill_ok_fg};"
                    )
                else:
                    btn.setStyleSheet("")
            else:
                btn.setVisible(False)

        if chosen is not None and 0 <= chosen < len(lstep.options):
            picked = lstep.options[chosen]
            prefix = "✓ " if picked.is_correct else "→ "
            fg = p.pill_ok_fg if picked.is_correct else p.pill_warn_fg
            self.quiz_feedback_label.setStyleSheet(f"font-size: 12px; color: {fg}; font-weight: 500; background: transparent;")
            self.quiz_feedback_label.setText(f"{prefix}{picked.feedback}")
            self.quiz_feedback_label.setVisible(True)
        else:
            self.quiz_feedback_label.setVisible(False)

    def _answer_quiz(self, option_idx: int) -> None:
        key = self.selected_key()
        if not key:
            return
        matches = bool(self.sentence and self.focus and key in (self.focus.key, self.focus.lexical_key))
        pipeline = self._ensure_pipeline_for_key(key, matches)
        step_idx = min(self.step, len(pipeline.steps) - 1)
        self._quiz_answered[step_idx] = option_idx
        self._update_quiz_widget(pipeline)

    def _update_mutation_widget(self, pipeline: LogicPipeline) -> None:
        muts = pipeline.mutations
        show_mut = (not self.review) and bool(muts)
        self.mutation_frame.setVisible(show_mut)
        if not show_mut:
            return
        while self.mutation_grid.count():
            item = self.mutation_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self.mutation_buttons.clear()
        p = self._palette

        for idx, mut in enumerate(muts):
            btn = QPushButton(mut.label.replace("&", "&&"))
            btn.setCheckable(True)
            is_sel = self._active_mutation is not None and self._active_mutation.key == mut.key
            btn.setChecked(is_sel)
            if is_sel:
                btn.setStyleSheet(
                    f"background-color: {p.pill_info_bg}; color: {p.pill_info_fg}; "
                    f"border: 1px solid {p.accent_pink}; font-weight: 500; padding: 3px 8px; font-size: 11.5px;"
                )
            else:
                btn.setStyleSheet("padding: 3px 8px; font-size: 11.5px;")
            btn.clicked.connect(lambda _=False, m=mut: self._select_mutation(m))
            self.mutation_grid.addWidget(btn, idx // 3, idx % 3)
            self.mutation_buttons.append(btn)

        if self._active_mutation is not None and any(m.key == self._active_mutation.key for m in muts):
            m = self._active_mutation
            self.mutation_result_label.setText(
                soften_rich_html(
                    f"<b>原句：</b>{escape(m.before_sentence)}　<span style='color:{p.accent_pink}; font-weight:500;'>⇒</span>　"
                    f"<b>改後：</b><span style='color:{p.accent_pink}; font-weight:500;'>{escape(m.after_sentence)}</span>"
                    f"<br><span style='color:{p.text_secondary}; font-size:11.5px;'>{escape(m.diff_note)}</span>"
                )
            )
            self.mutation_result_label.setVisible(True)
        else:
            self.mutation_result_label.setVisible(False)

    def _select_mutation(self, variant: MutationVariant) -> None:
        if self._active_mutation is not None and self._active_mutation.key == variant.key:
            self._active_mutation = None
        else:
            self._active_mutation = variant
        key = self.selected_key()
        if not key:
            return
        matches = bool(self.sentence and self.focus and key in (self.focus.key, self.focus.lexical_key))
        pipeline = self._ensure_pipeline_for_key(key, matches)
        self._update_mutation_widget(pipeline)

    def render(self) -> None:
        key = self.selected_key()
        if not key or key not in CONCEPTS:
            return
        item = CONCEPTS[key]
        examples = self.store.study_examples(key)
        matches = bool(self.sentence and self.focus and key in (self.focus.key, self.focus.lexical_key))
        pipeline = self._ensure_pipeline_for_key(key, matches)

        self.audio_btn.setEnabled(bool(self.sentence))
        self.mine_btn.setEnabled(bool(self.sentence))
        self.web_dict_btn.setEnabled(bool(self.lemma or (self._token is not None)))

        self.save.setVisible(matches and not self.review)
        saved = any(r["lemma"] == self.lemma and r["sentence"] == self.sentence for r in examples)
        self.save.setEnabled(not saved)
        self.save.setText("已留作例句" if saved else "留作我的例句")
        self.review_btn.setVisible(bool(examples))
        self.review_btn.setText("返回解說" if self.review else f"重溫例句（{len(examples)}）")
        self.more.setVisible(self.review and len(examples) > 1)
        self.recall.setText("收起答案" if self.revealed else "核對答案")

        live = (self.focus.walk if key == self.focus.key else self.focus.lexical_walk) if matches else None
        self.walk = live or EXAMPLES[key]
        count = len(self.walk.steps)
        summary = self.step == count
        expand_all = self.expand_all_btn.isChecked()

        self.previous_step.setVisible(not self.review and not expand_all)
        self.previous_step.setEnabled(self.step > 0)
        self.step_label.setVisible(not self.review and not expand_all)
        self.step_label.setText("綜合練習" if summary else f"第 {self.step + 1} 步，共 {count} 步")
        self.next_step.setVisible(not self.review and not summary and not expand_all)
        if not summary:
            self.next_step.setText("比較與練習" if self.step == count - 1 else "用這個結果，走下一步")
        self.recall.setVisible(self.review or summary)

        self._update_quiz_widget(pipeline)
        self._update_mutation_widget(pipeline)

        p = self._palette
        if self.review and examples:
            row = examples[self.saved_index % len(examples)]
            parts = [
                f"<p>我的例句 · {self.saved_index % len(examples) + 1} / {len(examples)}</p>",
                f"<h2>{escape(row['sentence'])}</h2>",
                f"<p>找出「{escape(row['lemma'])}」，用自己的話解釋它跟哪個詞搭配、在這句起什麼作用。</p>",
            ]
            if self.revealed:
                parts.append(f'<p><a name="answer"></a>{escape(row["excerpt"])}</p>')
        elif expand_all:
            parts = [f"<h2>{escape(item.title)} · 完整 4 步推導</h2>"]
            if matches:
                parts.append(f"<p><b>原句</b>　{escape(self.sentence)}</p>")
            for lstep in pipeline.steps:
                parts.append(
                    f'<div style="margin:6px 0; padding:7px 10px; border-left:3px solid {p.accent_pink}; '
                    f'background:rgba(127,127,127,0.07); border-radius:6px;">'
                    f'<div style="font-weight:500; color:{p.accent_pink};">{escape(lstep.title)}</div>'
                    f'<div><b>結論：</b>{escape(lstep.conclusion)}</div>'
                    f'<div style="font-size:12px; color:{p.text_secondary};">{escape(lstep.explanation)}</div>'
                    f"</div>"
                )
            parts.append(
                "<p><b>變形步驟鏈：</b>"
                + " → ".join(escape(v) for v in (self.walk.start, *(s[2] for s in self.walk.steps)))
                + "</p>"
            )
            parts.append(self._render_spoken_drill_html(pipeline))
        else:
            parts = [f"<h2>{escape(item.title)}</h2>"]
            if matches:
                parts.append(f"<p><b>原句</b>　{escape(self.sentence)}</p>")
            if summary:
                parts.append(
                    "<p>"
                    + " → ".join(escape(v) for v in (self.walk.start, *(s[2] for s in self.walk.steps)))
                    + "</p>"
                )
                parts += [
                    f"<h3>比較兩句</h3><p>{escape(item.first)}<br>{escape(item.second)}</p><p>{escape(item.contrast)}</p>",
                    f"<h3>換你判斷</h3><p>{escape(item.prompt)}</p>",
                ]
                if self.revealed:
                    parts.append(f'<p><a name="answer"></a>{escape(item.solution)}</p>')
                parts.append(self._render_spoken_drill_html(pipeline))
            else:
                action, reason, result = self.walk.steps[self.step]
                parts += [
                    f"<p>{'這句的拆解' if live else '用例示範'}　·　{escape(action)}</p>",
                    f"<p>{'先辨認' if not self.step else '沿用上一步的結果'}</p><h3>{escape(self.walk.before(self.step))}</h3>",
                    f"<p>{escape(reason)}</p><p>↓</p><h3>{escape(result)}</h3>",
                ]
                if self.step + 1 < count:
                    parts.append(f"<p>下一步會用這個結果來{escape(self.walk.steps[self.step + 1][0])}。</p>")
            if matches and self.focus.key != self.focus.lexical_key and key == self.focus.key:
                parts.append(f'<p><a href="topic:{escape(self.focus.lexical_key)}">比較本句的詞彙搭配</a></p>')
            if summary or self.show_relations:
                for label, edges in (
                    ("先要理解", REQUIRES.get(key, ())),
                    (
                        "學會後可以用在",
                        [
                            (target, why)
                            for target, reqs in REQUIRES.items()
                            for source, why in reqs
                            if source == key
                        ],
                    ),
                ):
                    if edges:
                        parts.append(f"<h3>{label}</h3>")
                        for node, why in edges:
                            parts.append(f'<p><a href="topic:{escape(node)}">{escape(CONCEPTS[node].title)}</a>　{escape(why)}</p>')
            if not summary:
                label = "收起前置與後續" if self.show_relations else "查看前置與後續的關係"
                parts.append(f'<p><a href="edges:toggle">{label}</a></p>')
            if key in PATTERNS:
                parts.append('<p><a href="pattern:index">看相關句型的接續與例句</a></p>')
            sources = '<a href="tufs:module">東外大 · 練習與用例</a>'
            if matches and self.lemma:
                sources += '　<a href="dict:word">コトバンク · 核對選詞</a>'
            if matches:
                parts.append('<p><a href="reference:search">在離線速查表查這個詞</a></p>')
            parts.append(f"<p>{sources}</p>")
        self.reader.setHtml("".join(parts))

    def _render_spoken_drill_html(self, pipeline: LogicPipeline) -> str:
        p = self._palette
        drill = pipeline.spoken_drill
        replies = "".join(
            f'<div style="margin:2px 0;">• <b>{escape(jp)}</b>'
            f' <span style="color:{p.text_muted}; font-size:12px;">（{escape(zh)}）</span></div>'
            for jp, zh in drill.sample_replies
        )
        return (
            f'<div style="margin-top:8px; padding:8px 10px; border-left:3px solid {p.accent_lilac}; '
            f'background:rgba(127,127,127,0.06); border-radius:6px;">'
            f'<div style="font-weight:500; color:{p.accent_lilac}; margin-bottom:3px;">聽說轉換 · 跟讀與口語回應</div>'
            f'<div style="font-size:12px; color:{p.text_secondary}; margin-bottom:4px;">{escape(drill.shadow_tip)}</div>'
            f'<div style="font-size:12.5px; margin-bottom:3px;">{escape(drill.context_prompt)}</div>'
            f"{replies}</div>"
        )

    def follow(self, link: QUrl) -> None:
        if link.scheme() == "topic":
            self.select(link.path())
        elif link.scheme() == "edges":
            self.show_relations = not self.show_relations
            self.render()
        elif link.scheme() == "pattern" and self.selected_key() in PATTERNS:
            self.pattern_requested.emit(PATTERNS[self.selected_key()])
        elif link.scheme() == "reference":
            self.reference_requested.emit(self.lemma)
        elif link.scheme() == "dict" and self.lemma:
            show_web_dictionary(self.lemma, self, "kotobank")
        elif link.scheme() == "tufs":
            self.open_module()

    def open_module(self) -> None:
        key = self.selected_key()
        if not key or key not in CONCEPTS:
            return
        item = CONCEPTS[key]
        url = QUrl("https://www.coelang.tufs.ac.jp/mt/ja/" + item.module)
        try:
            from PySide6.QtWebEngineCore import QWebEnginePage
            from PySide6.QtWebEngineWidgets import QWebEngineView
        except ImportError:
            QDesktopServices.openUrl(url)
            return

        class _TufsPage(QWebEnginePage):
            def acceptNavigationRequest(page, target, _nav_type, _is_main):
                return target.scheme() == "https" and target.host().lower() in {
                    "www.coelang.tufs.ac.jp",
                    "coelang.tufs.ac.jp",
                }

            def createWindow(page, _window_type):
                return None

        dialog = QDialog(self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.setWindowTitle("東京外國語大學 · " + item.title)
        dialog.resize(820, 600)
        layout = QVBoxLayout(dialog)
        guide = QLabel(
            "先聽一遍，再扮演其中一個角色接話。"
            if "dmod" in item.module
            else "查閱模組例子，再回到自己的台詞，比較同一個用法。"
        )
        guide.setWordWrap(True)
        layout.addWidget(guide)
        view = QWebEngineView(dialog)
        view.setPage(_TufsPage(view))
        layout.addWidget(view, 1)
        retry = QPushButton("載入失敗，重新開啟教材")
        retry.clicked.connect(lambda: view.load(url))
        layout.addWidget(retry)
        retry.hide()
        view.loadFinished.connect(lambda ok: retry.setVisible(not ok))
        dialog.open()
        view.load(url)


# 保持既有匯入名稱相容
StudyMap = LogicWorkbench
