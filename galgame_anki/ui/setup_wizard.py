"""一鍵初始化精靈（AnkiConnect 檢測、三階段模板建置、FSRS 檢查與免費詞典一鍵下載）。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThreadPool, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from galgame_anki.adapters.base import PlatformAdapter
from galgame_anki.config import AppConfig
from galgame_anki.core.anki_client import (
    ANKICONNECT_ADDON_CODE,
    AnkiConnectClient,
    AnkiInitReport,
)
from galgame_anki.core.dictionary import FREE_DICTIONARY_CATALOG, DictionaryEngine
from galgame_anki.ui.workers import (
    DictBundleDownloadWorker,
    DictDownloadWorker,
    DictImportWorker,
    FunctionWorker,
)


class SetupWizardDialog(QDialog):
    """解決原攻略「折騰門檻太高」痛點的一鍵初始化精靈。"""

    anki_initialized = Signal(object)   # AnkiInitReport
    dictionary_updated = Signal()

    def __init__(
        self,
        config: AppConfig,
        adapter: PlatformAdapter,
        dictionary: DictionaryEngine,
        anki: AnkiConnectClient,
        thread_pool: QThreadPool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("🚀 GalAnki 一鍵環境初始化精靈")
        self.resize(740, 660)

        self.config = config
        self.adapter = adapter
        self.dictionary = dictionary
        self.anki = anki
        self.thread_pool = thread_pool
        self._active_workers: list[object] = []

        self._build_ui()
        self._refresh_diagnostics()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        intro = QLabel(
            "歡迎使用 GalAnki！依照原攻略《關於我一年不到速通 N1 這件事》的 Refold 路線，"
            "以下 3 個步驟即可完成全套一鍵製卡環境："
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("font-size: 14px; font-weight: 600;")
        layout.addWidget(intro)

        # --- Step 1: 系統環境診斷 ---
        step1 = QFrame()
        step1.setObjectName("CardPanel")
        s1_layout = QVBoxLayout(step1)
        s1_title = QLabel("1️⃣ 系統環境自動診斷（Linux Wayland / Wine / PipeWire & Anki）")
        s1_title.setStyleSheet("font-weight: 700; font-size: 14px; background: transparent;")
        s1_layout.addWidget(s1_title)

        self.diag_label = QLabel("")
        self.diag_label.setWordWrap(True)
        s1_layout.addWidget(self.diag_label)
        layout.addWidget(step1)

        # --- Step 2: 一鍵初始化 Anki 環境 ---
        step2 = QFrame()
        step2.setObjectName("CardPanel")
        s2_layout = QVBoxLayout(step2)
        s2_top = QHBoxLayout()
        s2_title = QLabel("2️⃣ 一鍵初始化 Anki（建立牌組 + 原生主題三階段模板 + 檢查 FSRS）")
        s2_title.setStyleSheet("font-weight: 700; font-size: 14px; background: transparent;")
        s2_top.addWidget(s2_title)
        s2_top.addStretch()

        copy_code_btn = QPushButton(f"📋 複製 AnkiConnect 代碼 ({ANKICONNECT_ADDON_CODE})")
        copy_code_btn.clicked.connect(self._copy_addon_code)
        s2_top.addWidget(copy_code_btn)
        s2_layout.addLayout(s2_top)

        self.init_anki_btn = QPushButton("⚡ 一鍵檢查並初始化 Anki 環境")
        self.init_anki_btn.setObjectName("MineBtn")
        self.init_anki_btn.clicked.connect(self._run_anki_init)
        s2_layout.addWidget(self.init_anki_btn)

        self.anki_log = QTextEdit()
        self.anki_log.setReadOnly(True)
        self.anki_log.setMaximumHeight(110)
        self.anki_log.setPlaceholderText("請先開啟本機 Anki，接著點擊上方按鈕一鍵完成設定...")
        s2_layout.addWidget(self.anki_log)
        layout.addWidget(step2)

        # --- Step 3: 免費開源詞典一鍵下載與匯入 ---
        step3 = QFrame()
        step3.setObjectName("CardPanel")
        s3_layout = QVBoxLayout(step3)
        s3_top = QHBoxLayout()
        s3_title = QLabel("3️⃣ 免費開源詞典（推薦一鍵安裝 5 合 1 黃金搭配）")
        s3_title.setStyleSheet("font-weight: 700; font-size: 14px; background: transparent;")
        s3_top.addWidget(s3_title)
        s3_top.addStretch()

        import_local_btn = QPushButton("📂 匯入本機 Yomitan .zip")
        import_local_btn.clicked.connect(self._import_local_zip)
        s3_top.addWidget(import_local_btn)
        s3_layout.addLayout(s3_top)

        self.bundle_btn = QPushButton("🌟 一鍵安裝最佳黃金詞典組合（日中 + 日日 + Jitendex + Kanjium音調 + JPDB詞頻）")
        self.bundle_btn.setObjectName("MineBtn")
        self.bundle_btn.clicked.connect(self._download_bundle)
        s3_layout.addWidget(self.bundle_btn)

        self.dict_buttons: list[QPushButton] = [self.bundle_btn]
        self._wizard_dict_rows: dict[str, tuple[QLabel, QPushButton, dict]] = {}
        for spec in FREE_DICTIONARY_CATALOG[:5]:
            row = QHBoxLayout()
            info = QLabel("")
            info.setWordWrap(True)
            row.addWidget(info, 1)

            btn = QPushButton("⬇ 安裝")
            btn.clicked.connect(lambda _c=False, cid=spec["id"]: self._download_dict(cid))
            self.dict_buttons.append(btn)
            self._wizard_dict_rows[spec["id"]] = (info, btn, spec)
            row.addWidget(btn)
            s3_layout.addLayout(row)

        self.dict_progress_label = QLabel("目前狀態：待命（可在「詞典管理」自由勾選開啟或停用個別詞典）")
        self.dict_progress_label.setObjectName("MutedLabel")
        s3_layout.addWidget(self.dict_progress_label)
        self._refresh_dict_status()

        self.dict_progress = QProgressBar()
        self.dict_progress.setRange(0, 100)
        self.dict_progress.setValue(0)
        s3_layout.addWidget(self.dict_progress)
        layout.addWidget(step3)

        # --- Step 4: Hook 串接速查 ---
        hook_hint = QLabel(
            f"💡 <b>台詞與字幕擷取（Galgame + mpv 影片）：</b>"
            "① 點「<b>🎮 遊戲/影片</b>」可直接啟動生肉自動 Hook，或一鍵連線 mpv 本地動畫字幕；"
            f"② 同時支援 <b>📷 框選 OCR</b>（Ctrl+Shift+O）及剪貼簿 / WebSocket (ws://{self.config.ws_host}:{self.config.ws_port})！"
        )
        hook_hint.setWordWrap(True)
        hook_hint.setObjectName("MutedLabel")
        layout.addWidget(hook_hint)

        bottom = QHBoxLayout()
        copy_ws_btn = QPushButton(f"📋 複製 WebSocket 位址 (ws://{self.config.ws_host}:{self.config.ws_port})")
        copy_ws_btn.clicked.connect(self._copy_ws_url)
        bottom.addWidget(copy_ws_btn)
        bottom.addStretch()

        done_btn = QPushButton("完成並開始學習")
        done_btn.setObjectName("PrimaryBtn")
        done_btn.clicked.connect(self.accept)
        bottom.addWidget(done_btn)
        layout.addLayout(bottom)

    def _refresh_diagnostics(self) -> None:
        diag = self.adapter.diagnose_environment()
        tool_badges = []
        for k, ok in diag.tools.items():
            icon = "✅" if ok else "⬜"
            tool_badges.append(f"{icon} {k}")
        lines = [
            f"平台：{diag.platform_name}（工作階段：{diag.session_type}）",
            "工具狀態：" + "　".join(tool_badges),
            *diag.notes,
        ]
        self.diag_label.setText("\n".join(lines))

    def _copy_addon_code(self) -> None:
        cb = QGuiApplication.clipboard()
        if cb:
            cb.setText(ANKICONNECT_ADDON_CODE)
        self.anki_log.append(
            f"已複製 AnkiConnect 代碼 {ANKICONNECT_ADDON_CODE}。請在 Anki 點擊「工具 → 附加元件 → 取得附加元件」貼上安裝並重啟 Anki。"
        )

    def _copy_ws_url(self) -> None:
        url = f"ws://{self.config.ws_host}:{self.config.ws_port}"
        cb = QGuiApplication.clipboard()
        if cb:
            cb.setText(url)
        self.dict_progress_label.setText(f"已複製 WebSocket 位址：{url}")

    def _run_anki_init(self) -> None:
        self.init_anki_btn.setEnabled(False)
        self.anki_log.setPlainText("正在連線 AnkiConnect 並初始化牌組與三階段模板...")

        worker = FunctionWorker(
            self.anki.one_click_initialize,
            self.config.deck_name,
            self.config.note_type,
        )
        worker.signals.finished.connect(self._on_anki_init_done)
        worker.signals.failed.connect(self._on_anki_init_failed)
        self._active_workers.append(worker)
        self.thread_pool.start(worker)

    def _on_anki_init_done(self, report: AnkiInitReport) -> None:
        self.init_anki_btn.setEnabled(True)
        self.anki_log.setPlainText("\n".join(report.messages))
        self.anki_initialized.emit(report)

    def _on_anki_init_failed(self, err: str) -> None:
        self.init_anki_btn.setEnabled(True)
        self.anki_log.setPlainText(f"初始化失敗：{err}")

    def _set_dict_buttons_enabled(self, enabled: bool) -> None:
        for b in self.dict_buttons:
            b.setEnabled(enabled)

    def _download_bundle(self) -> None:
        self._set_dict_buttons_enabled(False)
        self.dict_progress.setValue(2)
        self.dict_progress_label.setText("開始一鍵安裝最佳 5 套開源詞典（日中 + 日日 + Jitendex + 音調 + 詞頻）...")

        worker = DictBundleDownloadWorker(self.dictionary)
        worker.signals.progress.connect(self._on_dict_progress)
        worker.signals.finished.connect(self._on_bundle_done)
        worker.signals.failed.connect(self._on_dict_failed)
        self._active_workers.append(worker)
        self.thread_pool.start(worker)

    def _refresh_dict_status(self) -> None:
        all_installed = True
        for cid, (info, btn, spec) in self._wizard_dict_rows.items():
            inst = self.dictionary.find_installed_for_catalog(cid)
            if inst is not None:
                info.setText(
                    f"<b>{spec['name']}</b> <span style='color:#059669; font-weight:700;'>✅ 已安裝（{inst['entry_count']:,} 筆）</span>"
                    f"<br><span style='opacity:0.75;font-size:12px;'>{spec['description']}</span>"
                )
                btn.setText("↻ 重裝")
            else:
                all_installed = False
                info.setText(
                    f"<b>{spec['name']}</b> <span style='opacity:0.65;'>⬜ 未安裝</span>"
                    f"<br><span style='opacity:0.75;font-size:12px;'>{spec['description']}</span>"
                )
                btn.setText("⬇ 安裝")
        if all_installed:
            self.bundle_btn.setText("✅ 最佳黃金 5 合 1 詞典已全部安裝完成")
            self.bundle_btn.setObjectName("")
        else:
            self.bundle_btn.setText("🌟 一鍵安裝最佳黃金詞典組合（日中 + 日日 + Jitendex + Kanjium音調 + JPDB詞頻）")
            self.bundle_btn.setObjectName("MineBtn")

    def _on_bundle_done(self, results: list[dict]) -> None:
        self._set_dict_buttons_enabled(True)
        self.dict_progress.setValue(100)
        total = sum(int(r.get("entry_count", 0)) for r in results)
        self.dict_progress_label.setText(f"✓ 最佳黃金詞典組合全部安裝完成（共 {len(results)} 套，{total:,} 筆）！")
        self._refresh_dict_status()
        self.dictionary_updated.emit()

    def _download_dict(self, catalog_id: str) -> None:
        self._set_dict_buttons_enabled(False)
        self.dict_progress.setValue(2)
        self.dict_progress_label.setText("開始下載並匯入詞典...")

        worker = DictDownloadWorker(self.dictionary, catalog_id)
        worker.signals.progress.connect(self._on_dict_progress)
        worker.signals.finished.connect(self._on_dict_done)
        worker.signals.failed.connect(self._on_dict_failed)
        self._active_workers.append(worker)
        self.thread_pool.start(worker)

    def _import_local_zip(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "選擇 Yomitan 詞典壓縮檔 (.zip)",
            str(Path.home()),
            "Yomitan Dictionary (*.zip)",
        )
        if not file_path:
            return

        self._set_dict_buttons_enabled(False)
        self.dict_progress.setValue(5)
        self.dict_progress_label.setText(f"正在匯入 {Path(file_path).name}...")

        worker = DictImportWorker(self.dictionary, Path(file_path))
        worker.signals.progress.connect(self._on_dict_progress)
        worker.signals.finished.connect(self._on_dict_done)
        worker.signals.failed.connect(self._on_dict_failed)
        self._active_workers.append(worker)
        self.thread_pool.start(worker)

    def _on_dict_progress(self, msg: str, pct: int) -> None:
        self.dict_progress_label.setText(msg)
        self.dict_progress.setValue(pct)

    def _on_dict_done(self, info: dict) -> None:
        self._set_dict_buttons_enabled(True)
        self.dict_progress.setValue(100)
        self.dict_progress_label.setText(
            f"✓ 匯入成功：{info['name']}（{info['kind']}，共 {info['entry_count']:,} 筆詞條）"
        )
        self._refresh_dict_status()
        self.dictionary_updated.emit()

    def _on_dict_failed(self, err: str) -> None:
        self._set_dict_buttons_enabled(True)
        self.dict_progress_label.setText(f"詞典下載/匯入失敗：{err}")
        QMessageBox.warning(self, "詞典處理失敗", err)
