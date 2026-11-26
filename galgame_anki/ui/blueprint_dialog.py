"""系統化日語語法技能樹（N5 → N1 ＋ 口語實戰，8 大領域篩選、即時搜尋、進度總覽與一頁速查）。"""

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
from galgame_anki.ui.study_map import StudyMap
from galgame_anki.ui.verb_explorer import VerbExplorer
from galgame_anki.ui.theme import get_palette


class LearningBlueprintDialog(QDialog):
    """結構化 N5→N1 ＋ 口語實戰語法技能樹、進度總覽與軟體上手速查中心。"""

    mine_grammar_requested = Signal(dict, str)  # (pattern_dict, real_example_sentence)

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
        self.resize(840, 600)

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

    def select_pattern(self, pattern_id: str) -> bool:
        """跳轉並選取指定語法句型（從「句型拆解」卡片點擊進入時使用）。"""
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
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        self.tabs = QTabWidget()
        self.study_map = StudyMap(self.progress_store, self.theme_name)
        self.tabs.addTab(self.study_map, "知識地圖")
        self.study_map.pattern_requested.connect(self.select_pattern)
        self.grammar_tab = self._build_grammar_tab()
        self.tabs.addTab(self.grammar_tab, "句型索引")
        self.reference = JapaneseReference(self.grammar, self.theme_name)
        self.tabs.addTab(self.reference, "日語總索引")
        self.reference_shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        self.reference_shortcut.activated.connect(self.find_reference)
        self.study_map.reference_requested.connect(self.find_reference)
        self.reference.pattern_requested.connect(self.select_pattern)
        self.reference.lesson_requested.connect(self._open_concept)
        self.tabs.addTab(self._build_overview_tab(), "學習總覽")
        self.tabs.addTab(self._build_cheatsheet_tab(), "上手與速查")
        self.verb_explorer = VerbExplorer(self.theme_name)
        self.tabs.addTab(self.verb_explorer, "看懂動詞變化")
        layout.addWidget(self.tabs, 1)

        bottom = QHBoxLayout()
        bottom.addStretch()
        close_btn = QPushButton("關閉")
        close_btn.setObjectName("PrimaryBtn")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

    def find_reference(self, query=None):
        self.tabs.setCurrentWidget(self.reference)
        if query is not None:
            self.reference.search.setText(query)
        self.reference.search.setFocus()
        self.reference.search.selectAll()

    def _open_concept(self, key):
        self.study_map.select(key)
        self.tabs.setCurrentWidget(self.study_map)

    def _build_grammar_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

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
        self.grammar_list.currentRowChanged.connect(self._on_grammar_selected)
        splitter.addWidget(self.grammar_list)

        detail_box = QFrame()
        detail_box.setObjectName("CardPanel")
        dl = QVBoxLayout(detail_box)
        dl.setContentsMargins(14, 12, 14, 12)
        dl.setSpacing(8)

        self.grammar_detail = QTextBrowser()
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
        splitter.setSizes([330, 460])
        layout.addWidget(splitter, 1)
        return tab

    def _build_overview_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        stats_box = QFrame()
        stats_box.setObjectName("CardPanel")
        sl = QHBoxLayout(stats_box)
        sl.setContentsMargins(16, 12, 16, 12)

        self.lbl_vocab = QLabel("")
        self.lbl_sentences = QLabel("")
        self.lbl_grammar_total = QLabel("")
        for lbl in (self.lbl_vocab, self.lbl_sentences, self.lbl_grammar_total):
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("font-size: 14px; background: transparent;")
            sl.addWidget(lbl, 1)
        layout.addWidget(stats_box)

        bars_box = QFrame()
        bars_box.setObjectName("CardPanel")
        bl = QVBoxLayout(bars_box)
        bl.setContentsMargins(16, 12, 16, 12)
        bl.setSpacing(7)

        b_title = QLabel("各等級句型覆蓋進度（閱讀遊戲／影片台詞自動點亮）")
        b_title.setStyleSheet("font-weight: 700; font-size: 13.5px; background: transparent;")
        bl.addWidget(b_title)

        self.level_bars: dict[str, tuple[QLabel, QProgressBar]] = {}
        for lv, desc in [
            ("N5", "N5  基礎骨架（助詞・時態・願望・請求・授受）"),
            ("N4", "N4  核心活用（條件・可能・被動・使役・推測）"),
            ("N3", "N3  日常實戰（趨勢・立場・限定・日常作品主力）"),
            ("N2", "N2  敘事進階（書面銜接・高階比喻・讓步對比）"),
            ("N1", "N1  文藝書面（典雅因果・反轉・文學作品級）"),
            ("口語", "口語  遊戲與動畫高頻口語縮略・句尾語氣"),
        ]:
            row = QHBoxLayout()
            lbl = QLabel(desc)
            lbl.setMinimumWidth(330)
            lbl.setStyleSheet("background: transparent;")
            row.addWidget(lbl)

            bar = QProgressBar()
            bar.setRange(0, 100)
            row.addWidget(bar, 1)

            count_lbl = QLabel("")
            count_lbl.setMinimumWidth(115)
            count_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            count_lbl.setObjectName("MutedLabel")
            row.addWidget(count_lbl)

            self.level_bars[lv] = (count_lbl, bar)
            bl.addLayout(row)

        layout.addWidget(bars_box)

        self.roadmap_browser = QTextBrowser()
        layout.addWidget(self.roadmap_browser, 1)
        return tab

    def _build_cheatsheet_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)

        p = get_palette(self.theme_name)
        browser = QTextBrowser()
        browser.setHtml(
            f"""
            <h3 style="color:{p.accent_pink}; margin:4px 0 6px 0;">一、GalAnki 使用邏輯（3 步驟）</h3>
            <div style="background:{p.bg_input}; padding:10px 14px; border-radius:8px; line-height:1.7;">
              <div><b>1. 接入來源</b>：從系統匣或「來源」啟動 Galgame（自動 Hook）或連接 mpv 字幕，主視窗平時收進系統匣，只留覆蓋層疊在畫面上。</div>
              <div><b>2. 懸停查詞</b>：滑鼠移到生詞上自動顯示原形、讀音、音調曲線與本句語境釋義；右鍵覆蓋層或點右下角齒輪可調外觀與開控制台。</div>
              <div><b>3. 一鍵收錄</b>：左鍵點「一鍵收錄」直接寫入 Anki（自動帶原聲、截圖與關聯詞，右下角 6 秒內可撤銷）；按住 <b>Shift</b> 點擊可先編輯。</div>
            </div>

            <h3 style="color:{p.accent_pink}; margin:14px 0 6px 0;">二、詞典標記說明</h3>
            <div style="background:{p.bg_input}; padding:10px 14px; border-radius:8px; line-height:1.7;">
              <div><b>粗體字（例：泳ぐ）</b>：辭書原形（句中變形如 <code>泳げない</code> 會自動還原）。</div>
              <div><b>[0] / [1] / [2]</b>：東京式音調核（0 平板、1 頭高、數字表示第幾個音拍後下降）。</div>
              <div><b>#490</b>：JPDB 詞頻排名（數字越小越常見；#1–#5000 優先收錄，#20000 以上看懂即可）。</div>
              <div><b>他動詞（〜を）vs 自動詞（〜が）</b>：他動詞作用於受詞（開ける＝打開）；自動詞描述自身狀態變化（開く＝開了）。</div>
            </div>

            <h3 style="color:{p.accent_pink}; margin:14px 0 6px 0;">三、日語核心語序</h3>
            <div style="background:{p.bg_input}; padding:10px 14px; border-radius:8px; line-height:1.75;">
              <div style="font-size:14.5px; font-weight:700;">
                [主題] <span style="color:{p.accent_pink};">は</span> ＋ [時間/落點] <span style="color:{p.accent_pink};">に</span> ＋ [地點/工具] <span style="color:{p.accent_pink};">で</span> ＋ [對象] <span style="color:{p.accent_pink};">を</span> ＋ [句尾動詞＋語氣]
              </div>
              <div style="color:{p.text_secondary}; margin-top:4px;">
                讀長句順序：先看「は／が」抓主語，直接看句尾動詞抓核心動作，再補中間修飾語。
              </div>
            </div>

            <h3 style="color:{p.accent_pink}; margin:14px 0 6px 0;">四、動詞活用核心規律</h3>
            <div style="background:{p.bg_input}; padding:10px 14px; border-radius:8px; line-height:1.65;">
              <div><b>一段動詞（去 る 直接接，例：食べる）</b>：食べない（否定）／ 食べます（禮貌）／ 食べた（過去）／ 食べられる（可能）</div>
              <div style="margin-top:4px;"><b>五段動詞（詞尾依 あ・い・う・え・お 變化，例：泳ぐ）</b>：</div>
              <ul style="margin:2px 0 4px 18px;">
                <li><b>あ段 + ない</b>：否定形（泳がない）</li>
                <li><b>い段 + ます / たい</b>：禮貌・願望・複合詞幹（泳ぎます）</li>
                <li><b>え段 + る / ば</b>：可能形・假定形（泳げる / 泳げば）</li>
                <li><b>お段 + う</b>：意志形（泳ごう）</li>
                <li><b>て／た音便</b>：うつる→って｜ぬぶむ→んで｜く→いて｜ぐ→いで｜す→して</li>
              </ul>
            </div>

            <h3 style="color:{p.accent_pink}; margin:14px 0 6px 0;">五、五十音表</h3>
            <div style="background:{p.bg_input}; padding:10px 12px; border-radius:8px; font-family:monospace; font-size:13px; line-height:1.8;">
              a: あ/ア　i: い/イ　u: う/ウ　e: え/エ　o: お/オ<br>
              k: か/カ　き/キ　く/ク　け/ケ　こ/コ　　s: さ/サ　し/シ　す/ス　せ/セ　そ/ソ<br>
              t: た/タ　ち/チ　つ/ツ　て/テ　と/ト　　n: な/ナ　に/ニ　ぬ/ヌ　ね/ネ　の/ノ<br>
              h: は/ハ　ひ/ヒ　ふ/フ　へ/ヘ　ほ/ホ　　m: ま/マ　み/ミ　む/ム　め/メ　も/モ<br>
              y: や/ヤ　ゆ/ユ　よ/ヨ　　r: ら/ラ　り/リ　る/ル　れ/レ　ろ/ロ　　w: わ/ワ　を/ヲ　ん/ン
            </div>
            """
        )
        layout.addWidget(browser)
        return tab

    def _refresh_overview(self) -> None:
        self._progress_map = self.progress_store.get_all_grammar_progress()
        sentences_read = self.progress_store.get_sentences_read()
        seen_grammar = sum(1 for v in self._progress_map.values() if v.get("seen_count", 0) > 0)
        mastered_grammar = sum(1 for v in self._progress_map.values() if v.get("status") == "mastered")
        total_grammar = len(self.grammar.patterns)

        self.lbl_vocab.setText(f"<b>{self.known_words_count:,}</b><br><span style='font-size:12px; opacity:0.75;'>Anki 已知詞彙</span>")
        self.lbl_sentences.setText(f"<b>{sentences_read:,}</b><br><span style='font-size:12px; opacity:0.75;'>已讀台詞句數</span>")
        self.lbl_grammar_total.setText(
            f"<b>{seen_grammar} / {total_grammar}</b>（掌握 {mastered_grammar}）<br>"
            "<span style='font-size:12px; opacity:0.75;'>語法覆蓋進度</span>"
        )

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
            count_lbl.setText(f"遇見 {seen}/{tot} · 懂 {mast}")

        p = get_palette(self.theme_name)
        stage_advice = {
            "lv1": (
                "目前階段：Lv.1 破冰期（0 – 800 詞 · 假名與基礎語序）",
                "1. 挑日常系作品開覆蓋層聽原聲讀台詞；2. 先把技能樹 N5 與口語縮略看熟；3. 每天收錄 15 張 #1–#5000 高頻詞即可。",
            ),
            "lv2": (
                "目前階段：Lv.2 沉浸期（800 – 3,500 詞 · 1T 句卡與 N4/N3 句型）",
                "1. 專收每句只卡 1 個生詞的 1T 黃金句；2. 配合技能樹補齊 N4/N3 句型；3. 每日完成 FSRS 複習後專心推劇情。",
            ),
            "lv3": (
                "目前階段：Lv.3 單語過渡（3,500+ 詞 · 日日詞典與 N2/N1）",
                "1. 以日日單語釋義為主，雙語退居折疊安全網；2. 透過技能樹篩選未掌握的 N2/N1 書面句型逐項打通。",
            ),
        }
        head, body = stage_advice.get(self.stage_mode, stage_advice["lv1"])
        self.roadmap_browser.setHtml(
            f'<div style="background:{p.bg_input}; padding:11px 14px; border-left:3px solid {p.accent_pink}; border-radius:8px; line-height:1.7;">'
            f'<div style="font-size:13.5px; font-weight:700; color:{p.accent_pink}; margin-bottom:4px;">{head}</div>'
            f'<div>{body}</div>'
            f'</div>'
        )

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
                f'<div style="font-size:12px; font-weight:700; margin-bottom:2px;">實戰遇見 {seen_cnt} 次{src_txt}</div>'
                f'<div style="font-size:14.5px;">{html.escape(last_ex)}</div>'
                f'</div>'
            )

        self.grammar_detail.setHtml(
            f'<h3 style="color:{p.accent_pink}; margin:0 0 4px 0;">[{html.escape(pat["level"])}] {html.escape(pat["name"])}</h3>'
            f'<div style="font-size:12px; color:{p.text_muted}; margin-bottom:10px;">{html.escape(pat["category"])}</div>'
            f'<div style="padding:8px 12px; background:{p.bg_input}; border-radius:8px; margin-bottom:8px;">'
            f'<b>接續：</b>{html.escape(pat["formula"])}</div>'
            f'<div style="line-height:1.7; margin-bottom:8px;"><b>用法：</b>{html.escape(pat["meaning"])}</div>'
            f'<div style="line-height:1.7;"><b>例句：</b>{html.escape(pat["example"])}</div>'
            f'{seen_html}'
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
