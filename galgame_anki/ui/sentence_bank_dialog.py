"""💎 1T 黃金句礦場（Sentence Bank）視窗 — 從已解包遊戲劇本庫高速篩選 i+1 低垂果實。"""

from __future__ import annotations

from PySide6.QtCore import Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from galgame_anki.adapters.media_utils import play_mp3_bytes_async
from galgame_anki.config import AppConfig
from galgame_anki.core.sentence_bank import (
    SentenceBankItem,
    SentenceBankScanner,
    SentenceBankScanResult,
)
from galgame_anki.core.voice_pack import VoiceCharacterGroup, VoicePackManager
from galgame_anki.ui.pitch_widget import PitchGraphWidget
from galgame_anki.ui.workers import FunctionWorker


class SentenceBankDialog(QDialog):
    """對已索引遊戲劇本按 Anki 已知詞＋JPDB 詞頻＋防劇透章節篩出 1T 黃金有聲句，一鍵試聽與精煉收錄。"""

    mine_item_requested = Signal(object)       # SentenceBankItem
    mark_known_requested = Signal(str)         # lemma
    _scan_progress = Signal(str)

    def __init__(
        self,
        config: AppConfig,
        voice_packs: VoicePackManager,
        scanner: SentenceBankScanner,
        known_words: set[str],
        thread_pool: QThreadPool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("💎 1T 黃金句礦場（從已解包劇本收割低垂果實）")
        self.resize(840, 650)
        self.config = config
        self.voice_packs = voice_packs
        self.scanner = scanner
        self.known_words = known_words
        self.thread_pool = thread_pool
        self._workers: list[object] = []
        self._mined_lemmas: set[str] = set()
        self._last_result: SentenceBankScanResult | None = None

        self._build_ui()
        self._scan_progress.connect(lambda msg: self.stats_label.setText(f"⏳ {msg}"))
        self._populate_packs()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        intro = QLabel(
            "<span style='font-weight:500;'>AJATT / Refold 低垂果實挖礦：</span> 自動扣除您 Anki 裡的已知詞，從遊戲劇本庫篩出"
            "「<span style='font-weight:500;'>正好只差 1 個高頻生詞、且附帶 0% BGM 聲優純人聲</span>」的黃金句（支援按章節與喜歡的角色篩選，絕不劇透）！"
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        # === 篩選列 ===
        flt_box = QFrame()
        flt_box.setObjectName("CardPanel")
        fl = QVBoxLayout(flt_box)
        fl.setSpacing(7)

        r1 = QHBoxLayout()
        r1.addWidget(QLabel("遊戲："))
        self.pack_combo = QComboBox()
        self.pack_combo.currentIndexChanged.connect(self._on_pack_changed)
        r1.addWidget(self.pack_combo, 2)

        r1.addWidget(QLabel("角色："))
        self.char_combo = QComboBox()
        r1.addWidget(self.char_combo, 1)

        r1.addWidget(QLabel("生詞門檻："))
        self.target_mode_combo = QComboBox()
        self.target_mode_combo.addItem("僅 1T 黃金句（最推薦）", 1)
        self.target_mode_combo.addItem("放寬至 1T–2T", 2)
        r1.addWidget(self.target_mode_combo)
        fl.addLayout(r1)

        r2 = QHBoxLayout()
        r2.addWidget(QLabel("防劇透章節："))
        self.start_scene_combo = QComboBox()
        r2.addWidget(self.start_scene_combo, 1)
        r2.addWidget(QLabel("～"))
        self.end_scene_combo = QComboBox()
        r2.addWidget(self.end_scene_combo, 1)

        self.keyword_edit = QLineEdit()
        self.keyword_edit.setPlaceholderText("可選：搜尋關鍵字或詞彙…")
        self.keyword_edit.returnPressed.connect(self._start_scan)
        r2.addWidget(self.keyword_edit, 1)
        fl.addLayout(r2)

        r3 = QHBoxLayout()
        self.voiced_only_chk = QCheckBox("只看有 0% BGM 純人聲的句子")
        self.voiced_only_chk.setChecked(True)
        r3.addWidget(self.voiced_only_chk)

        self.dedup_chk = QCheckBox("每個生詞只顯示最佳 1 句（防重複）")
        self.dedup_chk.setChecked(True)
        r3.addWidget(self.dedup_chk)

        r3.addStretch()
        self.scan_btn = QPushButton("🔍 掃描 1T 黃金句")
        self.scan_btn.setObjectName("PrimaryBtn")
        self.scan_btn.clicked.connect(self._start_scan)
        r3.addWidget(self.scan_btn)
        fl.addLayout(r3)
        root.addWidget(flt_box)

        # 統計與覆蓋率列
        self.stats_label = QLabel("選擇遊戲與章節範圍後，點擊「🔍 掃描 1T 黃金句」開始挖礦。")
        self.stats_label.setObjectName("MutedLabel")
        self.stats_label.setWordWrap(True)
        root.addWidget(self.stats_label)

        # === 結果清單 ===
        self.result_list = QListWidget()
        self.result_list.setMinimumHeight(230)
        self.result_list.currentRowChanged.connect(self._on_item_selected)
        root.addWidget(self.result_list, 1)

        # === 預覽與一鍵操作卡 ===
        detail_box = QFrame()
        detail_box.setObjectName("CardPanel")
        dl = QVBoxLayout(detail_box)
        dl.setContentsMargins(12, 10, 12, 10)
        dl.setSpacing(6)

        hdr = QHBoxLayout()
        self.word_lbl = QLabel("點選上方任一句 1T 黃金句預覽詳情")
        self.word_lbl.setStyleSheet("font-size: 18px; font-weight: 500; background: transparent;")
        hdr.addWidget(self.word_lbl)

        self.reading_lbl = QLabel("")
        self.reading_lbl.setObjectName("MutedLabel")
        hdr.addWidget(self.reading_lbl)

        self.pitch_widget = PitchGraphWidget(
            compact=True,
            dark_mode=(self.config.ui_theme == "starlight"),
        )
        hdr.addWidget(self.pitch_widget)
        hdr.addStretch()
        dl.addLayout(hdr)

        self.sentence_lbl = QLabel("")
        self.sentence_lbl.setWordWrap(True)
        self.sentence_lbl.setStyleSheet("font-size: 16px; background: transparent;")
        dl.addWidget(self.sentence_lbl)

        self.meaning_lbl = QLabel("")
        self.meaning_lbl.setWordWrap(True)
        self.meaning_lbl.setObjectName("MutedLabel")
        dl.addWidget(self.meaning_lbl)

        self.trans_lbl = QLabel("")
        self.trans_lbl.setWordWrap(True)
        self.trans_lbl.setObjectName("MutedLabel")
        self.trans_lbl.hide()
        dl.addWidget(self.trans_lbl)

        act_row = QHBoxLayout()
        self.play_btn = QPushButton("🔊 試聽 0% BGM 純人聲")
        self.play_btn.setEnabled(False)
        self.play_btn.clicked.connect(self._play_current_voice)
        act_row.addWidget(self.play_btn)

        self.toggle_trans_btn = QPushButton("🌐 顯示劇本譯文")
        self.toggle_trans_btn.setEnabled(False)
        self.toggle_trans_btn.clicked.connect(self._toggle_translation)
        act_row.addWidget(self.toggle_trans_btn)

        self.known_btn = QPushButton("✓ 我已認識此詞")
        self.known_btn.setEnabled(False)
        self.known_btn.setToolTip("將此詞標記為已知詞，不再算進生詞（提升之後 1T 篩選精準度）")
        self.known_btn.clicked.connect(self._mark_current_known)
        act_row.addWidget(self.known_btn)

        act_row.addStretch()

        self.mine_btn = QPushButton("✨ 精煉收錄至 Anki")
        self.mine_btn.setObjectName("MineBtn")
        self.mine_btn.setEnabled(False)
        self.mine_btn.clicked.connect(self._mine_current_item)
        act_row.addWidget(self.mine_btn)
        dl.addLayout(act_row)

        root.addWidget(detail_box)

        bottom = QHBoxLayout()
        bottom.addStretch()
        close_btn = QPushButton("關閉")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

    def _populate_packs(self) -> None:
        self.pack_combo.blockSignals(True)
        self.pack_combo.clear()
        packs = self.voice_packs.list_packs()
        target_idx = 0
        for i, pk in enumerate(packs):
            self.pack_combo.addItem(f"{pk.game_name}（{pk.voiced_line_count} 句有聲）", pk.pack_id)
            if pk.pack_id == self.config.active_voice_pack_id:
                target_idx = i
        self.pack_combo.blockSignals(False)
        if packs:
            self.pack_combo.setCurrentIndex(target_idx)
            self._on_pack_changed(target_idx)
        else:
            self.stats_label.setText("⚠ 尚未建立任何遊戲純人聲索引，請先從「📦 解包遊戲提取純人聲」掃描遊戲目錄。")
            self.scan_btn.setEnabled(False)

    def _on_pack_changed(self, idx: int) -> None:
        pack_id = str(self.pack_combo.itemData(idx) or "")
        self.start_scene_combo.clear()
        self.end_scene_combo.clear()
        self.char_combo.clear()
        self.start_scene_combo.addItem("（從開頭）", "")
        self.end_scene_combo.addItem("（到結尾）", "")
        self.char_combo.addItem("全部角色", "")
        if not pack_id:
            return
        for sc in self.voice_packs.list_pack_scenes(pack_id):
            self.start_scene_combo.addItem(sc, sc)
            self.end_scene_combo.addItem(sc, sc)
        chars: list[VoiceCharacterGroup] = self.voice_packs.list_pack_characters(pack_id)
        for ch in chars:
            self.char_combo.addItem(f"{ch.display_name} ({ch.solo_line_count} 句)", ch.char_key)

    def _start_scan(self) -> None:
        pack_id = str(self.pack_combo.currentData() or "")
        if not pack_id:
            return
        start_sc = str(self.start_scene_combo.currentData() or "")
        end_sc = str(self.end_scene_combo.currentData() or "")
        char_key = str(self.char_combo.currentData() or "")
        voiced_only = self.voiced_only_chk.isChecked()
        max_unk = int(self.target_mode_combo.currentData() or 1)
        dedup = self.dedup_chk.isChecked()
        kw = self.keyword_edit.text().strip()
        known_snapshot = set(self.known_words)
        dict_pref = self.config.effective_dict_preference()

        self.scan_btn.setEnabled(False)
        self.scan_btn.setText("⏳ 掃描中…")
        self.result_list.clear()

        def _work() -> SentenceBankScanResult:
            return self.scanner.scan_candidates(
                voice_packs=self.voice_packs,
                pack_id=pack_id,
                known_words=known_snapshot,
                start_scene=start_sc,
                end_scene=end_sc,
                char_key=char_key,
                voiced_only=voiced_only,
                max_unknown=max_unk,
                dedup_by_lemma=dedup,
                keyword=kw,
                dict_preference=dict_pref,
                limit=180,
                progress_cb=lambda msg: self._scan_progress.emit(msg),
            )

        w = FunctionWorker(_work)
        w.signals.finished.connect(self._on_scan_done)
        w.signals.failed.connect(self._on_scan_fail)
        self._workers.append(w)
        self.thread_pool.start(w)

    def _on_scan_done(self, res: SentenceBankScanResult) -> None:
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText("🔍 掃描 1T 黃金句")
        self._last_result = res
        self.result_list.clear()

        for item in res.items:
            t_tag = "★1T" if item.unknown_count == 1 else f"{item.unknown_count}T"
            f_tag = f"#{item.target_frequency}" if item.target_frequency else "—"
            spk = f"[{item.speaker}] " if item.speaker else ""
            gloss = f" — {item.target_meaning[:26]}" if item.target_meaning else ""
            done_tag = "✓ " if item.target_lemma in self._mined_lemmas else ""
            li = QListWidgetItem(
                f"{done_tag}[{t_tag} · {f_tag}] {spk}{item.highlighted_text[:44]}{gloss}"
            )
            li.setData(Qt.ItemDataRole.UserRole, item)
            self.result_list.addItem(li)

        self.stats_label.setText(
            f"✅ 掃描 {res.total_lines_scanned} 句台詞 ｜ 已知詞覆蓋率：{res.coverage_pct:.1f}% "
            f"｜ 全懂 0T：{res.zero_t_count} 句 ｜ 1T 黃金句總計：{res.one_t_count} 句（依詞頻精選前 {len(res.items)} 筆）"
        )
        if res.items:
            self.result_list.setCurrentRow(0)
        else:
            self._clear_preview()

    def _on_scan_fail(self, err: str) -> None:
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText("🔍 掃描 1T 黃金句")
        self.stats_label.setText(f"❌ 掃描失敗：{err}")
        QMessageBox.warning(self, "掃描 1T 句庫失敗", err)

    def _current_item(self) -> SentenceBankItem | None:
        it = self.result_list.currentItem()
        if not it:
            return None
        return it.data(Qt.ItemDataRole.UserRole)

    def _clear_preview(self) -> None:
        self.word_lbl.setText("找不到符合條件的 1T 候選句（可放寬至 1T–2T 或擴大章節範圍）")
        self.reading_lbl.setText("")
        self.pitch_widget.hide()
        self.sentence_lbl.setText("")
        self.meaning_lbl.setText("")
        self.trans_lbl.hide()
        self.play_btn.setEnabled(False)
        self.toggle_trans_btn.setEnabled(False)
        self.known_btn.setEnabled(False)
        self.mine_btn.setEnabled(False)

    def _on_item_selected(self, _row: int) -> None:
        item = self._current_item()
        if item is None:
            self._clear_preview()
            return

        freq_txt = f" · 詞頻 #{item.target_frequency}" if item.target_frequency else ""
        spk_txt = f" · [{item.speaker}]" if item.speaker else ""
        self.word_lbl.setText(item.target_lemma)
        self.reading_lbl.setText(f"「{item.target_reading}」{freq_txt}{spk_txt} · {item.scene_file}")
        self.pitch_widget.set_pitch(
            item.target_reading or item.target_lemma,
            item.target_pitches,
            dark_mode=(self.config.ui_theme == "starlight"),
        )
        self.sentence_lbl.setText(item.highlighted_text)
        self.meaning_lbl.setText(f"🎯 精煉釋義：{item.target_meaning or '（點擊「精煉收錄」檢視完整詞典）'}")

        self.trans_lbl.setText(f"🌐 劇本參考譯文：{item.translation}" if item.translation else "")
        self.trans_lbl.hide()
        self.toggle_trans_btn.setEnabled(bool(item.translation))
        self.toggle_trans_btn.setText("🌐 顯示劇本譯文")

        self.play_btn.setEnabled(bool(item.voice_ids))
        self.known_btn.setEnabled(True)
        self.mine_btn.setEnabled(True)

    def _toggle_translation(self) -> None:
        vis = not self.trans_lbl.isVisible()
        self.trans_lbl.setVisible(vis)
        self.toggle_trans_btn.setText("🌐 隱藏劇本譯文" if vis else "🌐 顯示劇本譯文")

    def _play_current_voice(self) -> None:
        item = self._current_item()
        if item is None or not item.voice_ids:
            return
        self.play_btn.setEnabled(False)

        def _do() -> tuple[bytes, str] | None:
            return self.voice_packs.extract_single_line_mp3(item.pack_id, item.voice_ids)

        w = FunctionWorker(_do)
        w.signals.finished.connect(self._on_voice_ready)
        w.signals.failed.connect(lambda _e: self.play_btn.setEnabled(True))
        self._workers.append(w)
        self.thread_pool.start(w)

    def _on_voice_ready(self, clip: tuple[bytes, str] | None) -> None:
        self.play_btn.setEnabled(True)
        if clip:
            play_mp3_bytes_async(clip[0])

    def _mark_current_known(self) -> None:
        item = self._current_item()
        if item is None:
            return
        self.known_words.add(item.target_lemma)
        self.mark_known_requested.emit(item.target_lemma)
        self._mined_lemmas.add(item.target_lemma)
        row = self.result_list.currentRow()
        if row >= 0:
            self.result_list.takeItem(row)

    def _mine_current_item(self) -> None:
        item = self._current_item()
        if item is None:
            return
        self._mined_lemmas.add(item.target_lemma)
        self.mine_item_requested.emit(item)
        li = self.result_list.currentItem()
        if li is not None and not li.text().startswith("✓ "):
            li.setText("✓ " + li.text())
