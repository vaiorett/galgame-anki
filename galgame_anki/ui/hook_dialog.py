"""遊戲 Hook 管理與進階特殊碼視窗（自動掃描 Wine 遊戲、一鍵掛載、切換線程與查看 Textractor 日誌）。"""

from __future__ import annotations

from PySide6.QtCore import Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from galgame_anki.adapters.hook_engine import (
    GameHookManager,
    HookTargetProcess,
    discover_wine_game_targets,
    ensure_textractor_installed,
    is_textractor_installed,
)
from galgame_anki.config import AppConfig
from galgame_anki.ui.workers import FunctionWorker


class HookManagerDialog(QDialog):
    """提供一鍵掛載執行中遊戲、注入自訂 H-Code、檢視 Hook 日誌與開啟 Textractor GUI 備援。"""

    target_attached = Signal(object)  # HookTargetProcess

    def __init__(
        self,
        config: AppConfig,
        hook_manager: GameHookManager,
        thread_pool: QThreadPool,
        console_history: list[str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("🪝 遊戲自動 Hook 管理與特殊碼設定")
        self.resize(680, 560)

        self.config = config
        self.hook_manager = hook_manager
        self.thread_pool = thread_pool
        self._targets: list[HookTargetProcess] = []
        self._workers: list[object] = []

        self._build_ui()
        self.log_view.setPlainText("\n".join(console_history[-80:]))
        self.hook_manager.console_logged.connect(self._append_log)
        self._refresh_engine_status()
        self._scan_running_games()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        # 1. Hook 引擎安裝狀態
        eng_box = QFrame()
        eng_box.setObjectName("CardPanel")
        eb = QVBoxLayout(eng_box)
        row1 = QHBoxLayout()
        self.engine_status = QLabel("")
        self.engine_status.setStyleSheet("font-weight: 700; color: #93c5fd;")
        row1.addWidget(self.engine_status, 1)

        self.install_btn = QPushButton("⬇ 一鍵下載 Textractor Hook 引擎")
        self.install_btn.setObjectName("PrimaryBtn")
        self.install_btn.clicked.connect(self._install_textractor)
        row1.addWidget(self.install_btn)
        eb.addLayout(row1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.hide()
        eb.addWidget(self.progress)
        layout.addWidget(eng_box)

        # 2. 掃描並掛載進行中的遊戲
        proc_box = QFrame()
        proc_box.setObjectName("CardPanel")
        pb = QVBoxLayout(proc_box)

        phead = QHBoxLayout()
        ptitle = QLabel("執行中的 Wine / Windows 遊戲進程（自動對齊 WINEPREFIX 與 32/64 位元）")
        ptitle.setStyleSheet("font-weight: 700; color: #6ee7b7;")
        phead.addWidget(ptitle, 1)

        scan_btn = QPushButton("🔄 重新掃描")
        scan_btn.clicked.connect(self._scan_running_games)
        phead.addWidget(scan_btn)
        pb.addLayout(phead)

        self.proc_list = QListWidget()
        self.proc_list.setMaximumHeight(130)
        self.proc_list.itemDoubleClicked.connect(lambda _item: self._attach_selected())
        pb.addWidget(self.proc_list)

        pact = QHBoxLayout()
        self.attach_btn = QPushButton("🪝 一鍵掛載選取遊戲")
        self.attach_btn.setObjectName("MineBtn")
        self.attach_btn.clicked.connect(self._attach_selected)
        pact.addWidget(self.attach_btn)

        detach_btn = QPushButton("⏹ 中斷掛載")
        detach_btn.clicked.connect(self._detach)
        pact.addWidget(detach_btn)

        gui_btn = QPushButton("🔧 進階：開啟 Textractor 視窗（搜碼備援）")
        gui_btn.clicked.connect(self._open_textractor_gui)
        pact.addWidget(gui_btn)
        pact.addStretch()
        pb.addLayout(pact)
        layout.addWidget(proc_box)

        # 3. 自訂特殊碼 (H-Code)
        hcode_row = QHBoxLayout()
        hcode_lbl = QLabel("特殊碼 (H-Code)：")
        hcode_row.addWidget(hcode_lbl)

        self.hcode_edit = QLineEdit()
        self.hcode_edit.setPlaceholderText("若特殊引擎未自動抓到文本，可貼上 Textractor H 碼（例如 HS-4@...）")
        hcode_row.addWidget(self.hcode_edit, 1)

        inject_btn = QPushButton("注入特殊碼")
        inject_btn.clicked.connect(self._inject_hcode)
        hcode_row.addWidget(inject_btn)
        layout.addLayout(hcode_row)

        # 4. 即時 Hook 引擎診斷日誌
        log_lbl = QLabel("Textractor 即時注入日誌（可確認是否已成功插入如 KiriKiriZ / TextOut 等 Hook）：")
        log_lbl.setObjectName("MutedLabel")
        layout.addWidget(log_lbl)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view, 1)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("關閉")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

    def _refresh_engine_status(self) -> None:
        if is_textractor_installed():
            self.engine_status.setText("✓ 內建 Textractor Hook 引擎 (x86 / x64) 已安裝就緒")
            self.install_btn.setText("已就緒")
            self.install_btn.setEnabled(False)
        else:
            self.engine_status.setText("⚠ 尚未下載 Textractor Hook 引擎（啟動遊戲或點右側按鈕即可自動安裝）")
            self.install_btn.setEnabled(True)

    def _install_textractor(self) -> None:
        self.install_btn.setEnabled(False)
        self.progress.show()
        self.progress.setValue(5)

        def _do() -> str:
            ensure_textractor_installed()
            return "OK"

        worker = FunctionWorker(_do)
        worker.signals.finished.connect(self._on_install_ok)
        worker.signals.failed.connect(self._on_install_fail)
        self._workers.append(worker)
        self.thread_pool.start(worker)

    def _on_install_ok(self, _res: object) -> None:
        self.progress.setValue(100)
        self._refresh_engine_status()
        self._append_log("[GalAnki] ✓ Textractor Hook 引擎下載安裝完成。")

    def _on_install_fail(self, err: str) -> None:
        self.install_btn.setEnabled(True)
        QMessageBox.warning(self, "下載失敗", err)

    def _scan_running_games(self) -> None:
        self.proc_list.clear()
        self.proc_list.addItem("正在掃描執行中的 Wine 遊戲進程...")
        worker = FunctionWorker(discover_wine_game_targets, self.config.wine_command)
        worker.signals.finished.connect(self._on_scan_done)
        worker.signals.failed.connect(lambda err: self._append_log(f"掃描失敗：{err}"))
        self._workers.append(worker)
        self.thread_pool.start(worker)

    def _on_scan_done(self, targets: list[HookTargetProcess]) -> None:
        self._targets = targets
        self.proc_list.clear()
        if not targets:
            self.proc_list.addItem("（未偵測到執行中的 Galgame，請先開啟遊戲後點「🔄 重新掃描」）")
            return
        for tgt in targets:
            prefix_hint = f"  |  WINEPREFIX={tgt.wine_prefix}" if tgt.wine_prefix else "  |  預設 Wine Prefix"
            item = QListWidgetItem(
                f"🎮 {tgt.exe_name}  (WinPID: {tgt.win_pid}, {tgt.arch}){prefix_hint}"
            )
            item.setData(Qt.ItemDataRole.UserRole, tgt)
            self.proc_list.addItem(item)
        self.proc_list.setCurrentRow(0)

    def _attach_selected(self) -> None:
        item = self.proc_list.currentItem()
        if not item:
            return
        tgt = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(tgt, HookTargetProcess):
            return
        try:
            if not is_textractor_installed():
                ensure_textractor_installed()
                self._refresh_engine_status()
            self.hook_manager.attach_target(tgt)
            self.target_attached.emit(tgt)
        except Exception as exc:
            QMessageBox.warning(self, "掛載失敗", str(exc))

    def _detach(self) -> None:
        self.hook_manager.detach()
        self._append_log("[GalAnki] 已中斷 Hook 掛載。")

    def _open_textractor_gui(self) -> None:
        try:
            if not is_textractor_installed():
                ensure_textractor_installed()
                self._refresh_engine_status()
            item = self.proc_list.currentItem()
            tgt = item.data(Qt.ItemDataRole.UserRole) if item else None
            self.hook_manager.launch_textractor_gui(tgt if isinstance(tgt, HookTargetProcess) else None)
            self._append_log("[GalAnki] 已開啟 Textractor GUI（請確認已啟用 Copy to Clipboard，複製的文本會自動同步進 GalAnki）。")
        except Exception as exc:
            QMessageBox.warning(self, "開啟失敗", str(exc))

    def _inject_hcode(self) -> None:
        code = self.hcode_edit.text().strip()
        if not code:
            return
        if not self.hook_manager.inject_hook_code(code):
            QMessageBox.warning(self, "尚未掛載", "請先掛載目標遊戲進程，再注入特殊碼。")
        else:
            self.hcode_edit.clear()

    def _append_log(self, msg: str) -> None:
        self.log_view.append(msg)
