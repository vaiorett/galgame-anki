"""📦 遊戲解包與純淨人聲（零 BGM・零音效）索引管理視窗。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
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
from galgame_anki.adapters.xp3_psb import cached_formats_dat_path
from galgame_anki.config import AppConfig
from galgame_anki.core.voice_pack import MatchedVoiceResult, VoicePackManager, VoicePackSummary
from galgame_anki.ui.workers import FunctionWorker


class VoicePackDialog(QDialog):
    """讓使用者一鍵掃描 Kirikiri (.xp3 + .scn) 或已解包遊戲目錄，打造 0% BGM/音效 純人聲體驗。"""

    packs_changed = Signal()

    def __init__(
        self,
        config: AppConfig,
        voice_packs: VoicePackManager,
        thread_pool: QThreadPool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("📦 遊戲解包與純淨人聲（0% BGM・0% 音效）")
        self.resize(740, 620)
        self.config = config
        self.voice_packs = voice_packs
        self.thread_pool = thread_pool
        self._workers: list[object] = []

        self._build_ui()
        self._prefill_default_game_dir()
        self._refresh_pack_list()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        intro = QLabel(
            "<b>100% 無背景音樂・無音效純淨人聲！</b> 直接從遊戲本體 <code>.xp3</code> 封包（免拆滿硬碟，支援 <b>ATRI 等 Kirikiri CxDec</b>）"
            "或 GARbro/FreeMote 已解包資料夾建立「<b>台詞 ↔ 純淨人聲</b>」索引。製卡與試聽時自動抽出聲優原聲！"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # === 區塊 1：新增 / 更新遊戲索引 ===
        add_box = QFrame()
        add_box.setObjectName("CardPanel")
        al = QVBoxLayout(add_box)
        al.setSpacing(7)

        add_title = QLabel("1️⃣ 選擇遊戲目錄一鍵建立純人聲索引（無需外接 Windows 工具）")
        add_title.setStyleSheet("font-weight: 700; font-size: 13.5px; background: transparent;")
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
        layout.addWidget(add_box)

        # === 區塊 2：已建立的遊戲純人聲索引包 ===
        list_box = QFrame()
        list_box.setObjectName("CardPanel")
        ll = QVBoxLayout(list_box)
        ll.setSpacing(7)

        ll_title = QLabel("2️⃣ 已安裝的純人聲遊戲索引（點選即可啟用／匯出實體解包檔）")
        ll_title.setStyleSheet("font-weight: 700; font-size: 13.5px; background: transparent;")
        ll.addWidget(ll_title)

        self.pack_list = QListWidget()
        self.pack_list.setMinimumHeight(115)
        self.pack_list.currentRowChanged.connect(self._on_pack_selected)
        ll.addWidget(self.pack_list)

        p_btns = QHBoxLayout()
        self.pin_pack_btn = QPushButton("📌 設為當前優先使用")
        self.pin_pack_btn.clicked.connect(self._pin_selected_pack)
        p_btns.addWidget(self.pin_pack_btn)

        self.export_btn = QPushButton("💾 匯出純淨語音＋劇本資料夾...")
        self.export_btn.setToolTip("若你想直接把所有 .opus/.ogg 純人聲與台詞對照 JSON 實體解壓出來保存，點這裡")
        self.export_btn.clicked.connect(self._export_selected_pack)
        p_btns.addWidget(self.export_btn)

        self.del_btn = QPushButton("🗑 移除索引")
        self.del_btn.clicked.connect(self._delete_selected_pack)
        p_btns.addWidget(self.del_btn)
        p_btns.addStretch()
        ll.addLayout(p_btns)
        layout.addWidget(list_box, 1)

        # === 區塊 3：即時台詞配對 & 純淨原聲試聽 ===
        test_box = QFrame()
        test_box.setObjectName("CardPanel")
        tl = QVBoxLayout(test_box)
        tl.setSpacing(6)

        t_title = QLabel("3️⃣ 即時實測：選擇／輸入遊戲對話台詞，試聽 0% BGM 純人聲")
        t_title.setStyleSheet("font-weight: 700; font-size: 13px; background: transparent;")
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
        layout.addWidget(test_box)

        bottom = QHBoxLayout()
        bottom.addStretch()
        close_btn = QPushButton("關閉")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

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
        if not p.is_dir():
            self.detect_label.setText("請選擇存在的遊戲資料夾。")
            return
        cap = self.voice_packs.detect_game_dir_capabilities(p)
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
        self.detect_label.setText("🔍 " + " ｜ ".join(parts))

    def _start_indexing(self) -> None:
        p = Path(self.dir_edit.text().strip())
        if not p.is_dir():
            QMessageBox.warning(self, "目錄無效", "請先選擇有效的遊戲資料夾。")
            return

        self.index_btn.setEnabled(False)
        self.index_btn.setText("⏳ 索引建立中…")
        self.detect_label.setText("正在掃描封包／劇本與建立 SQLite 純人聲對照索引，請稍候 2～5 秒...")

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
            if select_pack_id and pk.pack_id == select_pack_id:
                target_row = row
            elif not select_pack_id and active_id and pk.pack_id == active_id:
                target_row = row
        self.pack_list.blockSignals(False)
        if self.pack_list.count() > 0:
            self.pack_list.setCurrentRow(target_row)
            self._on_pack_selected(target_row)
        else:
            self.sample_combo.clear()

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
        self.export_btn.setText("💾 匯出純淨語音＋劇本資料夾...")
        QMessageBox.information(
            self,
            "匯出完成",
            f"已匯出 {res['exported_voices']} 個純淨語音檔至：\n{res['output_dir']}/voice\n\n"
            f"台詞與語音對照表（{res['line_count']} 句）：\n{res['script_json']}",
        )

    def _on_export_fail(self, err: str) -> None:
        self.export_btn.setEnabled(True)
        self.export_btn.setText("💾 匯出純淨語音＋劇本資料夾...")
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
