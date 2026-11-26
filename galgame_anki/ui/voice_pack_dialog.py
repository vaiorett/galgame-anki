"""📦 遊戲解包、純淨人聲（0% BGM・0% 音效）索引與【角色分類濃縮被動聽力 MP3】管理視窗。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from galgame_anki.adapters.media_utils import play_mp3_bytes_async
from galgame_anki.adapters.xp3_psb import cached_formats_dat_path
from galgame_anki.config import AppConfig
from galgame_anki.core.voice_pack import (
    MatchedVoiceResult,
    VoiceCharacterGroup,
    VoicePackManager,
    VoicePackSummary,
)
from galgame_anki.ui.workers import FunctionWorker


class VoicePackDialog(QDialog):
    """管理 Kirikiri (.xp3 + .scn) 或已解包目錄的純人聲索引，並支援按人物單獨匯出濃縮被動聽力 MP3。"""

    packs_changed = Signal()
    _export_progress = Signal(str)

    def __init__(
        self,
        config: AppConfig,
        voice_packs: VoicePackManager,
        thread_pool: QThreadPool,
        parent: QWidget | None = None,
        initial_tab: int = 0,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("📦 遊戲解包純淨人聲 & 🎧 角色分類濃縮聽力（0% BGM・0% 音效）")
        self.resize(780, 660)
        self.config = config
        self.voice_packs = voice_packs
        self.thread_pool = thread_pool
        self._workers: list[object] = []
        self._inspect_gen = 0
        self._inspect_timer = QTimer(self)
        self._inspect_timer.setSingleShot(True)
        self._inspect_timer.setInterval(260)
        self._inspect_timer.timeout.connect(self._run_dir_inspect)

        self._build_ui()
        self._export_progress.connect(self._on_condensed_progress)
        self._prefill_default_game_dir()
        self._refresh_pack_list()
        if 0 <= initial_tab < self.tabs.count():
            self.tabs.setCurrentIndex(initial_tab)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        intro = QLabel(
            "<span style='font-weight:500;'>100% 無背景音樂・無音效純淨人聲！</span> 直接從遊戲本體 <code>.xp3</code> 封包（免拆滿硬碟，支援 <span style='font-weight:500;'>ATRI 等 Kirikiri CxDec</span>）"
            "或 GARbro/FreeMote 已解包資料夾建立「<span style='font-weight:500;'>台詞 ↔ 純淨人聲</span>」索引；並可按<span style='font-weight:500;'>單一角色獨立匯出</span> AJATT 被動濃縮聽力 MP3！"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        # ==================== Tab 1: 遊戲索引建立與即時試聽 ====================
        tab_index = QWidget()
        il = QVBoxLayout(tab_index)
        il.setContentsMargins(6, 10, 6, 6)
        il.setSpacing(10)

        add_box = QFrame()
        add_box.setObjectName("CardPanel")
        al = QVBoxLayout(add_box)
        al.setSpacing(7)

        add_title = QLabel("1️⃣ 選擇遊戲目錄一鍵建立純人聲索引（無需外接 Windows 工具）")
        add_title.setStyleSheet("font-weight: 500; font-size: 13.5px; background: transparent;")
        al.addWidget(add_title)

        dir_row = QHBoxLayout()
        self.dir_edit = QLineEdit()
        self.dir_edit.setPlaceholderText("選擇遊戲主資料夾（例如 ATRI -My Dear Moments- v1.3）或已解包劇本/語音目錄…")
        self.dir_edit.textChanged.connect(self._inspect_selected_dir)
        dir_row.addWidget(self.dir_edit, 1)

        browse_btn = QPushButton("📂 瀏覽資料夾...")
        browse_btn.clicked.connect(self._browse_dir)
        dir_row.addWidget(browse_btn)
        al.addLayout(dir_row)

        self.detect_label = QLabel("請選擇遊戲目錄以自動偵測封包類型。")
        self.detect_label.setObjectName("MutedLabel")
        self.detect_label.setWordWrap(True)
        al.addWidget(self.detect_label)

        act_row = QHBoxLayout()
        self.index_btn = QPushButton("⚡ 一鍵掃描並建立純人聲索引")
        self.index_btn.setObjectName("PrimaryBtn")
        self.index_btn.clicked.connect(self._start_indexing)
        act_row.addWidget(self.index_btn)

        self.formats_lbl = QLabel("")
        self.formats_lbl.setObjectName("MutedLabel")
        act_row.addWidget(self.formats_lbl, 1)
        al.addLayout(act_row)
        self._refresh_formats_status()
        il.addWidget(add_box)

        list_box = QFrame()
        list_box.setObjectName("CardPanel")
        ll = QVBoxLayout(list_box)
        ll.setSpacing(7)

        ll_title = QLabel("2️⃣ 已安裝的純人聲遊戲索引（點選即可啟用／匯出實體解包檔）")
        ll_title.setStyleSheet("font-weight: 500; font-size: 13.5px; background: transparent;")
        ll.addWidget(ll_title)

        self.pack_list = QListWidget()
        self.pack_list.setMinimumHeight(105)
        self.pack_list.currentRowChanged.connect(self._on_pack_selected)
        ll.addWidget(self.pack_list)

        p_btns = QHBoxLayout()
        self.pin_pack_btn = QPushButton("📌 設為當前優先使用")
        self.pin_pack_btn.clicked.connect(self._pin_selected_pack)
        p_btns.addWidget(self.pin_pack_btn)

        self.go_condensed_btn = QPushButton("🎧 按角色匯出濃縮聽力 →")
        self.go_condensed_btn.setObjectName("AccentBtn")
        self.go_condensed_btn.clicked.connect(lambda: self.tabs.setCurrentIndex(1))
        p_btns.addWidget(self.go_condensed_btn)

        self.export_btn = QPushButton("💾 匯出散檔＋JSON...")
        self.export_btn.setToolTip("將封包內所有 .opus/.ogg 語音散檔與台詞對照 JSON 實體解壓至資料夾")
        self.export_btn.clicked.connect(self._export_selected_pack)
        p_btns.addWidget(self.export_btn)

        self.del_btn = QPushButton("🗑 移除索引")
        self.del_btn.clicked.connect(self._delete_selected_pack)
        p_btns.addWidget(self.del_btn)
        p_btns.addStretch()
        ll.addLayout(p_btns)
        il.addWidget(list_box, 1)

        test_box = QFrame()
        test_box.setObjectName("CardPanel")
        tl = QVBoxLayout(test_box)
        tl.setSpacing(6)

        t_title = QLabel("3️⃣ 即時實測：選擇／輸入遊戲對話台詞，試聽 0% BGM 純人聲")
        t_title.setStyleSheet("font-weight: 500; font-size: 13px; background: transparent;")
        tl.addWidget(t_title)

        sample_row = QHBoxLayout()
        self.sample_combo = QComboBox()
        self.sample_combo.setMinimumWidth(260)
        self.sample_combo.currentIndexChanged.connect(self._on_sample_picked)
        sample_row.addWidget(self.sample_combo, 1)
        tl.addLayout(sample_row)

        test_row = QHBoxLayout()
        self.test_edit = QLineEdit()
        self.test_edit.setPlaceholderText("輸入或從上方挑選一句劇本台詞測試純人聲（例如：「よろしくお願いしますね、夏生さん」）")
        test_row.addWidget(self.test_edit, 1)

        self.test_play_btn = QPushButton("🔊 試聽純淨人聲")
        self.test_play_btn.setObjectName("MineBtn")
        self.test_play_btn.clicked.connect(self._test_selected_line)
        test_row.addWidget(self.test_play_btn)
        tl.addLayout(test_row)

        self.test_status = QLabel("")
        self.test_status.setObjectName("MutedLabel")
        self.test_status.setWordWrap(True)
        tl.addWidget(self.test_status)
        il.addWidget(test_box)

        self.tabs.addTab(tab_index, "📦 遊戲索引與試聽")

        # ==================== Tab 2: 角色分類濃縮純人聲被動聽力 ====================
        tab_cond = QWidget()
        cl = QVBoxLayout(tab_cond)
        cl.setContentsMargins(6, 10, 6, 6)
        cl.setSpacing(10)

        cond_hint = QLabel(
            "🔒 <span style='font-weight:500;'>人物聲音嚴格分軌：</span>只匯出您勾選的喜歡角色；若勾選多位角色，會為<span style='font-weight:500;'>每位角色各自輸出獨立 MP3</span>"
            "（絕不把兩個人的聲音混在同一份音檔中，並自動排除多人合聲與無聲過場），讓通勤／散步被動聽力 100% 純淨！"
        )
        cond_hint.setWordWrap(True)
        cl.addWidget(cond_hint)

        filter_box = QFrame()
        filter_box.setObjectName("CardPanel")
        fl = QVBoxLayout(filter_box)
        fl.setSpacing(8)

        row_pack = QHBoxLayout()
        row_pack.addWidget(QLabel("目標遊戲："))
        self.cond_pack_combo = QComboBox()
        self.cond_pack_combo.currentIndexChanged.connect(self._on_cond_pack_changed)
        row_pack.addWidget(self.cond_pack_combo, 1)
        fl.addLayout(row_pack)

        row_scene = QHBoxLayout()
        row_scene.addWidget(QLabel("防劇透章節範圍："))
        self.start_scene_combo = QComboBox()
        self.start_scene_combo.currentIndexChanged.connect(self._refresh_character_list)
        row_scene.addWidget(self.start_scene_combo, 1)
        row_scene.addWidget(QLabel("～"))
        self.end_scene_combo = QComboBox()
        self.end_scene_combo.currentIndexChanged.connect(self._refresh_character_list)
        row_scene.addWidget(self.end_scene_combo, 1)
        fl.addLayout(row_scene)

        row_opts = QHBoxLayout()
        row_opts.addWidget(QLabel("句間停頓："))
        self.gap_spin = QDoubleSpinBox()
        self.gap_spin.setRange(0.0, 2.5)
        self.gap_spin.setSingleStep(0.1)
        self.gap_spin.setValue(0.4)
        self.gap_spin.setSuffix(" 秒")
        row_opts.addWidget(self.gap_spin)

        self.lrc_chk = QCheckBox("同步產生 .lrc 滾動歌詞與台詞對照清單 (.txt)")
        self.lrc_chk.setChecked(True)
        row_opts.addWidget(self.lrc_chk, 1)
        fl.addLayout(row_opts)
        cl.addWidget(filter_box)

        char_box = QFrame()
        char_box.setObjectName("CardPanel")
        chl = QVBoxLayout(char_box)
        chl.setSpacing(7)

        char_hdr = QHBoxLayout()
        char_title = QLabel("👤 人物聲音分類（勾選想匯出的角色，預設只勾選第 1 位主角）")
        char_title.setStyleSheet("font-weight: 500; font-size: 13.5px; background: transparent;")
        char_hdr.addWidget(char_title)
        char_hdr.addStretch()

        only_cur_btn = QPushButton("只勾選目前這位")
        only_cur_btn.clicked.connect(self._check_only_current_char)
        char_hdr.addWidget(only_cur_btn)

        sel_none_btn = QPushButton("全不選")
        sel_none_btn.clicked.connect(lambda: self._set_all_chars_checked(False))
        char_hdr.addWidget(sel_none_btn)

        sel_all_btn = QPushButton("全選(獨立分檔)")
        sel_all_btn.clicked.connect(lambda: self._set_all_chars_checked(True))
        char_hdr.addWidget(sel_all_btn)
        chl.addLayout(char_hdr)

        self.char_list = QListWidget()
        self.char_list.setMinimumHeight(180)
        chl.addWidget(self.char_list, 1)

        cond_act = QHBoxLayout()
        self.export_condensed_btn = QPushButton("🎧 匯出勾選角色的濃縮純人聲 MP3...")
        self.export_condensed_btn.setObjectName("MineBtn")
        self.export_condensed_btn.clicked.connect(self._export_condensed_audio)
        cond_act.addWidget(self.export_condensed_btn)
        cond_act.addStretch()
        chl.addLayout(cond_act)

        self.cond_status = QLabel("提示：建議只勾選 1～2 位最常聽的角色，每位角色會輸出獨立的 MP3 檔案。")
        self.cond_status.setObjectName("MutedLabel")
        self.cond_status.setWordWrap(True)
        chl.addWidget(self.cond_status)
        cl.addWidget(char_box, 1)

        self.tabs.addTab(tab_cond, "🎧 角色分類濃縮聽力 MP3")

        bottom = QHBoxLayout()
        bottom.addStretch()
        close_btn = QPushButton("關閉")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

    # === Tab 1: 索引建立與試聽 ===
    def _prefill_default_game_dir(self) -> None:
        if self.config.recent_games:
            exe = Path(self.config.recent_games[0].get("path", ""))
            if exe.parent.exists():
                self.dir_edit.setText(str(exe.parent))
                return
        packs = self.voice_packs.list_packs()
        if packs:
            self.dir_edit.setText(packs[0].game_dir)

    def _refresh_formats_status(self) -> None:
        dat = cached_formats_dat_path()
        if dat.exists():
            self.formats_lbl.setText(f"🔑 本機已快取 Formats.dat ({dat.stat().st_size / 1024:.0f} KB)")
        else:
            self.formats_lbl.setText("🔑 首次解析加密 XP3 時將自動快取公開 Formats.dat")

    def _browse_dir(self) -> None:
        start = self.dir_edit.text().strip() or str(Path.home())
        if not Path(start).exists():
            start = str(Path.home())
        d = QFileDialog.getExistingDirectory(self, "選擇遊戲本體資料夾或已解包目錄", start)
        if d:
            self.dir_edit.setText(d)

    def _inspect_selected_dir(self, path_str: str) -> None:
        p = Path(path_str.strip())
        if not path_str.strip() or not p.is_dir():
            self._inspect_timer.stop()
            self._inspect_gen += 1
            self.detect_label.setText("請選擇存在的遊戲資料夾。")
            return
        self.detect_label.setText("正在掃描資料夾內容…")
        self._inspect_timer.start()

    def _run_dir_inspect(self) -> None:
        p = Path(self.dir_edit.text().strip())
        if not p.is_dir():
            self.detect_label.setText("請選擇存在的遊戲資料夾。")
            return
        self._inspect_gen += 1
        gen = self._inspect_gen
        w = FunctionWorker(self.voice_packs.detect_game_dir_capabilities, p)
        w.signals.finished.connect(lambda cap, g=gen: self._on_dir_inspect_done(cap, g))
        w.signals.failed.connect(lambda err, g=gen: self.detect_label.setText(f"掃描失敗：{err}") if g == self._inspect_gen else None)
        self._workers.append(w)
        self.thread_pool.start(w)

    def _on_dir_inspect_done(self, cap: dict, gen: int) -> None:
        if gen != self._inspect_gen:
            return
        parts: list[str] = []
        if cap["xp3_count"] > 0:
            names = ", ".join(cap["xp3_names"][:5])
            scheme_tag = f" · 命中解密參數《{cap['cx_scheme_title']}》" if cap["cx_scheme_title"] else " · 建索引時自動比對解密參數"
            parts.append(f"偵測到 {cap['xp3_count']} 個 XP3 封包 ({names}){scheme_tag}")
        if cap["loose_script_count"] > 0 or cap["loose_audio_count"] > 0:
            parts.append(
                f"偵測到已解包檔案：{cap['loose_script_count']} 個劇本 / {cap['loose_audio_count']} 個音檔"
            )
        if not parts:
            parts.append("此目錄未直接掃到 .xp3 或劇本/語音，請確認選到遊戲安裝根目錄。")
        self.detect_label.setText(" ｜ ".join(parts))

    def _start_indexing(self) -> None:
        p = Path(self.dir_edit.text().strip())
        if not p.is_dir():
            QMessageBox.warning(self, "目錄無效", "請先選擇有效的遊戲資料夾。")
            return

        self.index_btn.setEnabled(False)
        self.index_btn.setText("⏳ 索引建立中…")
        self.detect_label.setText("正在掃描封包／劇本與建立 SQLite 純人聲對照索引，請稍候 2～8 秒...")

        def _work() -> VoicePackSummary:
            return self.voice_packs.index_game_directory(p)

        w = FunctionWorker(_work)
        w.signals.finished.connect(self._on_index_done)
        w.signals.failed.connect(self._on_index_fail)
        self._workers.append(w)
        self.thread_pool.start(w)

    def _on_index_done(self, summary: VoicePackSummary) -> None:
        self.index_btn.setEnabled(True)
        self.index_btn.setText("⚡ 一鍵掃描並建立純人聲索引")
        self.config.active_voice_pack_id = summary.pack_id
        self.config.save()
        self._refresh_formats_status()
        self._refresh_pack_list(select_pack_id=summary.pack_id)
        self.packs_changed.emit()
        self.detect_label.setText(
            f"✅ 完成《{summary.game_name}》純人聲索引！共收錄 {summary.line_count} 句台詞、"
            f"{summary.voiced_line_count} 條配音關聯、{summary.asset_count} 個語音資源。"
        )

    def _on_index_fail(self, err: str) -> None:
        self.index_btn.setEnabled(True)
        self.index_btn.setText("⚡ 一鍵掃描並建立純人聲索引")
        self.detect_label.setText(f"❌ 建立索引失敗：{err}")
        QMessageBox.warning(self, "建立純人聲索引失敗", err)

    def _refresh_pack_list(self, select_pack_id: str | None = None) -> None:
        self.pack_list.blockSignals(True)
        self.pack_list.clear()
        self.cond_pack_combo.blockSignals(True)
        self.cond_pack_combo.clear()

        target_row = 0
        packs = self.voice_packs.list_packs()
        active_id = self.config.active_voice_pack_id
        for row, pk in enumerate(packs):
            mode_tag = "XP3 免解壓直讀" if pk.mode == "xp3_cx" else "已解包目錄"
            pin_tag = "📌 使用中 · " if pk.pack_id == active_id else ""
            item = QListWidgetItem(
                f"{pin_tag}{pk.game_name}　[{mode_tag}]　台詞 {pk.line_count} 句 · 有聲 {pk.voiced_line_count} 句 · 音檔 {pk.asset_count} 個"
            )
            item.setData(Qt.ItemDataRole.UserRole, pk)
            self.pack_list.addItem(item)
            self.cond_pack_combo.addItem(f"{pk.game_name}（{pk.voiced_line_count} 句有聲）", pk.pack_id)
            if select_pack_id and pk.pack_id == select_pack_id:
                target_row = row
            elif not select_pack_id and active_id and pk.pack_id == active_id:
                target_row = row

        self.pack_list.blockSignals(False)
        self.cond_pack_combo.blockSignals(False)
        if self.pack_list.count() > 0:
            self.pack_list.setCurrentRow(target_row)
            self._on_pack_selected(target_row)
            self.cond_pack_combo.setCurrentIndex(target_row)
            self._on_cond_pack_changed(target_row)
        else:
            self.sample_combo.clear()
            self.start_scene_combo.clear()
            self.end_scene_combo.clear()
            self.char_list.clear()

    def _current_pack(self) -> VoicePackSummary | None:
        item = self.pack_list.currentItem()
        if not item:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _on_pack_selected(self, _row: int) -> None:
        pk = self._current_pack()
        self.sample_combo.blockSignals(True)
        self.sample_combo.clear()
        if pk is not None:
            for s in self.voice_packs.get_sample_voiced_lines(pk.pack_id, limit=12):
                spk = f"[{s['speaker']}] " if s["speaker"] else ""
                vtag = ", ".join(s["voices"][:2])
                label = f"{spk}{s['text'][:34]}  ({vtag})"
                self.sample_combo.addItem(label, s["text"])
        self.sample_combo.blockSignals(False)
        if self.sample_combo.count() > 0:
            self._on_sample_picked(0)

    def _on_sample_picked(self, idx: int) -> None:
        if idx >= 0:
            txt = self.sample_combo.itemData(idx)
            if txt:
                self.test_edit.setText(str(txt))

    def _pin_selected_pack(self) -> None:
        pk = self._current_pack()
        if pk is None:
            return
        self.config.active_voice_pack_id = pk.pack_id
        self.config.save()
        self._refresh_pack_list(select_pack_id=pk.pack_id)
        self.packs_changed.emit()

    def _delete_selected_pack(self) -> None:
        pk = self._current_pack()
        if pk is None:
            return
        self.voice_packs.delete_pack(pk.pack_id)
        if self.config.active_voice_pack_id == pk.pack_id:
            self.config.active_voice_pack_id = ""
            self.config.save()
        self._refresh_pack_list()
        self.packs_changed.emit()

    def _export_selected_pack(self) -> None:
        pk = self._current_pack()
        if pk is None:
            return
        out = QFileDialog.getExistingDirectory(
            self,
            f"選擇「{pk.game_name}」純淨語音與劇本匯出資料夾",
            str(Path.home()),
        )
        if not out:
            return
        self.export_btn.setEnabled(False)
        self.export_btn.setText("⏳ 匯出中…")

        def _do() -> dict:
            return self.voice_packs.export_pack_assets(pk.pack_id, Path(out))

        w = FunctionWorker(_do)
        w.signals.finished.connect(self._on_export_done)
        w.signals.failed.connect(self._on_export_fail)
        self._workers.append(w)
        self.thread_pool.start(w)

    def _on_export_done(self, res: dict) -> None:
        self.export_btn.setEnabled(True)
        self.export_btn.setText("💾 匯出散檔＋JSON...")
        QMessageBox.information(
            self,
            "匯出完成",
            f"已匯出 {res['exported_voices']} 個純淨語音檔至：\n{res['output_dir']}/voice\n\n"
            f"台詞與語音對照表（{res['line_count']} 句）：\n{res['script_json']}",
        )

    def _on_export_fail(self, err: str) -> None:
        self.export_btn.setEnabled(True)
        self.export_btn.setText("💾 匯出散檔＋JSON...")
        QMessageBox.warning(self, "匯出失敗", err)

    def _test_selected_line(self) -> None:
        pk = self._current_pack()
        txt = self.test_edit.text().strip()
        if pk is None or not txt:
            return
        self.test_play_btn.setEnabled(False)
        self.test_status.setText("⏳ 正在直接從遊戲封包解密並轉換純淨人聲…")

        def _do() -> MatchedVoiceResult:
            return self.voice_packs.match_and_extract_voice(txt, pack_id=pk.pack_id)

        w = FunctionWorker(_do)
        w.signals.finished.connect(self._on_test_done)
        w.signals.failed.connect(self._on_test_fail)
        self._workers.append(w)
        self.thread_pool.start(w)

    def _on_test_done(self, res: MatchedVoiceResult) -> None:
        self.test_play_btn.setEnabled(True)
        if res.has_voice and res.audio_clip:
            mp3, _ = res.audio_clip
            play_mp3_bytes_async(mp3)
            vtags = ", ".join(res.voice_ids)
            spk = f"{res.speaker}：" if res.speaker else ""
            tr = f" ｜ 譯文：{res.translation}" if res.translation else ""
            self.test_status.setText(
                f"✅ 播放純淨原音中！（{spk}{vtags} · {len(mp3) / 1024:.1f} KB · 0% BGM/音效{tr}）"
            )
        elif res.matched_line:
            self.test_status.setText("ℹ 此句在劇本中為無配音旁白。")
        else:
            self.test_status.setText("⚠ 未在目前索引的劇本中找到完全相符的台詞。")

    def _on_test_fail(self, err: str) -> None:
        self.test_play_btn.setEnabled(True)
        self.test_status.setText(f"❌ 試聽失敗：{err}")

    # === Tab 2: 角色分類濃縮聽力 MP3 ===
    def _on_cond_pack_changed(self, idx: int) -> None:
        pack_id = str(self.cond_pack_combo.itemData(idx) or "")
        self.start_scene_combo.blockSignals(True)
        self.end_scene_combo.blockSignals(True)
        self.start_scene_combo.clear()
        self.end_scene_combo.clear()
        self.start_scene_combo.addItem("（從開頭）", "")
        self.end_scene_combo.addItem("（到結尾）", "")
        if pack_id:
            for sc in self.voice_packs.list_pack_scenes(pack_id):
                self.start_scene_combo.addItem(sc, sc)
                self.end_scene_combo.addItem(sc, sc)
        self.start_scene_combo.blockSignals(False)
        self.end_scene_combo.blockSignals(False)
        self._refresh_character_list()

    def _refresh_character_list(self) -> None:
        pack_id = str(self.cond_pack_combo.currentData() or "")
        self.char_list.clear()
        if not pack_id:
            return
        start_sc = str(self.start_scene_combo.currentData() or "")
        end_sc = str(self.end_scene_combo.currentData() or "")
        chars: list[VoiceCharacterGroup] = self.voice_packs.list_pack_characters(
            pack_id,
            start_scene=start_sc,
            end_scene=end_sc,
        )
        for idx, ch in enumerate(chars):
            pref_tag = f" [{ch.voice_prefix}]" if ch.voice_prefix else ""
            item = QListWidgetItem(
                f"{ch.display_name}{pref_tag}　—　{ch.solo_line_count} 句單人純人聲（跨 {ch.scene_count} 個章節）"
            )
            item.setData(Qt.ItemDataRole.UserRole, ch)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            # 預設僅勾選第 1 位主角（例如 アトリ），避免不小心把所有角色全混在一起匯出
            item.setCheckState(Qt.CheckState.Checked if idx == 0 else Qt.CheckState.Unchecked)
            self.char_list.addItem(item)
        if chars:
            self.char_list.setCurrentRow(0)
            self.cond_status.setText(
                f"已分類出 {len(chars)} 位獨立角色（皆僅含單人獨白，已自動過濾多人合聲）；勾選喜歡的角色即可獨立匯出專屬 MP3。"
            )
        else:
            self.cond_status.setText("目前範圍內沒有單人純人聲。")

    def _set_all_chars_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self.char_list.count()):
            self.char_list.item(i).setCheckState(state)

    def _check_only_current_char(self) -> None:
        cur = self.char_list.currentRow()
        if cur < 0:
            return
        for i in range(self.char_list.count()):
            self.char_list.item(i).setCheckState(
                Qt.CheckState.Checked if i == cur else Qt.CheckState.Unchecked
            )

    def _selected_char_keys(self) -> list[str]:
        keys: list[str] = []
        for i in range(self.char_list.count()):
            it = self.char_list.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                ch: VoiceCharacterGroup | None = it.data(Qt.ItemDataRole.UserRole)
                if ch is not None:
                    keys.append(ch.char_key)
        return keys

    def _export_condensed_audio(self) -> None:
        pack_id = str(self.cond_pack_combo.currentData() or "")
        char_keys = self._selected_char_keys()
        if not pack_id:
            QMessageBox.warning(self, "尚未選擇遊戲", "請先於第一分頁建立遊戲純人聲索引。")
            return
        if not char_keys:
            QMessageBox.warning(self, "尚未勾選角色", "請至少勾選 1 位想要匯出純人聲的角色！")
            return

        out_dir = QFileDialog.getExistingDirectory(
            self,
            "選擇濃縮純人聲 MP3 匯出資料夾（每位角色將各存成獨立 MP3）",
            str(Path.home()),
        )
        if not out_dir:
            return

        start_sc = str(self.start_scene_combo.currentData() or "")
        end_sc = str(self.end_scene_combo.currentData() or "")
        gap_sec = float(self.gap_spin.value())
        write_lrc = self.lrc_chk.isChecked()

        self.export_condensed_btn.setEnabled(False)
        self.export_condensed_btn.setText("⏳ 正在按角色獨立轉碼拼接…")
        self.cond_status.setText("⏳ 正在解碼並串接所選角色的純淨人聲，請稍候...")

        def _work() -> dict:
            return self.voice_packs.export_condensed_character_audio(
                pack_id=pack_id,
                char_keys=char_keys,
                output_dir=Path(out_dir),
                start_scene=start_sc,
                end_scene=end_sc,
                gap_seconds=gap_sec,
                write_lrc=write_lrc,
                progress_cb=lambda msg: self._export_progress.emit(msg),
            )

        w = FunctionWorker(_work)
        w.signals.finished.connect(self._on_condensed_done)
        w.signals.failed.connect(self._on_condensed_fail)
        self._workers.append(w)
        self.thread_pool.start(w)

    def _on_condensed_progress(self, msg: str) -> None:
        self.cond_status.setText(f"⏳ {msg}")

    def _on_condensed_done(self, res: dict) -> None:
        self.export_condensed_btn.setEnabled(True)
        self.export_condensed_btn.setText("🎧 匯出勾選角色的濃縮純人聲 MP3...")
        files = res.get("files", [])
        lines_summary = [
            f"• {f['char_name']}：{f['line_count']} 句（{f['duration_sec'] / 60:.1f} 分鐘 · {f['size_bytes'] / (1024 * 1024):.1f} MB）\n  {f['mp3_path']}"
            for f in files
        ]
        self.cond_status.setText(f"✅ 已完成 {len(files)} 位角色的獨立純人聲 MP3 匯出（絕無混音）！")
        QMessageBox.information(
            self,
            "濃縮純人聲匯出完成",
            f"已依人物獨立分檔匯出至：{res['output_dir']}\n\n" + "\n".join(lines_summary),
        )

    def _on_condensed_fail(self, err: str) -> None:
        self.export_condensed_btn.setEnabled(True)
        self.export_condensed_btn.setText("🎧 匯出勾選角色的濃縮純人聲 MP3...")
        self.cond_status.setText(f"❌ 匯出失敗：{err}")
        QMessageBox.warning(self, "濃縮純人聲匯出失敗", err)
