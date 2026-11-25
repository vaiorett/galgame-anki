"""輕量化彈出視窗：Yomitan 詞典管理視窗 & 原攻略 Galgame 生肉難度與避雷指南。"""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from galgame_anki.core.dictionary import FREE_DICTIONARY_CATALOG, DictionaryEngine
from galgame_anki.core.sentence_store import load_difficulty_guide
from galgame_anki.ui.theme import get_palette
from galgame_anki.ui.workers import DictBundleDownloadWorker, DictDownloadWorker, DictImportWorker


class DictionaryManagerDialog(QDialog):
    """獨立輕量詞典管理視窗（每部詞典獨立開關、優先序調整、一鍵階段預設與明確的已安裝狀態）。"""

    dictionary_updated = Signal()

    def __init__(
        self,
        dictionary: DictionaryEngine,
        thread_pool: QThreadPool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("📚 詞典管理（獨立開關・優先序・相容 Yomitan .zip）")
        self.resize(740, 680)
        self.dictionary = dictionary
        self.thread_pool = thread_pool
        self._workers: list[object] = []
        self._catalog_rows: dict[str, tuple[QLabel, QPushButton, dict]] = {}

        self._build_ui()
        self._refresh_list()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        top = QHBoxLayout()
        title = QLabel("已安裝本地詞典（勾選即可單獨開啟／停用，不需全部開啟！）")
        title.setWordWrap(True)
        title.setStyleSheet("font-weight: 700;")
        top.addWidget(title, 1)

        up_btn = QPushButton("⬆ 上移")
        up_btn.setToolTip("提高選取詞典的查詢與釋義優先順序")
        up_btn.clicked.connect(lambda: self._move_selected(-1))
        top.addWidget(up_btn)

        down_btn = QPushButton("⬇ 下移")
        down_btn.setToolTip("降低選取詞典的查詢與釋義優先順序")
        down_btn.clicked.connect(lambda: self._move_selected(1))
        top.addWidget(down_btn)

        import_btn = QPushButton("📂 匯入 .zip")
        import_btn.clicked.connect(self._import_local)
        top.addWidget(import_btn)

        del_btn = QPushButton("🗑 移除")
        del_btn.clicked.connect(self._delete_selected)
        top.addWidget(del_btn)
        layout.addLayout(top)

        self.dict_list = QListWidget()
        self.dict_list.setMinimumHeight(145)
        self.dict_list.setMaximumHeight(185)
        self.dict_list.itemChanged.connect(self._on_item_toggled)
        layout.addWidget(self.dict_list)

        # 分離「查詢可用性」與「顯示優先序」：保留日日與單一日英備援供語塊查詞，不因切中文而查不到慣用語
        preset_row = QHBoxLayout()
        preset_lbl = QLabel("排序預設：")
        preset_lbl.setObjectName("MutedLabel")
        preset_row.addWidget(preset_lbl)

        zh_btn = QPushButton("繁中優先（保留備援）")
        zh_btn.setToolTip("繁中主力優先顯示，同時保留日日與單一日英備援以確保慣用語語塊完整可查")
        zh_btn.clicked.connect(lambda: self._apply_preset("zh_focus", "已套用「繁中優先」：中文優先顯示，並保留日日與單一日英備援！"))
        preset_row.addWidget(zh_btn)

        mono_btn = QPushButton("日日優先（Lv.3）")
        mono_btn.setToolTip("日日單語優先顯示，並保留繁中與單一日英備援")
        mono_btn.clicked.connect(lambda: self._apply_preset("mono_focus", "已套用「日日優先」排序組合！"))
        preset_row.addWidget(mono_btn)

        all_btn = QPushButton("全部啟用")
        all_btn.clicked.connect(lambda: self._apply_preset("all", "已啟用全部已安裝詞典。"))
        preset_row.addWidget(all_btn)

        en_lbl = QLabel("日英備援：")
        en_lbl.setObjectName("MutedLabel")
        preset_row.addWidget(en_lbl)

        self.en_fallback_combo = QComboBox()
        self.en_fallback_combo.addItem("JMdict（推薦）", "jmdict")
        self.en_fallback_combo.addItem("Jitendex", "jitendex")
        self.en_fallback_combo.addItem("兩套並用", "both")
        self.en_fallback_combo.addItem("停用日英", "none")
        self.en_fallback_combo.setToolTip("選擇單一日英備援來源，便於在 JMdict 與 Jitendex 間實測與回退")
        self.en_fallback_combo.currentIndexChanged.connect(self._on_en_fallback_changed)
        preset_row.addWidget(self.en_fallback_combo)
        preset_row.addStretch()
        layout.addLayout(preset_row)

        # --- 開源詞典下載與安裝狀態面板 ---
        panel = QFrame()
        panel.setObjectName("CardPanel")
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(12, 10, 12, 10)
        pl.setSpacing(7)

        self.bundle_btn = QPushButton("🌟 一鍵安裝最佳黃金搭配（日中 + 日日 + Jitendex + Kanjium音調 + JPDB詞頻）")
        self.bundle_btn.setObjectName("MineBtn")
        self.bundle_btn.clicked.connect(self._download_bundle)
        pl.addWidget(self.bundle_btn)

        sub = QLabel("開源詞典安裝狀態（已安裝的詞典會顯示 ✅，無需重複下載；請在上方勾選啟用或停用）：")
        sub.setObjectName("MutedLabel")
        pl.addWidget(sub)

        self.dl_btns: list[QPushButton] = [self.bundle_btn]
        for spec in FREE_DICTIONARY_CATALOG:
            row = QHBoxLayout()
            lbl = QLabel("")
            lbl.setWordWrap(True)
            row.addWidget(lbl, 1)

            btn = QPushButton("⬇ 安裝")
            btn.clicked.connect(lambda _c=False, cid=spec["id"]: self._download(cid))
            self.dl_btns.append(btn)
            row.addWidget(btn)
            self._catalog_rows[spec["id"]] = (lbl, btn, spec)
            pl.addLayout(row)

        self.status_lbl = QLabel("就緒")
        self.status_lbl.setObjectName("MutedLabel")
        pl.addWidget(self.status_lbl)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        pl.addWidget(self.progress)

        layout.addWidget(panel)

        bottom = QHBoxLayout()
        bottom.addStretch()
        close_btn = QPushButton("完成")
        close_btn.setObjectName("PrimaryBtn")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

    def _refresh_list(self) -> None:
        self.dict_list.blockSignals(True)
        self.dict_list.clear()
        kind_map = {"bilingual": "雙語", "monolingual": "日日單語", "meta": "音調/詞頻"}
        for rank, d in enumerate(self.dictionary.list_dictionaries(), start=1):
            kind_label = kind_map.get(d["kind"], d["kind"])
            rev_txt = f" · {d['revision']}" if d.get("revision") else ""
            state_txt = f"已啟用 · #{rank}" if d["enabled"] else "已停用"
            item = QListWidgetItem(
                f"[{kind_label}] {d['name']}{rev_txt}  —  {d['entry_count']:,} 筆（{state_txt}）"
            )
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if d["enabled"] else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, d["id"])
            self.dict_list.addItem(item)
        self.dict_list.blockSignals(False)

        en_mode = self.dictionary.get_en_fallback_source()
        self.en_fallback_combo.blockSignals(True)
        en_idx = self.en_fallback_combo.findData(en_mode)
        if en_idx >= 0:
            self.en_fallback_combo.setCurrentIndex(en_idx)
        self.en_fallback_combo.blockSignals(False)

        # 同步更新下方每一部開源詞典的「✅ 已安裝」狀態！
        bundle_all_installed = True
        for cid, (lbl, btn, spec) in self._catalog_rows.items():
            installed = self.dictionary.find_installed_for_catalog(cid)
            if installed is not None:
                en_badge = "已啟用" if installed["enabled"] else "已停用（可在上方勾選開啟）"
                lbl.setText(
                    f"<b>{spec['name']}</b> "
                    f"<span style='color:#059669; font-weight:700;'>✅ 已安裝（{installed['entry_count']:,} 筆 · {en_badge}）</span>"
                    f"<br><span style='font-size:12px; opacity:0.78;'>{spec['description']}</span>"
                )
                btn.setText("↻ 重裝")
            else:
                if spec.get("recommended_bundle"):
                    bundle_all_installed = False
                lbl.setText(
                    f"<b>{spec['name']}</b> "
                    f"<span style='opacity:0.65;'>⬜ 未安裝</span>"
                    f"<br><span style='font-size:12px; opacity:0.78;'>{spec['description']}</span>"
                )
                btn.setText("⬇ 安裝")

        if bundle_all_installed:
            self.bundle_btn.setText("✅ 最佳黃金 5 合 1 詞典已全部安裝（點擊可重新更新，上方可單獨勾選開關）")
            self.bundle_btn.setObjectName("")
        else:
            self.bundle_btn.setText("🌟 一鍵安裝最佳黃金搭配（日中雙語 + 日日單語 + Jitendex + Kanjium音調 + JPDB詞頻）")
            self.bundle_btn.setObjectName("MineBtn")
        self.bundle_btn.style().unpolish(self.bundle_btn)
        self.bundle_btn.style().polish(self.bundle_btn)

    def _on_item_toggled(self, item: QListWidgetItem) -> None:
        dict_id = item.data(Qt.ItemDataRole.UserRole)
        if dict_id is None:
            return
        enabled = item.checkState() == Qt.CheckState.Checked
        self.dictionary.set_dictionary_enabled(int(dict_id), enabled)
        self._refresh_list()
        self.dictionary_updated.emit()
        self.status_lbl.setText("✓ 已更新詞典啟用狀態（立即套用於查詞與分詞）。")

    def _move_selected(self, direction: int) -> None:
        row = self.dict_list.currentRow()
        item = self.dict_list.currentItem()
        if not item:
            return
        dict_id = int(item.data(Qt.ItemDataRole.UserRole))
        self.dictionary.move_dictionary_priority(dict_id, direction)
        self._refresh_list()
        new_row = max(0, min(self.dict_list.count() - 1, row + direction))
        self.dict_list.setCurrentRow(new_row)
        self.dictionary_updated.emit()
        self.status_lbl.setText("✓ 已調整詞典優先順序。")

    def _apply_preset(self, preset: str, msg: str) -> None:
        self.dictionary.apply_dictionary_preset(preset)
        self._refresh_list()
        self.dictionary_updated.emit()
        self.status_lbl.setText(f"✓ {msg}")

    def _on_en_fallback_changed(self) -> None:
        mode = str(self.en_fallback_combo.currentData() or "jmdict")
        self.dictionary.set_en_fallback_source(mode)
        self._refresh_list()
        self.dictionary_updated.emit()
        self.status_lbl.setText(f"✓ 已切換日英備援來源：{self.en_fallback_combo.currentText()}")

    def _set_busy(self, busy: bool) -> None:
        for b in self.dl_btns:
            b.setEnabled(not busy)

    def _download_bundle(self) -> None:
        self._set_busy(True)
        self.progress.setValue(2)
        self.status_lbl.setText("開始一鍵安裝最佳 5 套開源詞典（總計約 64 MB，請稍候）...")
        worker = DictBundleDownloadWorker(self.dictionary)
        worker.signals.progress.connect(
            lambda msg, pct: (self.status_lbl.setText(msg), self.progress.setValue(pct))
        )
        worker.signals.finished.connect(self._on_bundle_done)
        worker.signals.failed.connect(self._on_fail)
        self._workers.append(worker)
        self.thread_pool.start(worker)

    def _on_bundle_done(self, results: list[dict]) -> None:
        self._set_busy(False)
        self.progress.setValue(100)
        total_entries = sum(int(r.get("entry_count", 0)) for r in results)
        self.status_lbl.setText(f"✓ 黃金搭配全部安裝完成（共 {len(results)} 套，{total_entries:,} 筆詞條/資料）！")
        self._refresh_list()
        self.dictionary_updated.emit()

    def _download(self, catalog_id: str) -> None:
        self._set_busy(True)
        self.progress.setValue(5)
        self.status_lbl.setText("正在下載並匯入詞典...")
        worker = DictDownloadWorker(self.dictionary, catalog_id)
        worker.signals.progress.connect(
            lambda msg, pct: (self.status_lbl.setText(msg), self.progress.setValue(pct))
        )
        worker.signals.finished.connect(self._on_done)
        worker.signals.failed.connect(self._on_fail)
        self._workers.append(worker)
        self.thread_pool.start(worker)

    def _import_local(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "匯入 Yomitan 詞典 (.zip)",
            str(Path.home()),
            "Yomitan Dictionary (*.zip)",
        )
        if not file_path:
            return
        self._set_busy(True)
        self.progress.setValue(5)
        self.status_lbl.setText(f"正在匯入 {Path(file_path).name}...")
        worker = DictImportWorker(self.dictionary, Path(file_path))
        worker.signals.progress.connect(
            lambda msg, pct: (self.status_lbl.setText(msg), self.progress.setValue(pct))
        )
        worker.signals.finished.connect(self._on_done)
        worker.signals.failed.connect(self._on_fail)
        self._workers.append(worker)
        self.thread_pool.start(worker)

    def _on_done(self, info: dict) -> None:
        self._set_busy(False)
        self.progress.setValue(100)
        self.status_lbl.setText(f"✓ 已匯入：{info['name']}（{info['entry_count']:,} 筆詞條）")
        self._refresh_list()
        self.dictionary_updated.emit()

    def _on_fail(self, err: str) -> None:
        self._set_busy(False)
        self.status_lbl.setText(f"失敗：{err}")
        QMessageBox.warning(self, "詞典處理失敗", err)

    def _delete_selected(self) -> None:
        item = self.dict_list.currentItem()
        if not item:
            return
        dict_id = item.data(Qt.ItemDataRole.UserRole)
        self.dictionary.delete_dictionary(int(dict_id))
        self._refresh_list()
        self.dictionary_updated.emit()
        self.status_lbl.setText("已移除選取的詞典。")


