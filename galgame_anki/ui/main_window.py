"""GalAnki 輕量二次元伴讀主視窗：Galgame Hook + mpv 影片字幕 + 原聲錄製 + 系統化 N5→N1 學習藍圖 + 一鍵 Anki 製卡。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, Qt, QThreadPool, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from galgame_anki.adapters import create_platform_adapter
from galgame_anki.adapters.audio_recorder import (
    AudioRingRecorder,
    extract_video_audio_mp3,
    extract_video_frame_webp,
    play_mp3_bytes_async,
)
from galgame_anki.adapters.hook_engine import (
    GameHookManager,
    HookTargetProcess,
    HookThreadInfo,
    detect_pe_arch,
    discover_wine_game_targets,
    ensure_textractor_installed,
    list_wine_tasklist,
)
from galgame_anki.adapters.mpv_bridge import MpvSubtitleBridge, MpvSubtitleEvent
from galgame_anki.adapters.ocr_engine import capture_region_and_ocr
from galgame_anki.adapters.text_sources import TextIngestionManager
from galgame_anki.config import AppConfig
from galgame_anki.core.anki_client import AnkiCardPayload, AnkiConnectClient
from galgame_anki.core.context_meaning import format_contextual_meaning
from galgame_anki.core.dictionary import DictionaryEngine
from galgame_anki.core.grammar_engine import (
    OfflineGrammarEngine,
    build_web_ai_prompt,
    translate_sentence_free,
)
from galgame_anki.core.learning_progress import LearningProgressStore
from galgame_anki.core.sentence_store import SentenceRecord, SentenceStore
from galgame_anki.core.tokenizer import (
    JapaneseTokenizer,
    Token,
    highlight_target_in_sentence,
)
from galgame_anki.ui.blueprint_dialog import LearningBlueprintDialog
from galgame_anki.ui.card_forge_dialog import CardForgeDialog, ForgedCardData
from galgame_anki.ui.extra_dialogs import DifficultyGuideDialog, DictionaryManagerDialog
from galgame_anki.ui.hook_dialog import HookManagerDialog
from galgame_anki.ui.lookup_popup import LookupDetailPanel
from galgame_anki.ui.media_bridge_dialog import MediaBridgeDialog
from galgame_anki.ui.overlay_window import OverlayWindow
from galgame_anki.ui.sentence_widget import InteractiveSentenceWidget
from galgame_anki.ui.settings_dialog import SettingsDialog
from galgame_anki.ui.setup_wizard import SetupWizardDialog
from galgame_anki.ui.theme import build_stylesheet
from galgame_anki.ui.workers import FunctionWorker

# 來自 galgame.dev/topic/639 的經典範例句（隨時可從選單試玩）
DEMO_SENTENCES: list[tuple[str, str]] = [
    ("攻略 Lv.2・募る", "外の世界を知りたいという想いは、募る一方だ"),
    ("攻略 Lv.2・やがて", "答えを知るために歩いた場所が、やがて道となるのだ。"),
    ("攻略 Lv.2・握り飯", "米を炊いて握り飯を作る"),
    ("攻略 Lv.3・振舞う", "明日以降、学園で顔を合わせても、まるで今日のことがなかったかのように振舞います"),
    ("攻略 Lv.3・踏ん張る", "たいした力ではなかったが、あまりにも突然のことに踏ん張れなかった。"),
    ("攻略 Lv.3・かぶれる", "『誠、あなたは少しこちらの世界にかぶれすぎた。私自身もね』"),
]


class MainWindow(QMainWindow):
    """輕量化二次元伴讀控制台（Galgame + mpv 影片字幕・系統語法藍圖・原聲 Anki 製卡）。"""

    def __init__(self, config: AppConfig | None = None, load_demo: bool = False) -> None:
        super().__init__()
        self.setWindowTitle("GalAnki 🌸 邊玩邊看・系統化日語伴讀")
        self.resize(860, 580)
        self.setMinimumSize(720, 480)

        self.config = config or AppConfig.load()
        self.adapter = create_platform_adapter()
        self.dictionary = DictionaryEngine()
        self.tokenizer = JapaneseTokenizer(self.dictionary)
        self.grammar_engine = OfflineGrammarEngine()
        self.progress_store = LearningProgressStore()
        self.anki = AnkiConnectClient(self.config.anki_url)
        self.store = SentenceStore(max_history=50)
        self.ingestion = TextIngestionManager(self)
        self.hook_manager = GameHookManager(self)
        self.mpv_bridge = MpvSubtitleBridge(parent=self)
        self.audio_recorder = AudioRingRecorder(max_seconds=240, parent=self)
        self._hook_console_log: list[str] = []
        self.thread_pool = QThreadPool.globalInstance()
        self._active_workers: list[object] = []

        self._current_record: SentenceRecord | None = None
        self._selected_token: Token | None = None
        self._anki_connected: bool = False

        self.overlay = OverlayWindow(
            theme_name=self.config.ui_theme,
            grammar_engine=self.grammar_engine,
        )
        self.overlay.restore_saved_state(
            x=self.config.overlay_x,
            y=self.config.overlay_y,
            width=self.config.overlay_width,
            height=self.config.overlay_height,
            locked=self.config.overlay_locked,
            hover_lookup=self.config.overlay_hover_lookup,
            guide_dismissed=self.config.overlay_guide_dismissed,
            replace_mode=self.config.overlay_replace_mode,
            backdrop=self.config.overlay_backdrop,
            font_size=self.config.overlay_font_size,
            always_on_top=self.config.overlay_always_on_top,
        )
        self.overlay.mine_requested.connect(self._mine_record_token)
        self.overlay.copy_prompt_requested.connect(self._copy_web_ai_prompt_for_record)
        self.overlay.translate_requested.connect(self._translate_record_for_overlay)
        self.overlay.prev_requested.connect(self._step_history_prev)
        self.overlay.next_requested.connect(self._step_history_next)
        self.overlay.ocr_requested.connect(self._trigger_region_ocr)
        self.overlay.audio_preview_requested.connect(self._preview_current_audio)
        self.overlay.visibility_changed.connect(self._on_overlay_visibility_changed)
        self.overlay.replace_mode_changed.connect(self._on_overlay_replace_mode_changed)
        self.overlay.state_saved.connect(self._on_overlay_state_saved)

        self._build_ui()
        self._setup_shortcuts()
        self._connect_signals()
        self._apply_theme(self.config.ui_theme, save=False)
        self._apply_stage_ui()

        if load_demo or not self.store.latest():
            self._ingest_sentence(DEMO_SENTENCES[0][1], source="demo")

        # 啟動後自動連接 Anki（同步已知詞並自動更新為原生默認主題模板）與啟動語音環形緩衝
        QTimer.singleShot(250, self._background_sync_anki)
        if self.config.capture_audio_on_mine:
            QTimer.singleShot(450, self._start_audio_capture)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # === 極簡二次元頂部膠囊工具列 ===
        top_bar = QFrame()
        top_bar.setObjectName("TopBar")
        tl = QHBoxLayout(top_bar)
        tl.setContentsMargins(12, 8, 12, 8)
        tl.setSpacing(6)

        # 1. 遊戲啟動 & mpv 影片字幕 統一來源選單
        self.game_btn = QPushButton("🎮 遊戲/影片")
        self.game_btn.setObjectName("PrimaryBtn")
        self.game_menu = QMenu(self)
        self.game_btn.setMenu(self.game_menu)
        self._rebuild_game_menu()
        tl.addWidget(self.game_btn)

        # 2. 遮蓋替換覆蓋層、系統學習藍圖與 OCR
        self.overlay_btn = QPushButton("🪟 覆蓋層")
        self.overlay_btn.setCheckable(True)
        self.overlay_btn.setToolTip("置頂遮蓋替換覆蓋層：直接蓋住遊戲原對話文字或取代 mpv 字幕，絕不看 2 份字幕")
        self.overlay_btn.clicked.connect(self._toggle_overlay)
        tl.addWidget(self.overlay_btn)

        self.blueprint_btn = QPushButton("🗺️ 藍圖")
        self.blueprint_btn.setObjectName("AccentBtn")
        self.blueprint_btn.setToolTip("開啟 N5→N1 系統化學習藍圖、實戰語法解鎖進度與「30秒看懂詞典與動詞活用」速查表")
        self.blueprint_btn.clicked.connect(self.open_learning_blueprint)
        tl.addWidget(self.blueprint_btn)

        self.ocr_btn = QPushButton("📷 OCR")
        self.ocr_btn.setToolTip("框選任意畫面對話框立即辨識日文 (Ctrl+Shift+O)")
        self.ocr_btn.clicked.connect(self._trigger_region_ocr)
        tl.addWidget(self.ocr_btn)

        tl.addSpacing(3)

        # 3. 迷你三階段切換（登樓撤梯）
        self.stage_group = QButtonGroup(self)
        self.stage_buttons: dict[str, QPushButton] = {}
        for mode_id, label, tip in [
            ("lv1", "Lv.1", "五十音破冰：精準假名提示 + 雙語詞典"),
            ("lv2", "Lv.2", "精細沉浸：1T 高亮 + 雙語詞典"),
            ("lv3", "Lv.3", "登樓撤梯：日日單語詞典優先"),
        ]:
            btn = QPushButton(label)
            btn.setObjectName("StageBtn")
            btn.setToolTip(tip)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _c=False, m=mode_id: self._set_stage_mode(m))
            self.stage_group.addButton(btn)
            self.stage_buttons[mode_id] = btn
            tl.addWidget(btn)

        tl.addStretch()

        # 4. 語音錄製開關膠囊
        self.audio_toggle_btn = QPushButton("🎙 錄音")
        self.audio_toggle_btn.setCheckable(True)
        self.audio_toggle_btn.setChecked(self.config.capture_audio_on_mine)
        self.audio_toggle_btn.setToolTip("背景純淨錄製遊戲／影片原聲（parec s16le），一鍵製卡時自動切出聲優原聲 .mp3 寫入 Anki")
        self.audio_toggle_btn.clicked.connect(self._toggle_audio_recording)
        tl.addWidget(self.audio_toggle_btn)

        self.budget_pill = QLabel(f"0/{self.config.daily_card_limit}")
        self.budget_pill.setObjectName("PillInfo")
        self.budget_pill.setToolTip("今日製卡數 / 每日建議上限")
        tl.addWidget(self.budget_pill)

        self.anki_pill = QLabel("Anki…")
        self.anki_pill.setObjectName("PillWarn")
        tl.addWidget(self.anki_pill)

        # 5. 一鍵切換櫻花/星夜二次元主題
        self.theme_btn = QPushButton("🌙" if self.config.ui_theme == "sakura" else "🌸")
        self.theme_btn.setObjectName("GhostBtn")
        self.theme_btn.setFixedWidth(32)
        self.theme_btn.setToolTip("一鍵切換 🌸 櫻霧輕透 / 🌙 星夜琉璃 主題")
        self.theme_btn.clicked.connect(self._toggle_theme)
        tl.addWidget(self.theme_btn)

        # 6. 輕量「更多」選單
        self.more_btn = QPushButton("☰")
        self.more_btn.setObjectName("GhostBtn")
        self.more_btn.setFixedWidth(34)
        self.more_menu = QMenu(self)
        self._build_more_menu()
        self.more_btn.setMenu(self.more_menu)
        tl.addWidget(self.more_btn)

        root.addWidget(top_bar)

        # === 主內容區（左：輕量對話框 + 查詞與離線邏輯語法卡；右：可收合 50 句歷史抽屜） ===
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(12, 10, 12, 6)
        body_layout.setSpacing(8)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)

        left_col = QWidget()
        left_layout = QVBoxLayout(left_col)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        # --- Hero Galgame / 影片字幕台詞卡片 ---
        sent_frame = QFrame()
        sent_frame.setObjectName("SentencePanel")
        sf = QVBoxLayout(sent_frame)
        sf.setContentsMargins(14, 10, 14, 10)
        sf.setSpacing(6)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(6)

        self.game_badge = QLabel(self.config.current_game_name or "🌸 等待遊戲 / 影片字幕")
        self.game_badge.setObjectName("PillInfo")
        meta_row.addWidget(self.game_badge)

        self.thread_combo = QComboBox()
        self.thread_combo.setMinimumWidth(140)
        self.thread_combo.setMaximumWidth(230)
        self.thread_combo.addItem("🧵 自動線程", "auto")
        self.thread_combo.setToolTip("若遊戲有多條 Hook 線程，可在此一鍵切換主對話線程")
        self.thread_combo.currentIndexChanged.connect(self._on_hook_thread_changed)
        meta_row.addWidget(self.thread_combo)

        meta_row.addStretch()

        self.sentence_badge = QLabel("")
        self.sentence_badge.setObjectName("PillOk")
        meta_row.addWidget(self.sentence_badge)

        self.translate_btn = QPushButton("🌐 核對句意")
        self.translate_btn.setObjectName("GhostBtn")
        self.translate_btn.setToolTip("卡住時點一下核對整句自然中文意思（按需呼叫免費翻譯，平時隱藏不劇透）")
        self.translate_btn.clicked.connect(self._check_current_sentence_translation)
        meta_row.addWidget(self.translate_btn)

        self.history_toggle_btn = QPushButton("🕒 歷史")
        self.history_toggle_btn.setObjectName("GhostBtn")
        self.history_toggle_btn.setCheckable(True)
        self.history_toggle_btn.setToolTip("展開/收合近期 50 句時光機")
        self.history_toggle_btn.clicked.connect(self._toggle_history_drawer)
        meta_row.addWidget(self.history_toggle_btn)

        sf.addLayout(meta_row)

        self.sentence_widget = InteractiveSentenceWidget(font_size=22, theme_name=self.config.ui_theme)
        self.sentence_widget.token_clicked.connect(self._on_main_token_clicked)
        sf.addWidget(self.sentence_widget)

        # 按需顯示的「本句自然語境理解」（平時隱藏不破壞啃生肉沉浸感）
        self.translation_label = QLabel("")
        self.translation_label.setObjectName("MutedLabel")
        self.translation_label.setWordWrap(True)
        self.translation_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.translation_label.hide()
        sf.addWidget(self.translation_label)

        # 精簡手動輸入列
        self.manual_input = QLineEdit()
        self.manual_input.setPlaceholderText("手動貼上日語句測試分詞、語法拆解與製卡（Enter 送出）…")
        self.manual_input.returnPressed.connect(self._submit_manual_input)
        sf.addWidget(self.manual_input)

        left_layout.addWidget(sent_frame)

        # --- 查詞、白話詞性、離線邏輯語法與一鍵收錄卡片 ---
        self.lookup_panel = LookupDetailPanel(theme_name=self.config.ui_theme)
        self.lookup_panel.mine_requested.connect(self._on_panel_mine)
        self.lookup_panel.copy_prompt_requested.connect(self._on_panel_copy_prompt)
        self.lookup_panel.audio_preview_requested.connect(self._preview_current_audio)
        left_layout.addWidget(self.lookup_panel, 1)

        self.splitter.addWidget(left_col)

        # --- 右側可收合「近期 50 句時光機」抽屜 ---
        self.history_drawer = QFrame()
        self.history_drawer.setObjectName("CardPanel")
        hd = QVBoxLayout(self.history_drawer)
        hd.setContentsMargins(10, 10, 10, 10)
        hd.setSpacing(6)

        hd_title = QLabel("🕒 近期台詞（最多 50 句）")
        hd_title.setStyleSheet("font-weight: 700; background: transparent;")
        hd.addWidget(hd_title)

        hd_hint = QLabel("點任一句即可回溯查詞、試聽原聲並補製 Anki 卡片。")
        hd_hint.setObjectName("MutedLabel")
        hd_hint.setWordWrap(True)
        hd.addWidget(hd_hint)

        self.history_list = QListWidget()
        self.history_list.currentRowChanged.connect(self._on_history_selected)
        hd.addWidget(self.history_list, 1)

        self.history_drawer.hide()
        self.splitter.addWidget(self.history_drawer)
        self.splitter.setSizes([660, 220])

        body_layout.addWidget(self.splitter, 1)
        root.addWidget(body, 1)

        status_bar = QStatusBar()
        self.setStatusBar(status_bar)
        self.statusBar().showMessage(
            "🌸 就緒：從左上角「🎮 遊戲/影片」啟動生肉或連接 mpv 影片字幕，或點「🗺️ 藍圖」看 30 秒上手指南。"
        )

    def _rebuild_game_menu(self) -> None:
        self.game_menu.clear()

        act_launch = QAction("🎮 啟動日區生肉並自動 Hook...", self)
        act_launch.triggered.connect(self._launch_game_dialog)
        self.game_menu.addAction(act_launch)

        act_attach = QAction("🪝 偵測並一鍵掛載進行中遊戲", self)
        act_attach.triggered.connect(self._detect_and_attach_running_game)
        self.game_menu.addAction(act_attach)

        act_mgr = QAction("🔧 Hook 線程・特殊碼與日誌管理", self)
        act_mgr.triggered.connect(self.open_hook_manager)
        self.game_menu.addAction(act_mgr)

        self.game_menu.addSeparator()

        act_media = QAction("🎬 連接 mpv 本地動畫／影片字幕...", self)
        act_media.triggered.connect(self.open_media_bridge)
        self.game_menu.addAction(act_media)

        if self.config.recent_games:
            self.game_menu.addSeparator()
            hdr = QAction("最近遊玩（點擊直接啟動並自動 Hook）", self)
            hdr.setEnabled(False)
            self.game_menu.addAction(hdr)
            for item in self.config.recent_games[:6]:
                name = item.get("name", "")
                path = item.get("path", "")
                if name and path:
                    act = QAction(f"▶ {name}", self)
                    act.triggered.connect(
                        lambda _c=False, p=path, n=name: self._launch_specific_game(Path(p), n)
                    )
                    self.game_menu.addAction(act)

    def _build_more_menu(self) -> None:
        self.more_menu.clear()

        act_bp = QAction("🗺️ N5→N1 學習藍圖 & 30 秒詞典／活用指南", self)
        act_bp.triggered.connect(self.open_learning_blueprint)
        self.more_menu.addAction(act_bp)

        act_media = QAction("🎬 mpv 本地動畫／影片字幕連線設定", self)
        act_media.triggered.connect(self.open_media_bridge)
        self.more_menu.addAction(act_media)

        self.more_menu.addSeparator()

        act_wiz = QAction("🚀 一鍵初始化精靈（Anki / FSRS / 詞典）", self)
        act_wiz.triggered.connect(self.open_setup_wizard)
        self.more_menu.addAction(act_wiz)

        act_dict = QAction("📚 詞典管理（一鍵黃金 5 合 1 / Yomitan .zip）", self)
        act_dict.triggered.connect(self.open_dictionary_manager)
        self.more_menu.addAction(act_dict)

        act_guide = QAction("🎯 生肉 Galgame 難度分級與避雷指南", self)
        act_guide.triggered.connect(self.open_difficulty_guide)
        self.more_menu.addAction(act_guide)

        demo_sub = self.more_menu.addMenu("✨ 試玩原攻略例句")
        for label, sent in DEMO_SENTENCES:
            a = QAction(label, self)
            a.triggered.connect(
                lambda _c=False, s=sent: self.ingestion.submit_manual_text(s, source="demo")
            )
            demo_sub.addAction(a)

        self.more_menu.addSeparator()

        self.act_pause = QAction("⏸ 暫停自動監聽（無阻沉浸）", self)
        self.act_pause.setCheckable(True)
        self.act_pause.triggered.connect(self._toggle_listening)
        self.more_menu.addAction(self.act_pause)

        act_cfg = QAction("⚙ 進階設定（Anki・語音・字幕・Wine）", self)
        act_cfg.triggered.connect(self.open_settings)
        self.more_menu.addAction(act_cfg)

    def _setup_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self._shortcut_mine)
        QShortcut(QKeySequence("Ctrl+M"), self, activated=self._shortcut_mine)
        QShortcut(QKeySequence("Ctrl+Shift+O"), self, activated=self._trigger_region_ocr)
        QShortcut(QKeySequence("Ctrl+P"), self, activated=self._preview_current_audio)
        QShortcut(QKeySequence("Ctrl+B"), self, activated=self.open_learning_blueprint)
        QShortcut(QKeySequence("Ctrl+1"), self, activated=lambda: self._set_stage_mode("lv1"))
        QShortcut(QKeySequence("Ctrl+2"), self, activated=lambda: self._set_stage_mode("lv2"))
        QShortcut(QKeySequence("Ctrl+3"), self, activated=lambda: self._set_stage_mode("lv3"))

    def _shortcut_mine(self) -> None:
        if self._current_record and self._selected_token:
            self._mine_record_token(self._current_record, self._selected_token)

    def _connect_signals(self) -> None:
        self.ingestion.dedup_repeated = self.config.dedup_repeated_chars
        self.ingestion.ignore_non_japanese = self.config.ignore_non_japanese
        self.ingestion.sentence_meta_received.connect(self._on_ingestion_sentence_meta)
        self.ingestion.status_changed.connect(lambda msg: self.statusBar().showMessage(msg, 4500))

        self.hook_manager.dedup_repeated = self.config.dedup_repeated_chars
        self.hook_manager.dialogue_received.connect(self._on_hook_dialogue)
        self.hook_manager.dialogue_extended.connect(self._on_hook_dialogue_extended)
        self.hook_manager.threads_updated.connect(self._on_hook_threads_updated)
        self.hook_manager.console_logged.connect(self._on_hook_console_log)
        self.hook_manager.hook_state_changed.connect(self._on_hook_state_changed)

        self.mpv_bridge.subtitle_received.connect(self._on_mpv_subtitle)
        self.mpv_bridge.connection_changed.connect(self._on_mpv_state_changed)
        self.mpv_bridge.start()

        self.audio_recorder.state_changed.connect(self._on_audio_state_changed)

        if self.config.ws_enabled:
            self.ingestion.start_websocket(self.config.ws_host, self.config.ws_port)
        if self.config.clipboard_watch:
            self.ingestion.start_clipboard_watch()

    # === 主題與階段切換 ===
    def _apply_theme(self, theme_name: str, save: bool = True) -> None:
        self.config.ui_theme = theme_name
        if save:
            self.config.save()
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(build_stylesheet(theme_name))
        self.theme_btn.setText("🌙" if theme_name == "sakura" else "🌸")
        self.sentence_widget.set_theme_name(theme_name)
        self.lookup_panel.set_theme_name(theme_name)
        self.overlay.apply_theme(theme_name)

    def _toggle_theme(self) -> None:
        next_theme = "starlight" if self.config.ui_theme == "sakura" else "sakura"
        self._apply_theme(next_theme, save=True)
        label = "🌙 星夜琉璃" if next_theme == "starlight" else "🌸 櫻霧輕透"
        self.statusBar().showMessage(f"已切換主題：{label}", 3000)

    def _apply_stage_ui(self) -> None:
        mode = self.config.stage_mode
        if mode in self.stage_buttons:
            self.stage_buttons[mode].setChecked(True)

        show_furi = mode == "lv1" and self.config.overlay_furigana_in_lv1
        self.sentence_widget.set_display_options(22, show_furi, theme_name=self.config.ui_theme)
        self.overlay.set_context(
            dict_preference=self.config.effective_dict_preference(),
            known_words=self.store.known_words,
            font_size=self.config.overlay_font_size,
            show_furigana=show_furi,
            opacity=self.config.overlay_opacity,
            theme_name=self.config.ui_theme,
        )
        if self._current_record and self._selected_token:
            self._show_token_in_panel(self._selected_token)

    def _set_stage_mode(self, mode: str) -> None:
        self.config.stage_mode = mode
        self.config.save()
        self._apply_stage_ui()
        names = {
            "lv1": "Lv.1 五十音破冰（精準假名提示 + 雙語）",
            "lv2": "Lv.2 精細沉浸（1T 高亮 + 雙語）",
            "lv3": "Lv.3 登樓撤梯（日日單語優先）",
        }
        self.statusBar().showMessage(f"已切換階段：{names.get(mode, mode)}", 3500)

    def _toggle_history_drawer(self, checked: bool) -> None:
        self.history_drawer.setVisible(checked)

    # === 語音自動環形緩衝、mpv 無損切片與試聽 ===
    def _start_audio_capture(self) -> None:
        if not self.config.capture_audio_on_mine:
            return
        hint = self.config.current_game_name
        worker = FunctionWorker(self.audio_recorder.start, hint)
        self._active_workers.append(worker)
        self.thread_pool.start(worker)

    def _toggle_audio_recording(self, checked: bool) -> None:
        self.config.capture_audio_on_mine = checked
        self.config.save()
        if checked:
            self._start_audio_capture()
        else:
            worker = FunctionWorker(self.audio_recorder.stop)
            self._active_workers.append(worker)
            self.thread_pool.start(worker)

    def _on_audio_state_changed(self, recording: bool, msg: str) -> None:
        self.audio_toggle_btn.blockSignals(True)
        self.audio_toggle_btn.setChecked(recording)
        self.audio_toggle_btn.setText("🎙 錄音中" if recording else "🎙 靜音")
        self.audio_toggle_btn.blockSignals(False)
        self.statusBar().showMessage(msg, 4000)

    def _extract_audio_for_record(self, rec: SentenceRecord) -> tuple[bytes, str] | None:
        # 1. 若為 mpv 本地影片且帶有準確字幕時間軸，優先由 FFmpeg 無損直接裁切原聲！
        if rec.video_path and rec.sub_start is not None and rec.sub_end is not None:
            if rec.audio_clip is not None:
                return rec.audio_clip
            mpv_clip = extract_video_audio_mp3(
                video_path=rec.video_path,
                start_sec=rec.sub_start,
                end_sec=rec.sub_end,
            )
            if mpv_clip is not None:
                rec.audio_clip = mpv_clip
                return mpv_clip

        # 2. 已結束的台詞若已緩存音訊，直接重用
        if rec.audio_clip is not None and rec.end_timestamp is not None:
            return rec.audio_clip

        if not self.config.capture_audio_on_mine:
            return None

        # 若背景錄音意外尚未啟動，自動喚起
        if not self.audio_recorder.is_recording:
            self.audio_recorder.start(self.config.current_game_name)

        # 若使用者剛跳出最新一句就立刻點製卡，於背景等待 350ms 確保聲優語音完整進入緩衝區
        if rec.end_timestamp is None and (time.time() - rec.timestamp) < 1.8:
            time.sleep(0.35)

        clip = self.audio_recorder.extract_mp3_clip(
            t_start=rec.timestamp,
            t_end=rec.end_timestamp,
            pre_pad_sec=self.config.audio_pre_pad_ms / 1000.0,
            max_duration_sec=float(self.config.audio_max_seconds),
        )
        if clip is not None and rec.end_timestamp is not None:
            rec.audio_clip = clip
        return clip

    def _preview_current_audio(self) -> None:
        if self._current_record is None:
            return
        rec = self._current_record
        self.statusBar().showMessage("🔊 正在擷取本句原聲語音...")

        def _do() -> tuple[bytes, str] | None:
            return self._extract_audio_for_record(rec)

        worker = FunctionWorker(_do)
        worker.signals.finished.connect(self._on_audio_preview_ready)
        worker.signals.failed.connect(
            lambda err: self.statusBar().showMessage(f"語音試聽失敗：{err}", 4000)
        )
        self._active_workers.append(worker)
        self.thread_pool.start(worker)

    def _on_audio_preview_ready(self, clip: tuple[bytes, str] | None) -> None:
        if not clip:
            self.statusBar().showMessage(
                "目前緩衝區無此句語音（請確認右上角「🎙 錄音中」已開啟，且遊戲／影片正在播放語音）。",
                5000,
            )
            return
        mp3_bytes, name = clip
        play_mp3_bytes_async(mp3_bytes)
        self.statusBar().showMessage(f"🔊 正在試聽原聲（{len(mp3_bytes) / 1024:.1f} KB · {name}）", 3500)

    # === Anki 同步與製卡 ===
    def _update_budget_pill(self) -> None:
        count = self.store.today_mined_count
        limit = self.config.daily_card_limit
        self.budget_pill.setText(f"{count}/{limit}")
        self.budget_pill.setObjectName("PillWarn" if count >= limit else "PillInfo")
        self.budget_pill.style().unpolish(self.budget_pill)
        self.budget_pill.style().polish(self.budget_pill)

    def _background_sync_anki(self) -> None:
        def _sync() -> dict:
            ver = self.anki.ping()
            self.anki.ensure_deck(self.config.deck_name)
            self.anki.ensure_note_type(self.config.note_type)
            known = (
                self.anki.fetch_known_words(self.config.deck_name)
                if self.config.auto_sync_known_words
                else set()
            )
            today_count = self.anki.get_today_mined_count(self.config.deck_name)
            return {"version": ver, "known": known, "today": today_count}

        worker = FunctionWorker(_sync)
        worker.signals.finished.connect(self._on_anki_sync_ok)
        worker.signals.failed.connect(self._on_anki_sync_fail)
        self._active_workers.append(worker)
        self.thread_pool.start(worker)

    def _on_anki_sync_ok(self, info: dict) -> None:
        self._anki_connected = True
        self.store.known_words.update(info["known"])
        self.store.today_mined_count = int(info["today"])
        self._update_budget_pill()

        self.anki_pill.setText("Anki ✓")
        self.anki_pill.setToolTip(f"已連線 AnkiConnect，已同步 {len(self.store.known_words)} 個學習詞彙")
        self.anki_pill.setObjectName("PillOk")
        self.anki_pill.style().unpolish(self.anki_pill)
        self.anki_pill.style().polish(self.anki_pill)

        if self._current_record is not None:
            self._retokenize_current()

    def _on_anki_sync_fail(self, _err: str) -> None:
        self._anki_connected = False
        self.anki_pill.setText("Anki 未連")
        self.anki_pill.setObjectName("PillWarn")
        self.anki_pill.style().unpolish(self.anki_pill)
        self.anki_pill.style().polish(self.anki_pill)

    # === 多來源文本進流（Galgame Hook / mpv 影片 / OCR / 剪貼簿） ===
    def _on_ingestion_sentence_meta(self, text: str, source: str, media_title: str) -> None:
        self._ingest_sentence(text, source=source, media_title=media_title)

    def _on_mpv_state_changed(self, connected: bool, msg: str) -> None:
        if connected:
            self.game_badge.setText(msg)
            self.game_badge.setObjectName("PillOk")
            self.game_badge.style().unpolish(self.game_badge)
            self.game_badge.style().polish(self.game_badge)
            if self.overlay.isVisible() and self.config.overlay_replace_mode:
                self.mpv_bridge.set_native_subtitle_visible(False)
        self.statusBar().showMessage(msg, 4500)

    def _on_mpv_subtitle(self, ev: MpvSubtitleEvent) -> None:
        if self.ingestion.paused:
            return
        if ev.media_title:
            short = ev.media_title[:24] + ("…" if len(ev.media_title) > 24 else "")
            self.game_badge.setText(f"🎬 {short}")
            self.game_badge.setObjectName("PillOk")
            self.game_badge.style().unpolish(self.game_badge)
            self.game_badge.style().polish(self.game_badge)
        self._ingest_sentence(
            ev.text,
            source="mpv",
            media_title=ev.media_title,
            video_path=ev.video_path,
            sub_start=ev.sub_start,
            sub_end=ev.sub_end,
        )

    def _ingest_sentence(
        self,
        text: str,
        source: str,
        media_title: str = "",
        video_path: str = "",
        sub_start: float | None = None,
        sub_end: float | None = None,
    ) -> None:
        prev_rec = self.store.latest()
        tokenized = self.tokenizer.tokenize(text, self.store.known_words)
        analysis = self.grammar_engine.analyze(tokenized)
        rec = self.store.add(
            tokenized,
            source=source,
            media_title=media_title,
            video_path=video_path,
            sub_start=sub_start,
            sub_end=sub_end,
            grammar_analysis=analysis,
        )
        # 1. 新台詞到達時，自動於背景將上一句的聲優語音切片凍結保存，避免長時間細讀後語音被緩衝覆寫！
        if (
            prev_rec is not None
            and prev_rec is not rec
            and prev_rec.source != "demo"
            and prev_rec.audio_clip is None
            and self.config.capture_audio_on_mine
        ):
            self._freeze_audio_in_background(prev_rec)

        # 2. 自動於背景靜默捕獲當下遊戲 / 動畫畫面 CG 快照（音畫情景記憶核心！）
        if source in ("hook", "mpv") and (
            self.config.capture_screenshot_on_mine or self.config.quick_forge_before_mine
        ):
            self._snapshot_scene_in_background(rec)

        if source != "demo":
            src_label = media_title or self.config.current_game_name or source
            self.progress_store.record_sentence_seen(analysis.grammar_hits, text, src_label)

        self._refresh_history_list(select_record_id=rec.record_id)
        self._display_record(rec)
        if self.overlay.isVisible():
            self.overlay.show_record(rec)

    def _freeze_audio_in_background(self, rec: SentenceRecord) -> None:
        def _freeze() -> None:
            if rec.audio_clip is None:
                self._extract_audio_for_record(rec)

        worker = FunctionWorker(_freeze)
        self._active_workers.append(worker)
        self.thread_pool.start(worker)

    def _snapshot_scene_in_background(self, rec: SentenceRecord) -> None:
        def _snap() -> None:
            if rec.video_path and rec.sub_start is not None:
                frame = extract_video_frame_webp(rec.video_path, rec.sub_start)
                if frame is not None:
                    rec.screenshot_clip = frame
                    return
            shot = self.adapter.capture_screenshot_webp()
            if shot is not None:
                rec.screenshot_clip = shot

        worker = FunctionWorker(_snap)
        self._active_workers.append(worker)
        self.thread_pool.start(worker)

    def _check_current_sentence_translation(self) -> None:
        if self._current_record is None:
            return
        rec = self._current_record
        if rec.sentence_translation:
            self.translation_label.setText(f"🌐 語境理解：{rec.sentence_translation}")
            self.translation_label.setVisible(not self.translation_label.isVisible())
            return

        self.translate_btn.setEnabled(False)
        self.translate_btn.setText("核對中…")
        worker = FunctionWorker(translate_sentence_free, rec.raw_text)
        worker.signals.finished.connect(lambda tr, r=rec: self._on_sentence_trans_ok(r, tr))
        worker.signals.failed.connect(self._on_sentence_trans_fail)
        self._active_workers.append(worker)
        self.thread_pool.start(worker)

    def _on_overlay_state_saved(self, state: dict) -> None:
        self.config.overlay_x = int(state.get("x", -1))
        self.config.overlay_y = int(state.get("y", -1))
        self.config.overlay_width = int(state.get("width", 820))
        self.config.overlay_height = int(state.get("height", 128))
        self.config.overlay_locked = bool(state.get("locked", False))
        self.config.overlay_hover_lookup = bool(state.get("hover_lookup", True))
        self.config.overlay_always_on_top = bool(state.get("always_on_top", True))
        self.config.overlay_replace_mode = bool(state.get("replace_mode", True))
        self.config.overlay_backdrop = str(state.get("backdrop", "vn_dark"))
        self.config.overlay_font_size = int(state.get("font_size", self.config.overlay_font_size))
        self.config.overlay_guide_dismissed = bool(state.get("guide_dismissed", False))
        self.config.save()

    def _on_overlay_replace_mode_changed(self, replace_mode: bool) -> None:
        self.config.overlay_replace_mode = replace_mode
        if self.mpv_bridge.is_connected and self.overlay.isVisible():
            self.mpv_bridge.set_native_subtitle_visible(not replace_mode)

    def _translate_record_for_overlay(self, rec: SentenceRecord) -> None:
        worker = FunctionWorker(translate_sentence_free, rec.raw_text)
        worker.signals.finished.connect(lambda tr, r=rec: self._on_sentence_trans_ok(r, tr))
        worker.signals.failed.connect(self._on_sentence_trans_fail)
        self._active_workers.append(worker)
        self.thread_pool.start(worker)

    def _on_sentence_trans_ok(self, rec: SentenceRecord, trans: str) -> None:
        self.translate_btn.setEnabled(True)
        self.translate_btn.setText("🌐 核對句意")
        if not trans:
            self.statusBar().showMessage("無法取得參考句意，可改點「📋 問 Gemini/GPT」。", 4000)
            return
        rec.sentence_translation = trans
        if self._current_record is rec:
            self.translation_label.setText(f"🌐 語境理解：{trans}")
            self.translation_label.show()
        self.overlay.set_inline_translation(rec, trans)

    def _on_sentence_trans_fail(self, _err: str) -> None:
        self.translate_btn.setEnabled(True)
        self.translate_btn.setText("🌐 核對句意")
        self.statusBar().showMessage("目前無法連線核對翻譯，可看「🧭 理解本句與語法」離線語序骨架或複製給 Gemini/GPT。", 4500)

    def _on_hook_dialogue(self, sentence: str, _thread_key: str) -> None:
        if self.ingestion.paused:
            return
        self._ingest_sentence(sentence, source="hook", media_title=self.config.current_game_name)

    def _on_hook_dialogue_extended(self, sentence: str, _thread_key: str) -> None:
        """打字機停頓後補完後半句：原地更新目前這一句，保留原始語音起始時間戳且不漏字！"""
        if self.ingestion.paused:
            return
        prev_ids = (
            {h.pattern_id for h in self._current_record.grammar_analysis.grammar_hits}
            if (self._current_record and self._current_record.grammar_analysis)
            else set()
        )
        tokenized = self.tokenizer.tokenize(sentence, self.store.known_words)
        analysis = self.grammar_engine.analyze(tokenized)
        rec = self.store.extend_latest(tokenized, grammar_analysis=analysis)

        new_hits = [h for h in analysis.grammar_hits if h.pattern_id not in prev_ids]
        if new_hits:
            self.progress_store.record_sentence_seen(
                new_hits,
                sentence,
                rec.media_title or self.config.current_game_name or "hook",
            )

        self._refresh_history_list(select_record_id=rec.record_id)
        self._display_record(rec)
        if self.overlay.isVisible():
            self.overlay.show_record(rec)

    def _display_record(self, rec: SentenceRecord) -> None:
        self._current_record = rec
        self._selected_token = None
        if rec.grammar_analysis is None:
            rec.grammar_analysis = self.grammar_engine.analyze(rec.tokenized)

        self.sentence_widget.set_sentence(rec.tokenized)
        self.lookup_panel.set_sentence_analysis(rec.grammar_analysis)

        if rec.sentence_translation:
            self.translation_label.setText(f"🌐 語境理解：{rec.sentence_translation}")
            self.translation_label.show()
        else:
            self.translation_label.hide()

        hit_cnt = len(rec.grammar_analysis.grammar_hits)
        grammar_suffix = f" · 🧩{hit_cnt}" if hit_cnt else ""
        if rec.tokenized.is_1t_sentence:
            self.sentence_badge.setText(f"★ 1T 黃金句{grammar_suffix}")
            self.sentence_badge.setObjectName("PillOk")
        else:
            self.sentence_badge.setText(f"生詞 {rec.tokenized.unknown_count}{grammar_suffix}")
            self.sentence_badge.setObjectName("PillInfo")
        self.sentence_badge.style().unpolish(self.sentence_badge)
        self.sentence_badge.style().polish(self.sentence_badge)

        auto_tok = next((t for t in rec.tokenized.tokens if t.is_1t_candidate), None)
        if auto_tok is None:
            auto_tok = next(
                (t for t in rec.tokenized.tokens if t.is_content_word and not t.is_known),
                None,
            )
        if auto_tok is not None:
            self._selected_token = auto_tok
            self._show_token_in_panel(auto_tok)

    def _refresh_history_list(self, select_record_id: int | None = None) -> None:
        self.history_list.blockSignals(True)
        self.history_list.clear()
        target_row = 0
        for row, rec in enumerate(self.store.all_records()):
            prefix = "★ " if rec.tokenized.is_1t_sentence else ""
            mined_tag = f" ✓{len(rec.mined_words)}" if rec.mined_words else ""
            preview = rec.raw_text.replace("\n", " ")
            if len(preview) > 26:
                preview = preview[:26] + "…"
            item = QListWidgetItem(f"{prefix}{preview}{mined_tag}")
            item.setData(Qt.ItemDataRole.UserRole, rec.record_id)
            self.history_list.addItem(item)
            if select_record_id is not None and rec.record_id == select_record_id:
                target_row = row
        if self.history_list.count() > 0:
            self.history_list.setCurrentRow(target_row)
        self.history_list.blockSignals(False)

    def _on_history_selected(self, row: int) -> None:
        if row < 0:
            return
        item = self.history_list.item(row)
        if not item:
            return
        rec = self.store.get_by_id(item.data(Qt.ItemDataRole.UserRole))
        if rec:
            self._display_record(rec)
            if self.overlay.isVisible():
                self.overlay.show_record(rec)

    def _step_history_prev(self) -> None:
        row = self.history_list.currentRow()
        if row + 1 < self.history_list.count():
            self.history_list.setCurrentRow(row + 1)

    def _step_history_next(self) -> None:
        row = self.history_list.currentRow()
        if row > 0:
            self.history_list.setCurrentRow(row - 1)

    def _on_main_token_clicked(self, token: Token, _anchor: QPoint) -> None:
        self._selected_token = token
        self._show_token_in_panel(token)

    def _show_token_in_panel(self, token: Token) -> None:
        already_known = token.lemma in self.store.known_words or token.surface in self.store.known_words
        particle_info = self.grammar_engine.get_particle_info(token.surface)
        self.lookup_panel.show_token(
            token=token,
            dict_preference=self.config.effective_dict_preference(),
            already_known=already_known,
            particle_info=particle_info,
        )

    # === 零付費 API：一鍵複製高品質提問詞給 Google AI Pro / ChatGPT Plus ===
    def _on_panel_copy_prompt(self, token: Token | None) -> None:
        if self._current_record is not None:
            self._copy_web_ai_prompt_for_record(self._current_record, token)

    def _copy_web_ai_prompt_for_record(self, record: SentenceRecord, token: Token | None) -> None:
        target_word = ""
        target_reading = ""
        if token is not None:
            match = token.lookup
            target_word = match.lemma if match else token.lemma
            target_reading = match.reading if match else token.reading
        hits = record.grammar_analysis.grammar_hits if record.grammar_analysis else []
        prompt = build_web_ai_prompt(
            sentence=record.raw_text,
            target_word=target_word,
            target_reading=target_reading,
            grammar_hits=hits,
        )
        cb = QGuiApplication.clipboard()
        if cb is not None:
            cb.setText(prompt)
        msg = "📋 已複製日語拆解提問詞！直接 Ctrl+V 貼到你的 Gemini Pro / ChatGPT Plus 網頁版即可。"
        self.statusBar().showMessage(msg, 5500)
        if self.overlay.isVisible():
            self.overlay.show_toast("📋 已複製提問詞，去貼給 Gemini / GPT！")

    # === Refold 精煉製卡（精選核心釋義 + 聲優原聲 + 畫面 CG + 語境理解 + 離線語序句型） ===
    def _on_panel_mine(self, token: Token) -> None:
        if self._current_record is not None:
            self._mine_record_token(self._current_record, token)

    def _mine_record_token(self, record: SentenceRecord, token: Token) -> None:
        """依攻略方法論製卡：拒絕硬搬整頁詞典，精選單一核心意思＋聲優原聲＋當下畫面 CG＋整句理解。"""
        if self.store.today_mined_count >= self.config.daily_card_limit:
            self.statusBar().showMessage(
                f"💡 今日已達建議上限（{self.config.daily_card_limit} 張），適時切換無阻沉浸享受劇情防爆倉。",
                5000,
            )

        match = token.lookup
        lemma = match.lemma if match else token.lemma
        reading = match.reading if match else token.reading
        pitch = "/".join(match.pitches[:3]) if (match and match.pitches) else ""
        bi_senses = match.concise_senses("bilingual", max_senses=5, strict=True) if match else []
        mono_senses = match.concise_senses("monolingual", max_senses=4, strict=True) if match else []
        base_bi = match.best_concise_meaning("bilingual", strict=True) if match else ""
        default_bi = (
            format_contextual_meaning(token.surface, lemma, base_bi, match.reasons)
            if (match and base_bi)
            else base_bi
        )
        default_mono = match.best_concise_meaning("monolingual", strict=True) if match else ""

        if default_bi and default_bi not in bi_senses:
            bi_senses = [default_bi] + bi_senses

        alt_lemmas: list[tuple[str, str, str, str]] = []
        if match and match.alt_matches:
            for alt in match.alt_matches[:3]:
                alt_base = alt.best_concise_meaning("bilingual", strict=True)
                alt_ctx = format_contextual_meaning(token.surface, alt.lemma, alt_base, alt.reasons) if alt_base else ""
                alt_mn = alt.best_concise_meaning("monolingual", strict=True)
                alt_lemmas.append((alt.lemma, alt.reading, alt_ctx or alt_base, alt_mn))

        # 若點的是助詞，直接使用白話助詞骨架角色！
        particle = self.grammar_engine.get_particle_info(token.surface)
        if particle is not None:
            p_short = f"【助詞·{particle.role}】{particle.meaning}"
            bi_senses = [p_short] + [s for s in bi_senses if s != p_short]
            default_bi = p_short
            alt_lemmas = []

        highlighted_sentence = highlight_target_in_sentence(record.raw_text, token.start, token.end)
        analysis = record.grammar_analysis or self.grammar_engine.analyze(record.tokenized)
        grammar_html = analysis.to_compact_html(accent_color="#d9468f", muted_color="#7a7a8c")
        source_name = record.media_title or self.config.current_game_name or record.source

        # 先取出音訊與畫面快照供精煉確認或直寫
        audio_clip = self._extract_audio_for_record(record)
        shot_clip = record.screenshot_clip

        if self.config.quick_forge_before_mine:
            dlg = CardForgeDialog(
                stage_mode=self.config.stage_mode,
                sentence_highlighted=highlighted_sentence,
                raw_sentence=record.raw_text,
                target_word=lemma,
                target_reading=reading,
                pitch_accent=pitch,
                bi_senses=bi_senses,
                mono_senses=mono_senses,
                default_bi=default_bi,
                default_mono=default_mono,
                sentence_translation=record.sentence_translation,
                audio_clip=audio_clip,
                screenshot_clip=shot_clip,
                include_audio_default=self.config.capture_audio_on_mine,
                include_screenshot_default=self.config.capture_screenshot_on_mine or (shot_clip is not None),
                thread_pool=self.thread_pool,
                manual_record_fn=lambda: self.audio_recorder.capture_manual_window(4.0),
                capture_shot_fn=self.adapter.capture_screenshot_webp,
                alt_lemmas=alt_lemmas,
                parent=self,
            )
            if not dlg.exec() or dlg.result_data is None:
                return
            forged: ForgedCardData = dlg.result_data
            if forged.sentence_translation:
                record.sentence_translation = forged.sentence_translation
            if forged.audio_clip:
                record.audio_clip = forged.audio_clip
            if forged.screenshot_clip:
                record.screenshot_clip = forged.screenshot_clip
        else:
            forged = ForgedCardData(
                stage_mode=self.config.stage_mode,
                sentence=highlighted_sentence,
                target_word=lemma,
                target_reading=reading,
                pitch_accent=pitch,
                definition_bilingual=default_bi,
                definition_monolingual=default_mono,
                sentence_translation=record.sentence_translation,
                include_audio=self.config.capture_audio_on_mine,
                audio_clip=audio_clip if self.config.capture_audio_on_mine else None,
                include_screenshot=self.config.capture_screenshot_on_mine,
                screenshot_clip=shot_clip if self.config.capture_screenshot_on_mine else None,
            )

        def _do_mine() -> dict:
            self.anki.ensure_deck(self.config.deck_name)
            self.anki.ensure_note_type(self.config.note_type)

            audio_bytes, audio_name = (forged.audio_clip if forged.audio_clip else (None, ""))
            screenshot_bytes, screenshot_name = (
                forged.screenshot_clip if forged.screenshot_clip else (None, "")
            )
            if forged.include_screenshot and screenshot_bytes is None:
                shot = self.adapter.capture_screenshot_webp()
                if shot:
                    screenshot_bytes, screenshot_name = shot

            payload = AnkiCardPayload(
                target_word=forged.target_word or lemma,
                target_reading=forged.target_reading,
                pitch_accent=forged.pitch_accent,
                sentence=forged.sentence,
                sentence_furigana=record.tokenized.furigana_html,
                sentence_translation=forged.sentence_translation,
                definition_bilingual=forged.definition_bilingual,
                definition_monolingual=forged.definition_monolingual,
                source_game=source_name,
                stage_mode=forged.stage_mode,
                ai_grammar_notes=grammar_html,
                audio_bytes=audio_bytes,
                audio_filename=audio_name,
                screenshot_bytes=screenshot_bytes,
                screenshot_filename=screenshot_name,
            )
            note_id = self.anki.add_card(
                self.config.deck_name,
                self.config.note_type,
                payload,
                allow_duplicate=True,
            )
            return {
                "note_id": note_id,
                "record": record,
                "lemma": forged.target_word or lemma,
                "has_audio": bool(audio_bytes),
                "has_shot": bool(screenshot_bytes),
            }

        self.statusBar().showMessage(f"✨ 正在寫入精煉記憶卡「{forged.target_word or lemma}」至 Anki...")
        worker = FunctionWorker(_do_mine)
        worker.signals.finished.connect(self._on_mine_success)
        worker.signals.failed.connect(self._on_mine_failed)
        self._active_workers.append(worker)
        self.thread_pool.start(worker)

    def _on_mine_success(self, res: dict) -> None:
        rec: SentenceRecord = res["record"]
        lemma: str = res["lemma"]
        badges: list[str] = []
        if res.get("has_audio"):
            badges.append("🔊原聲")
        if res.get("has_shot"):
            badges.append("🖼畫面")
        media_tag = f"（{'＋'.join(badges)}）" if badges else "（純文字卡）"

        self.store.mark_mined(rec, lemma)
        self._update_budget_pill()
        self._refresh_history_list(select_record_id=rec.record_id)

        msg = f"✓ 已收錄精煉卡「{lemma}」{media_tag} · 今日 {self.store.today_mined_count}/{self.config.daily_card_limit}"
        self.statusBar().showMessage(msg, 5500)
        if self.overlay.isVisible():
            self.overlay.show_toast(msg)

    def _mine_grammar_card(self, pattern: dict[str, Any], example_sentence: str) -> None:
        """從「🗺️ 系統化學習藍圖」一鍵把 N5→N1 核心語法與真實例句收錄至 Anki。"""
        tokenized = self.tokenizer.tokenize(example_sentence, self.store.known_words)
        analysis = self.grammar_engine.analyze(tokenized)
        grammar_html = analysis.to_compact_html(accent_color="#d9468f", muted_color="#7a7a8c")
        title = f"[{pattern['level']}] {pattern['name']}"
        bi_def = (
            f"【{pattern['category']}】{pattern['meaning']}\n"
            f"接續公式：{pattern['formula']}\n"
            f"標準例句：{pattern['example']}"
        )

        def _do_mine() -> dict:
            self.anki.ensure_deck(self.config.deck_name)
            self.anki.ensure_note_type(self.config.note_type)
            payload = AnkiCardPayload(
                target_word=title,
                target_reading=str(pattern["formula"]),
                sentence=example_sentence,
                sentence_furigana=tokenized.furigana_html,
                definition_bilingual=bi_def,
                source_game=f"學習藍圖 · {pattern['level']}",
                stage_mode=self.config.stage_mode,
                ai_grammar_notes=grammar_html,
            )
            note_id = self.anki.add_card(
                self.config.deck_name,
                self.config.note_type,
                payload,
                allow_duplicate=True,
            )
            return {"note_id": note_id, "title": title}

        self.statusBar().showMessage(f"✨ 正在收錄語法卡「{title}」至 Anki...")
        worker = FunctionWorker(_do_mine)
        worker.signals.finished.connect(self._on_grammar_mine_success)
        worker.signals.failed.connect(self._on_mine_failed)
        self._active_workers.append(worker)
        self.thread_pool.start(worker)

    def _on_grammar_mine_success(self, res: dict) -> None:
        self.store.today_mined_count += 1
        self._update_budget_pill()
        self.statusBar().showMessage(f"✓ 已將語法卡「{res['title']}」收錄至 Anki！", 5000)

    def _on_mine_failed(self, err: str) -> None:
        self.statusBar().showMessage(f"收錄失敗：{err}", 6000)
        QMessageBox.warning(
            self,
            "收錄 Anki 失敗",
            f"{err}\n\n請確認 Anki 已開啟，或從右上角「☰ → 一鍵初始化精靈」檢查連線。",
        )

    def _submit_manual_input(self) -> None:
        txt = self.manual_input.text().strip()
        if not txt:
            return
        self.ingestion.submit_manual_text(txt, source="manual")
        self.manual_input.clear()

    # === 遊戲啟動、Hook 掛載與線程切換 ===
    def _on_hook_console_log(self, msg: str) -> None:
        self._hook_console_log.append(msg)
        if len(self._hook_console_log) > 200:
            self._hook_console_log = self._hook_console_log[-200:]

    def _on_hook_state_changed(self, attached: bool, label: str) -> None:
        if attached:
            self.game_badge.setText(label)
            self.game_badge.setObjectName("PillOk")
        else:
            self.game_badge.setText(self.config.current_game_name or "🌸 等待遊戲 / 影片字幕")
            self.game_badge.setObjectName("PillInfo")
        self.game_badge.style().unpolish(self.game_badge)
        self.game_badge.style().polish(self.game_badge)
        self.statusBar().showMessage(label, 5000)

    def _on_hook_threads_updated(self, threads: list[HookThreadInfo], selected_key: str) -> None:
        self.thread_combo.blockSignals(True)
        self.thread_combo.clear()
        auto_suffix = f" ({threads[0].hook_name})" if threads else ""
        self.thread_combo.addItem(f"🧵 自動{auto_suffix}", "auto")
        target_idx = 0
        for idx, info in enumerate(threads[:15], start=1):
            self.thread_combo.addItem(info.display_label(), info.thread_key)
            if info.thread_key == selected_key:
                target_idx = idx
        self.thread_combo.setCurrentIndex(target_idx)
        self.thread_combo.blockSignals(False)

    def _on_hook_thread_changed(self, idx: int) -> None:
        if idx < 0:
            return
        key = str(self.thread_combo.itemData(idx) or "auto")
        self.hook_manager.set_selected_thread(key)

    def open_hook_manager(self) -> None:
        dlg = HookManagerDialog(
            config=self.config,
            hook_manager=self.hook_manager,
            thread_pool=self.thread_pool,
            console_history=self._hook_console_log,
            parent=self,
        )
        dlg.target_attached.connect(self._on_hook_target_attached)
        dlg.exec()

    def _on_hook_target_attached(self, target: HookTargetProcess) -> None:
        self.config.current_game_name = target.name
        self.config.save()
        if self.config.capture_audio_on_mine and not self.audio_recorder.is_recording:
            QTimer.singleShot(1500, self._start_audio_capture)

    def _detect_and_attach_running_game(self) -> None:
        self.statusBar().showMessage("正在掃描執行中的 Wine 遊戲進程並準備自動 Hook...")

        def _scan() -> list[HookTargetProcess]:
            ensure_textractor_installed()
            return discover_wine_game_targets(self.config.wine_command)

        worker = FunctionWorker(_scan)
        worker.signals.finished.connect(self._on_detect_and_attach_done)
        worker.signals.failed.connect(
            lambda err: self.statusBar().showMessage(f"偵測掛載失敗：{err}", 5000)
        )
        self._active_workers.append(worker)
        self.thread_pool.start(worker)

    def _on_detect_and_attach_done(self, targets: list[HookTargetProcess]) -> None:
        if not targets:
            self.statusBar().showMessage(
                "未偵測到執行中遊戲，可從「🎮 遊戲/影片」啟動生肉、連接 mpv 影片或使用「📷 OCR」。",
                5500,
            )
            return
        if len(targets) == 1:
            tgt = targets[0]
            try:
                self.hook_manager.attach_target(tgt)
                self._on_hook_target_attached(tgt)
            except Exception as exc:
                QMessageBox.warning(self, "掛載失敗", str(exc))
            return
        self.open_hook_manager()

    def _launch_game_dialog(self) -> None:
        start_dir = str(Path.home())
        if self.config.recent_games:
            last_path = Path(self.config.recent_games[0].get("path", ""))
            if last_path.parent.exists():
                start_dir = str(last_path.parent)

        exe, _ = QFileDialog.getOpenFileName(
            self,
            "選擇日文生肉 Galgame 執行檔 (.exe)",
            start_dir,
            "Windows Executable (*.exe);;All Files (*)",
        )
        if not exe:
            return
        exe_path = Path(exe)
        game_name = exe_path.parent.name or exe_path.stem
        self._launch_specific_game(exe_path, game_name)

    def _launch_specific_game(self, exe_path: Path, game_name: str) -> None:
        try:
            pid = self.adapter.launch_game_jp_locale(
                exe_path,
                launcher_cmd=self.config.wine_command,
                locale=self.config.wine_locale,
            )
            self.config.add_recent_game(game_name, str(exe_path))
            self.config.save()
            self._rebuild_game_menu()
            self.game_badge.setText(f"🎮 {game_name}")
            self.statusBar().showMessage(
                f"🎮 已啟動「{game_name}」(PID {pid})，正在背景自動注入 Hook...",
                9000,
            )
            self._auto_attach_launched_game(exe_path, game_name)
        except Exception as exc:
            QMessageBox.warning(self, "啟動失敗", str(exc))

    def _auto_attach_launched_game(self, exe_path: Path, game_name: str) -> None:
        target_exe_name = exe_path.name
        arch = detect_pe_arch(exe_path)
        wine_cmd = self.config.wine_command

        def _wait() -> HookTargetProcess | None:
            ensure_textractor_installed()
            for _ in range(20):
                time.sleep(1.2)
                for exe_name, win_pid in list_wine_tasklist(wine_command=wine_cmd):
                    if exe_name.lower() == target_exe_name.lower():
                        return HookTargetProcess(
                            name=game_name,
                            exe_name=exe_name,
                            win_pid=win_pid,
                            arch=arch,
                            exe_path=str(exe_path),
                            wine_command=wine_cmd,
                        )
            return None

        worker = FunctionWorker(_wait)
        worker.signals.finished.connect(self._on_auto_attach_ready)
        worker.signals.failed.connect(
            lambda err: self.statusBar().showMessage(f"⚠ 自動 Hook 失敗：{err}", 6000)
        )
        self._active_workers.append(worker)
        self.thread_pool.start(worker)

    def _on_auto_attach_ready(self, target: HookTargetProcess | None) -> None:
        if target is None:
            self.statusBar().showMessage(
                "⚠ 等待遊戲逾時；進入畫面後請點「🎮 遊戲/影片 → 偵測並一鍵掛載」。",
                7000,
            )
            return
        try:
            self.hook_manager.attach_target(target)
            self._on_hook_target_attached(target)
        except Exception as exc:
            self.statusBar().showMessage(f"⚠ 自動掛載失敗：{exc}", 7000)

    # === OCR、覆蓋層、學習藍圖、mpv 影片橋接與工具視窗 ===
    def _trigger_region_ocr(self) -> None:
        self.ocr_btn.setEnabled(False)
        self.statusBar().showMessage("📷 請拖曳框選對話框區域（Esc 取消）...")
        worker = FunctionWorker(capture_region_and_ocr, False)
        worker.signals.finished.connect(self._on_ocr_done)
        worker.signals.failed.connect(self._on_ocr_failed)
        self._active_workers.append(worker)
        self.thread_pool.start(worker)

    def _on_ocr_done(self, text: str) -> None:
        self.ocr_btn.setEnabled(True)
        if not text:
            self.statusBar().showMessage("已取消 OCR 或未辨識到日文。", 3500)
            return
        self._ingest_sentence(text, source="ocr")
        self.statusBar().showMessage(f"📷 OCR 辨識成功：{text[:34]}", 4500)

    def _on_ocr_failed(self, err: str) -> None:
        self.ocr_btn.setEnabled(True)
        QMessageBox.warning(self, "OCR 失敗", err)

    def _toggle_overlay(self, checked: bool) -> None:
        if checked:
            self._apply_stage_ui()
            if self._current_record is not None:
                self.overlay.show_record(self._current_record)
            self.overlay.show()
            self.overlay.raise_()
        else:
            self.overlay.hide()

    def _on_overlay_visibility_changed(self, visible: bool) -> None:
        if hasattr(self, "overlay_btn"):
            self.overlay_btn.blockSignals(True)
            self.overlay_btn.setChecked(visible)
            self.overlay_btn.blockSignals(False)
        if self.mpv_bridge.is_connected:
            if visible and self.config.overlay_replace_mode:
                self.mpv_bridge.set_native_subtitle_visible(False)
            elif not visible:
                self.mpv_bridge.set_native_subtitle_visible(True)

    def _toggle_listening(self, paused: bool) -> None:
        self.ingestion.paused = paused
        self.statusBar().showMessage("已暫停監聽（無阻沉浸）" if paused else "已恢復自動監聽台詞與字幕", 3500)

    def open_learning_blueprint(self) -> None:
        dlg = LearningBlueprintDialog(
            grammar=self.grammar_engine,
            progress_store=self.progress_store,
            known_words_count=len(self.store.known_words),
            stage_mode=self.config.stage_mode,
            theme_name=self.config.ui_theme,
            parent=self,
        )
        dlg.mine_grammar_requested.connect(self._mine_grammar_card)
        dlg.exec()

    def open_media_bridge(self) -> None:
        dlg = MediaBridgeDialog(
            config=self.config,
            mpv_bridge=self.mpv_bridge,
            parent=self,
        )
        dlg.mpv_launched.connect(lambda: self._toggle_overlay(True) if not self.overlay.isVisible() else None)
        dlg.exec()

    def open_setup_wizard(self) -> None:
        dlg = SetupWizardDialog(
            config=self.config,
            adapter=self.adapter,
            dictionary=self.dictionary,
            anki=self.anki,
            thread_pool=self.thread_pool,
            parent=self,
        )
        dlg.anki_initialized.connect(lambda r: self._background_sync_anki() if r.connected else None)
        dlg.dictionary_updated.connect(self._retokenize_current)
        dlg.exec()
        self.config.first_run_completed = True
        self.config.save()

    def open_dictionary_manager(self) -> None:
        dlg = DictionaryManagerDialog(self.dictionary, self.thread_pool, self)
        dlg.dictionary_updated.connect(self._retokenize_current)
        dlg.exec()

    def open_difficulty_guide(self) -> None:
        dlg = DifficultyGuideDialog(theme_name=self.config.ui_theme, parent=self)
        dlg.exec()

    def open_settings(self) -> None:
        prev_theme = self.config.ui_theme
        prev_audio = self.config.capture_audio_on_mine
        dlg = SettingsDialog(self.config, self.thread_pool, self)
        if dlg.exec():
            self.anki = AnkiConnectClient(self.config.anki_url)
            self.ingestion.dedup_repeated = self.config.dedup_repeated_chars
            self.hook_manager.dedup_repeated = self.config.dedup_repeated_chars
            self.ingestion.ignore_non_japanese = self.config.ignore_non_japanese
            if self.config.ws_enabled:
                self.ingestion.start_websocket(self.config.ws_host, self.config.ws_port)
            else:
                self.ingestion.stop_websocket()
            if self.config.clipboard_watch:
                self.ingestion.start_clipboard_watch()
            else:
                self.ingestion.stop_clipboard_watch()
            if self.config.ui_theme != prev_theme:
                self._apply_theme(self.config.ui_theme, save=False)
            if self.config.capture_audio_on_mine != prev_audio:
                if self.config.capture_audio_on_mine:
                    self._start_audio_capture()
                else:
                    worker = FunctionWorker(self.audio_recorder.stop)
                    self._active_workers.append(worker)
                    self.thread_pool.start(worker)
            self._apply_stage_ui()
            self._update_budget_pill()
            self._background_sync_anki()

    def _retokenize_current(self) -> None:
        if self._current_record is not None:
            self._current_record.tokenized = self.tokenizer.tokenize(
                self._current_record.raw_text, self.store.known_words
            )
            self._current_record.grammar_analysis = self.grammar_engine.analyze(
                self._current_record.tokenized
            )
            self._display_record(self._current_record)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.hook_manager.detach()
        self.mpv_bridge.stop()
        self.audio_recorder.stop(emit_signal=False)
        self.overlay.shutdown_window_pin()
        self.overlay.close()
        self.ingestion.shutdown()
        self.progress_store.close()
        self.dictionary.close()
        super().closeEvent(event)
