"""🗺️ 系統化日語學習藍圖儀表板（N5→N1 技能樹進度、邊玩邊點亮語法、詞典用法與動詞活用一頁速查）。"""

from __future__ import annotations

import html
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
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
from galgame_anki.ui.theme import get_palette


class LearningBlueprintDialog(QDialog):
    """將碎片化的「玩遊戲查詞典」升級為有清晰邏輯藍圖的 N5→N1 系統學習中心。"""

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
        self.setWindowTitle("🗺️ 日語系統化學習藍圖（邊玩邊點亮 N5 → N1）")
        self.resize(840, 620)

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

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_overview_tab(), "🎯 學習藍圖進度")
        self.tabs.addTab(self._build_grammar_tab(), "🧩 N5→N1 語法技能樹")
        self.tabs.addTab(self._build_cheatsheet_tab(), "📖 30秒看懂詞典・語序・動詞活用")
        layout.addWidget(self.tabs, 1)

        bottom = QHBoxLayout()
        bottom.addStretch()
        close_btn = QPushButton("回到伴讀")
        close_btn.setObjectName("PrimaryBtn")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

    def _build_overview_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        # 頂部總覽指標卡
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

        # N5 -> N1 系統語法進度條
        bars_box = QFrame()
        bars_box.setObjectName("CardPanel")
        bl = QVBoxLayout(bars_box)
        bl.setContentsMargins(16, 12, 16, 12)
        bl.setSpacing(8)

        b_title = QLabel("📈 N5 → N1 核心句型解鎖進度（邊推 Galgame / 看動畫會自動點亮！）")
        b_title.setStyleSheet("font-weight: 700; font-size: 14px; background: transparent;")
        bl.addWidget(b_title)

        self.level_bars: dict[str, tuple[QLabel, QProgressBar]] = {}
        for lv, desc in [
            ("N5", "N5 骨架（基本助詞・時態・願望・禮貌體）"),
            ("N4", "N4 核心活用（可能形・てしまう・條件句・口語縮約）"),
            ("N3", "N3 實戰分水嶺（複合趨勢・邏輯推斷・推日常作主力）"),
            ("N2", "N2 敘事進階（高階比喻・書面銜接・近月/殼少級）"),
            ("N1", "N1 文藝書面（典雅因果・櫻之詩/N1 驗收級）"),
        ]:
            row = QHBoxLayout()
            lbl = QLabel(desc)
            lbl.setMinimumWidth(340)
            lbl.setStyleSheet("background: transparent;")
            row.addWidget(lbl)

            bar = QProgressBar()
            bar.setRange(0, 100)
            row.addWidget(bar, 1)

            count_lbl = QLabel("")
            count_lbl.setMinimumWidth(110)
            count_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            count_lbl.setObjectName("MutedLabel")
            row.addWidget(count_lbl)

            self.level_bars[lv] = (count_lbl, bar)
            bl.addLayout(row)

        layout.addWidget(bars_box)

        # 個人化當前階段「行動藍圖」
        self.roadmap_browser = QTextBrowser()
        layout.addWidget(self.roadmap_browser, 1)
        return tab

    def _build_grammar_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("篩選階段："))
        self.level_filter = QComboBox()
        self.level_filter.addItem("全部等級 (N5 → N1)", "ALL")
        self.level_filter.addItem("🟢 在遊戲／影片中已遇見", "SEEN")
        for lv in ("N5", "N4", "N3", "N2", "N1"):
            self.level_filter.addItem(f"僅顯示 {lv}", lv)
        self.level_filter.currentIndexChanged.connect(self._refresh_grammar_list)
        filter_row.addWidget(self.level_filter)
        filter_row.addStretch()
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
        self.mark_mastered_btn = QPushButton("✓ 標記已掌握")
        self.mark_mastered_btn.setEnabled(False)
        self.mark_mastered_btn.clicked.connect(self._toggle_mastered)
        act_row.addWidget(self.mark_mastered_btn)

        self.mine_grammar_btn = QPushButton("✨ 收錄此語法卡至 Anki")
        self.mine_grammar_btn.setObjectName("MineBtn")
        self.mine_grammar_btn.setEnabled(False)
        self.mine_grammar_btn.clicked.connect(self._mine_selected_grammar)
        act_row.addWidget(self.mine_grammar_btn)
        act_row.addStretch()
        dl.addLayout(act_row)

        splitter.addWidget(detail_box)
        splitter.setSizes([310, 480])
        layout.addWidget(splitter, 1)
        return tab

    def _build_cheatsheet_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)

        p = get_palette(self.theme_name)
        browser = QTextBrowser()
        browser.setHtml(
            f"""
            <h2 style="color:{p.accent_pink}; margin-top:4px;">① 30 秒看懂詞典怎麼用（不再一頭霧水）</h2>
            <div style="background:{p.bg_input}; padding:10px 14px; border-radius:10px; line-height:1.7;">
              <p style="margin:4px 0;"><b>1. 不要每個字都背！只挑「1T 黃金詞」</b>：一句話裡如果你大部分看得懂、只有 1 個核心單詞卡住（介面會用琥珀底線標出），點它看懂意思，按 <b>「✨ 收錄 Anki」</b> 連同聲優語音一起收錄，每天 15–20 張就夠。</p>
              <p style="margin:4px 0;"><b>2. 看懂詞典上的每個小標記：</b></p>
              <ul style="margin:4px 0 6px 18px;">
                <li><b>粗體大字（如 泳ぐ）</b>＝「原形／辭書形」（句中可能是變形後的 <code>泳げない</code>，系統會自動幫你還原！）</li>
                <li><b>「およぐ」</b>＝假名讀音；<b>[2]</b>＝高低音調核（0＝平板調、1＝頭高調、數字代表第幾個音拍後下降）</li>
                <li><b>#490</b>＝JPDB 詞頻排名（數字越小越常見，<b>#1～#5000 優先收錄，#20000 以上冷門詞看過就好別硬背！</b>）</li>
                <li><b>他動詞（〜を）vs 自動詞（〜が）</b>：他動詞是人對對象做動作（開ける＝打開門）；自動詞是東西自己變（開く＝門開了）</li>
              </ul>
              <p style="margin:4px 0;"><b>3. 沒看懂整句結構時</b>：先點下方 <b>「🧩 本句邏輯與語法」</b> 分頁，會直接標出句中的 N5–N1 句型與每個助詞的角色；還想深入問細節就點 <b>「📋 問 Gemini / GPT」</b> 一鍵貼到你的網頁版 AI。</p>
            </div>

            <h2 style="color:{p.accent_pink}; margin-top:16px;">② 日語語序萬用骨架（核心邏輯：動詞永遠放最後！）</h2>
            <div style="background:{p.bg_input}; padding:10px 14px; border-radius:10px; line-height:1.75;">
              <p style="font-size:15px; font-weight:700; margin:4px 0;">
                [主題] <span style="color:{p.accent_pink};">は</span> ＋ [時間/落點] <span style="color:{p.accent_pink};">に</span> ＋ [動作地點/工具] <span style="color:{p.accent_pink};">で</span> ＋ [對象] <span style="color:{p.accent_pink};">を</span> ＋ [動詞＋時態/否定/語氣]
              </p>
              <p style="margin:4px 0; color:{p.text_secondary};">
                例：<code>私は（主題） 学園で（地點） 答えを（對象） 知りたい（動詞＋想） のだ（語氣）</code><br>
                <b>讀長句祕訣：</b>先看「は／が」抓出主角，再直接看<b>句尾動詞</b>知道發生什麼事，最後把中間修飾語填進去！
              </p>
            </div>

            <h2 style="color:{p.accent_pink}; margin-top:16px;">③ 動詞活用一張表打通（看懂規律，不用死背！）</h2>
            <div style="background:{p.bg_input}; padding:10px 14px; border-radius:10px; line-height:1.65;">
              <p style="margin:4px 0;"><b>一段動詞（最簡單，去「る」直接接！例：食べる / 負ける / かぶれる）</b>：<br>
              否定 <code>食べ＋ない</code> ｜ 禮貌 <code>食べ＋ます</code> ｜ 過去 <code>食べ＋た</code> ｜ 可能 <code>食べ＋られる</code> ｜ 過頭 <code>食べ＋すぎる</code></p>
              <p style="margin:6px 0 4px 0;"><b>五段動詞（詞尾按 50 音 あ・い・う・え・お 切換！例：泳ぐ / 書く / 踏ん張る）</b>：</p>
              <ul style="margin:2px 0 6px 18px;">
                <li><b>あ段 + ない</b> ＝ 否定形（泳ぐ → 泳<b>が</b>ない）</li>
                <li><b>い段 + ます / たい / すぎる</b> ＝ 禮貌・願望・複合詞幹（泳ぐ → 泳<b>ぎ</b>ます）</li>
                <li><b>う段</b> ＝ 原形／辭書形（泳<b>ぐ</b>）</li>
                <li><b>え段 + る / ば</b> ＝ 可能形／假定形（泳ぐ → 泳<b>げ</b>る → 否定 泳<b>げない</b>！）</li>
                <li><b>お段 + う</b> ＝ 意志形「…吧」（泳ぐ → 泳<b>ご</b>う）</li>
                <li><b>て / た形音便口訣</b>：うつる→って/った｜ぬぶむ→んで/んだ｜く→いて/いた｜ぐ→いで/いだ｜す→して/した</li>
              </ul>
            </div>

            <h2 style="color:{p.accent_pink}; margin-top:16px;">④ 五十音平片假名速查表（認個臉熟就去遊戲裡靠語音熟記）</h2>
            <div style="background:{p.bg_input}; padding:10px 12px; border-radius:10px; font-family:monospace; font-size:13px; line-height:1.8;">
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

        self.lbl_vocab.setText(f"<b>{self.known_words_count:,}</b><br><span style='font-size:12px; opacity:0.75;'>Anki 累積詞彙</span>")
        self.lbl_sentences.setText(f"<b>{sentences_read:,}</b><br><span style='font-size:12px; opacity:0.75;'>已閱讀真實日語句</span>")
        self.lbl_grammar_total.setText(
            f"<b>{seen_grammar} / {total_grammar}</b>（掌握 {mastered_grammar}）<br>"
            "<span style='font-size:12px; opacity:0.75;'>核心語法點亮數</span>"
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
                "<b>目前階段：Lv.1 破冰期（0 → 800 詞 · 熟悉假名、助詞與基本語序）</b>",
                "① 選簡單日常作（柚子社 / ATRI / 日常動畫）開覆蓋層邊玩邊聽聲優發音；"
                "② 先去「📖 30秒看懂詞典・語序・動詞活用」花 2 分鐘看懂日語骨架；"
                "③ 每天只收錄 <b>15 張高頻詞（#1～#5000）＋原聲語音</b>，不要見詞就收，保持推劇情的快樂！",
            ),
            "lv2": (
                "<b>目前階段：Lv.2 精細沉浸期（800 → 3,500 詞 · 1T 句卡＋N4/N3 語法）</b>",
                "① 專挑「★ 1T 黃金句」與常用 N4/N3 句型收錄進 Anki；"
                "② 善用「🧩 本句邏輯與語法」確認活用形與助詞，卡住時一鍵複製提問詞問 Gemini/GPT；"
                "③ 每天刷完開啟 FSRS 的 Anki 卡片，其餘時間放心無阻沉浸推劇情。",
            ),
            "lv3": (
                "<b>目前階段：Lv.3 登樓撤梯期（3,500 → 6,000+ 詞 · 單語詞典過渡衝刺 N1）</b>",
                "① 以日日單語詞典為主，只在真的卡死時展開雙語安全網；"
                "② 挑戰近月 / 殼少 / 櫻之詩等高密度文本，同時補齊藍圖中的 N2/N1 書面句型。",
            ),
        }
        head, body = stage_advice.get(self.stage_mode, stage_advice["lv1"])
        self.roadmap_browser.setHtml(
            f'<div style="background:{p.bg_input}; padding:12px 14px; border-left:4px solid {p.accent_pink}; border-radius:10px; line-height:1.75;">'
            f'<div style="font-size:14px; color:{p.accent_pink}; margin-bottom:4px;">{head}</div>'
            f'<div>{body}</div>'
            f'</div>'
        )

    def _refresh_grammar_list(self) -> None:
        self._progress_map = self.progress_store.get_all_grammar_progress()
        filt = str(self.level_filter.currentData() or "ALL")
        self.grammar_list.clear()

        for pat in self.grammar.patterns:
            prog = self._progress_map.get(pat["id"], {})
            seen_cnt = int(prog.get("seen_count", 0))
            status = str(prog.get("status", "new"))

            if filt == "SEEN" and seen_cnt == 0:
                continue
            if filt in ("N5", "N4", "N3", "N2", "N1") and pat["level"] != filt:
                continue

            if status == "mastered":
                icon = "✅"
            elif seen_cnt > 0:
                icon = f"🌸({seen_cnt})"
            else:
                icon = "⬜"

            item = QListWidgetItem(f"{icon} [{pat['level']}] {pat['name']}")
            item.setData(Qt.ItemDataRole.UserRole, pat)
            self.grammar_list.addItem(item)

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

        self.mark_mastered_btn.setText("↩ 取消已掌握" if status == "mastered" else "✓ 標記已掌握")

        p = get_palette(self.theme_name)
        seen_html = ""
        if last_ex:
            src_txt = f"（來自：{html.escape(last_src)}）" if last_src else ""
            seen_html = (
                f'<div style="margin-top:10px; padding:10px 12px; background:{p.pill_ok_bg}; '
                f'color:{p.pill_ok_fg}; border-radius:8px;">'
                f'<b>🎮 你在實戰中遇見過 {seen_cnt} 次！{src_txt}</b><br>'
                f'<span style="font-size:15px;">{html.escape(last_ex)}</span>'
                f'</div>'
            )

        self.grammar_detail.setHtml(
            f'<h3 style="color:{p.accent_pink}; margin:0 0 6px 0;">[{html.escape(pat["level"])}] {html.escape(pat["name"])}</h3>'
            f'<div style="font-size:12px; color:{p.text_muted}; margin-bottom:10px;">分類：{html.escape(pat["category"])}</div>'
            f'<div style="padding:8px 12px; background:{p.bg_input}; border-radius:8px; margin-bottom:8px;">'
            f'<b>📐 接續公式：</b>{html.escape(pat["formula"])}</div>'
            f'<div style="line-height:1.7; margin-bottom:8px;"><b>💡 核心邏輯：</b>{html.escape(pat["meaning"])}</div>'
            f'<div style="line-height:1.7;"><b>📝 標準例句：</b>{html.escape(pat["example"])}</div>'
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