class DifficultyGuideDialog(QDialog):
    """原攻略（galgame.dev/topic/639）生肉 Galgame 難度分級與避雷作參考指南。"""

    def __init__(self, theme_name: str = "sakura", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("🗺️ 生肉 Galgame 難度分級與避雷指南")
        self.resize(680, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)

        p = get_palette(theme_name)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        guide = load_difficulty_guide()

        parts = [
            f'<h2 style="color:{p.accent_pink};">🌸 生肉 Galgame 由易到難進階路線</h2>',
            f'<p style="color:{p.text_secondary};">整理自 galgame.dev/topic/639：<b>興趣優先 → 理解意思 → 由易到難 → 登樓撤梯 → 每日開啟 FSRS 刷 Anki</b>。</p>',
        ]

        for tier in guide.get("tiers", []):
            parts.append(
                f'<div style="background:{p.bg_input}; border-left:4px solid {p.accent_pink}; padding:10px 14px; border-radius:10px; margin:9px 0;">'
                f'<h3 style="margin:0 0 4px 0; color:{p.text_primary};">{html.escape(tier["level"])} '
                f'<span style="font-size:12px; color:{p.accent_lilac};">（建議階段：{html.escape(tier["recommended_stage"])}）</span></h3>'
                f'<p style="margin:3px 0 6px 0; color:{p.text_secondary};">{html.escape(tier["summary"])}</p>'
            )
            for entry in tier.get("entries", []):
                titles = " / ".join(html.escape(t) for t in entry.get("titles", []))
                parts.append(
                    f'<p style="margin:5px 0;"><b>{html.escape(entry["creator"])}</b>：{titles}<br>'
                    f'<span style="color:{p.text_muted};">{html.escape(entry["notes"])}</span></p>'
                )
            parts.append("</div>")

        parts.append('<h2 style="color:#ef4444; margin-top:16px;">⚠ 新手避雷作（坐牢三幻神）</h2>')
        for trap in guide.get("avoid_for_beginners", []):
            titles = " / ".join(html.escape(t) for t in trap.get("titles", []))
            parts.append(
                f'<div style="background:{p.pill_warn_bg}; border-left:4px solid #ef4444; padding:10px 14px; border-radius:10px; margin:8px 0;">'
                f'<b>{html.escape(trap["creator"])}</b>（{titles}）<br>'
                f'<span style="color:{p.text_secondary};">{html.escape(trap["reason"])}</span></div>'
            )

        browser.setHtml("".join(parts))
        layout.addWidget(browser, 1)

        row = QHBoxLayout()
        row.addStretch()
        btn = QPushButton("關閉")
        btn.clicked.connect(self.accept)
        row.addWidget(btn)
        layout.addLayout(row)
