"""GalAnki 輕量二次元伴讀主視窗：Galgame Hook + mpv 影片字幕 + 原聲錄製 + 系統化 N5→N1 學習藍圖 + 一鍵 Anki 製卡。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication, QIcon, QKeySequence, QShortcut
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
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from galgame_anki.adapters import create_platform_adapter
from galgame_anki.adapters.media_utils import (
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
from galgame_anki.adapters.text_sources import TextIngestionManager
from galgame_anki.config import AppConfig
from galgame_anki.core.anki_client import (
    AnkiCardPayload,
    AnkiConnectClient,
    launch_anki_background,
)
from galgame_anki.core.context_meaning import context_gloss_is_chinese, explain_lookup_in_sentence, format_contextual_meaning, rank_senses_by_context
from galgame_anki.core.dictionary import DictionaryEngine
from galgame_anki.core.verb_lesson import VerbLesson
from galgame_anki.core.grammar_engine import (
    OfflineGrammarEngine,
    build_web_ai_prompt,
    translate_sentence_free,
)
from galgame_anki.core.learning_progress import LearningProgressStore
from galgame_anki.core.phrase_matcher import build_phrase_token, find_phrase_for_token
from galgame_anki.core.sentence_bank import SentenceBankItem, SentenceBankScanner
from galgame_anki.core.sentence_store import SentenceRecord, SentenceStore
from galgame_anki.core.tokenizer import (
    JapaneseTokenizer,
    Token,
    highlight_target_in_sentence,
)
from galgame_anki.core.voice_pack import VoicePackManager
from galgame_anki.core.word_associations import WordAssociationEngine, WordAssociationTree
from galgame_anki.ui.blueprint_dialog import LearningBlueprintDialog
from galgame_anki.ui.card_forge_dialog import CardForgeDialog, ForgedCardData
from galgame_anki.ui.extra_dialogs import DifficultyGuideDialog, DictionaryManagerDialog
from galgame_anki.ui.hook_dialog import HookManagerDialog
from galgame_anki.ui.lookup_popup import LookupDetailPanel
from galgame_anki.ui.media_bridge_dialog import MediaBridgeDialog
from galgame_anki.ui.overlay_window import OverlayWindow
from galgame_anki.ui.sentence_bank_dialog import SentenceBankDialog
from galgame_anki.ui.sentence_widget import InteractiveSentenceWidget
from galgame_anki.ui.settings_dialog import SettingsDialog
from galgame_anki.ui.setup_wizard import SetupWizardDialog
from galgame_anki.ui.theme import build_stylesheet
from galgame_anki.ui.toast_widget import FloatingUndoToast
from galgame_anki.ui.voice_pack_dialog import VoicePackDialog
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

    preview_failed = Signal(str)

    def __init__(self, config: AppConfig | None = None, load_demo: bool = False) -> None:
        super().__init__()
        self.preview_failed.connect(self._report_preview_failure)
        self.setWindowTitle("GalAnki")
        self.resize(740, 500)
        self.setMinimumSize(600, 400)

        self.config = config or AppConfig.load()
        self.adapter = create_platform_adapter()
        self.dictionary = DictionaryEngine()
        self.tokenizer = JapaneseTokenizer(self.dictionary)
        self.grammar_engine = OfflineGrammarEngine()
        self.progress_store = LearningProgressStore()
        self.anki = AnkiConnectClient(self.config.anki_url)
        self.store = SentenceStore(max_history=50)
        self.voice_packs = VoicePackManager()
        self.word_associations = WordAssociationEngine(self.dictionary, self.voice_packs)
        self.sentence_bank_scanner = SentenceBankScanner(self.tokenizer, self.dictionary)
        self.ingestion = TextIngestionManager(self)
        self.hook_manager = GameHookManager(self)
        self.mpv_bridge = MpvSubtitleBridge(parent=self)
        self._hook_console_log: list[str] = []
        self.thread_pool = QThreadPool.globalInstance()
        self._active_workers: list[object] = []

        self._current_record: SentenceRecord | None = None
        self._selected_token: Token | None = None
        self._anki_connected: bool = False
        self._anki_launch_attempted: bool = False
        self._last_mined_was_known: dict[int, bool] = {}
        self._tray_available: bool = False
        self.tray_icon: QSystemTrayIcon | None = None
        self._force_quit: bool = False
        self._closing: bool = False

        self.overlay = OverlayWindow(
            theme_name=self.config.ui_theme,
            grammar_engine=self.grammar_engine,
            dictionary=self.dictionary,
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
            text_align=self.config.overlay_text_align,
        )
        self.overlay.mine_requested.connect(self._mine_record_token)
        self.overlay.copy_prompt_requested.connect(self._copy_web_ai_prompt_for_record)
        self.overlay.translate_requested.connect(self._translate_record_for_overlay)
        self.overlay.loop_requested.connect(self._loop_from_overlay)
        self.overlay.replay_requested.connect(lambda: self.mpv_bridge.step_subtitle(0))
        self.mpv_bridge.navigation_failed.connect(self.overlay.show_toast)
        self.mpv_bridge.loop_changed.connect(self.overlay.loop_action.setChecked)
        self.overlay.prev_requested.connect(self._step_history_prev)
        self.overlay.next_requested.connect(self._step_history_next)
        self.overlay.merge_prev_requested.connect(self._merge_previous_sentence)
        self.overlay.open_console_requested.connect(self.show_console_window)
        self.overlay.audio_preview_requested.connect(self._preview_current_audio)
        self.overlay.visibility_changed.connect(self._on_overlay_visibility_changed)
        self.overlay.replace_mode_changed.connect(self._on_overlay_replace_mode_changed)
        self.overlay.state_saved.connect(self._on_overlay_state_saved)

        self.undo_toast = FloatingUndoToast(theme_name=self.config.ui_theme)
        self.undo_toast.undo_requested.connect(self._undo_mined_note)

        self._build_ui()
        self._setup_system_tray()
        self._setup_shortcuts()
        self._connect_signals()
        self._apply_theme(self.config.ui_theme, save=False)
        self._apply_stage_ui()

        if load_demo or not self.store.latest():
            self._ingest_sentence(DEMO_SENTENCES[0][1], source="demo")

        # 啟動後自動連接 Anki（若未啟動且設定啟用，自動背景啟動並最小化，再同步熟詞與原生模板）
        QTimer.singleShot(250, self._background_sync_anki)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # === 極簡頂部控制列（單一筆電螢幕友善：只留 5 個核心入口，絕不堆疊重複按鈕） ===
        top_bar = QFrame()
        top_bar.setObjectName("TopBar")
        tl = QHBoxLayout(top_bar)
        tl.setContentsMargins(10, 7, 10, 7)
        tl.setSpacing(6)

        # 1. 來源選單（遊戲 / mpv）
        self.game_btn = QPushButton("來源")
        self.game_btn.setObjectName("PrimaryBtn")
        self.game_menu = QMenu(self)
        self.game_btn.setMenu(self.game_menu)
        self._rebuild_game_menu()
        tl.addWidget(self.game_btn)

        # 2. 切回只留覆蓋層模式
        self.overlay_btn = QPushButton("覆蓋層")
        self.overlay_btn.setCheckable(True)
        self.overlay_btn.setToolTip("顯示字幕覆蓋層並收起控制台，筆電螢幕留給遊戲／mpv")
        self.overlay_btn.clicked.connect(self._on_overlay_btn_clicked)
        tl.addWidget(self.overlay_btn)

        tl.addSpacing(2)

        # 3. 階段切換
        self.stage_group = QButtonGroup(self)
        self.stage_buttons: dict[str, QPushButton] = {}
        for mode_id, label, tip in [
            ("lv1", "Lv.1", "假名輔助 + 雙語"),
            ("lv2", "Lv.2", "1T 高亮 + 雙語"),
            ("lv3", "Lv.3", "日日單語優先"),
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

        self.budget_pill = QLabel(f"0/{self.config.daily_card_limit}")
        self.budget_pill.setObjectName("PillInfo")
        self.budget_pill.setToolTip("今日收錄 / 每日上限")
        tl.addWidget(self.budget_pill)

        # 可點擊的 Anki 狀態膠囊
        self.anki_pill = QPushButton("Anki…")
        self.anki_pill.setObjectName("PillWarn")
        self.anki_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self.anki_pill.setToolTip("點擊重新連線或在背景啟動 Anki")
        self.anki_pill.clicked.connect(self._on_anki_pill_clicked)
        tl.addWidget(self.anki_pill)

        # 4. 精簡工具選單
        self.more_btn = QPushButton("工具")
        self.more_btn.setObjectName("GhostBtn")
        self.more_menu = QMenu(self)
        self._build_more_menu()
        self.more_btn.setMenu(self.more_menu)
        tl.addWidget(self.more_btn)

        root.addWidget(top_bar)

        # === 主內容區 ===
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(10, 8, 10, 4)
        body_layout.setSpacing(8)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)

        left_col = QWidget()
        left_layout = QVBoxLayout(left_col)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        # --- 台詞卡片 ---
        sent_frame = QFrame()
        sent_frame.setObjectName("SentencePanel")
        sf = QVBoxLayout(sent_frame)
        sf.setContentsMargins(12, 8, 12, 8)
        sf.setSpacing(5)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(5)

        self.game_badge = QLabel(self.config.current_game_name or "等待來源")
        self.game_badge.setObjectName("PillInfo")
        meta_row.addWidget(self.game_badge)

        self.thread_combo = QComboBox()
        self.thread_combo.setMinimumWidth(120)
        self.thread_combo.setMaximumWidth(210)
        self.thread_combo.addItem("自動線程", "auto")
        self.thread_combo.setToolTip("切換遊戲 Hook 線程")
        self.thread_combo.currentIndexChanged.connect(self._on_hook_thread_changed)
        self.thread_combo.hide()
        meta_row.addWidget(self.thread_combo)

        meta_row.addStretch()

        self.sentence_badge = QLabel("")
        self.sentence_badge.setObjectName("PillOk")
        meta_row.addWidget(self.sentence_badge)

        self.mpv_replay_btn = QPushButton("重播")
        self.mpv_replay_btn.setObjectName("GhostBtn")
        self.mpv_replay_btn.setToolTip("重播目前 mpv 字幕片段")
        self.mpv_replay_btn.clicked.connect(self._replay_mpv_current)
        self.mpv_replay_btn.hide()
        meta_row.addWidget(self.mpv_replay_btn)

        self.mpv_speed_btn = QPushButton("倍速")
        self.mpv_speed_btn.setObjectName("GhostBtn")
        self.mpv_speed_btn.setCheckable(True)
        self.mpv_speed_btn.setChecked(self.config.mpv_auto_speedup)
        self.mpv_speed_btn.setToolTip("無字幕自動倍速")
        self.mpv_speed_btn.clicked.connect(self._toggle_mpv_auto_speedup)
        self.mpv_speed_btn.hide()
        meta_row.addWidget(self.mpv_speed_btn)

        self.merge_prev_btn = QPushButton("合併")
        self.merge_prev_btn.setObjectName("GhostBtn")
        self.merge_prev_btn.setToolTip("合併上一句台詞與原聲 (Ctrl+J)")
        self.merge_prev_btn.clicked.connect(self._merge_previous_sentence)
        meta_row.addWidget(self.merge_prev_btn)

        self.translate_btn = QPushButton("句意")
        self.translate_btn.setObjectName("GhostBtn")
        self.translate_btn.setToolTip("優先查看字幕附帶譯文。缺少時使用 Google 機器翻譯，結果僅供參考。")
        self.translate_btn.clicked.connect(self._check_current_sentence_translation)
        meta_row.addWidget(self.translate_btn)

        self.manual_toggle_btn = QPushButton("輸入")
        self.manual_toggle_btn.setObjectName("GhostBtn")
        self.manual_toggle_btn.setCheckable(True)
        self.manual_toggle_btn.setToolTip("顯示／隱藏手動輸入列")
        self.manual_toggle_btn.clicked.connect(lambda c: self.manual_input.setVisible(c))
        meta_row.addWidget(self.manual_toggle_btn)

        self.history_toggle_btn = QPushButton("歷史")
        self.history_toggle_btn.setObjectName("GhostBtn")
        self.history_toggle_btn.setCheckable(True)
        self.history_toggle_btn.setToolTip("展開／收合近期 50 句")
        self.history_toggle_btn.clicked.connect(self._toggle_history_drawer)
        meta_row.addWidget(self.history_toggle_btn)

        sf.addLayout(meta_row)

        self.sentence_widget = InteractiveSentenceWidget(font_size=21, theme_name=self.config.ui_theme)
        self.sentence_widget.token_clicked.connect(self._on_main_token_clicked)
        sf.addWidget(self.sentence_widget)

        self.translation_label = QLabel("")
        self.translation_label.setObjectName("MutedLabel")
        self.translation_label.setWordWrap(True)
        self.translation_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.translation_label.hide()
        sf.addWidget(self.translation_label)

        self.manual_input = QLineEdit()
        self.manual_input.setPlaceholderText("貼上日語句測試分詞與收錄（Enter 送出）…")
        self.manual_input.returnPressed.connect(self._submit_manual_input)
        self.manual_input.hide()
        sf.addWidget(self.manual_input)

        left_layout.addWidget(sent_frame)

        # --- 查詞詳情卡 ---
        self.lookup_panel = LookupDetailPanel(theme_name=self.config.ui_theme)
        self.lookup_panel.mine_requested.connect(self._on_panel_mine)
        self.lookup_panel.copy_prompt_requested.connect(self._on_panel_copy_prompt)
        self.lookup_panel.audio_preview_requested.connect(self._preview_current_audio)
        self.lookup_panel.blueprint_requested.connect(self.open_learning_blueprint)
        self.overlay.popup.lesson_requested.connect(self._open_verb_lesson)
        self.lookup_panel.lesson_requested.connect(self._open_verb_lesson)
        left_layout.addWidget(self.lookup_panel, 1)

        self.splitter.addWidget(left_col)

        # --- 右側近期歷史抽屜 ---
        self.history_drawer = QFrame()
        self.history_drawer.setObjectName("CardPanel")
        hd = QVBoxLayout(self.history_drawer)
        hd.setContentsMargins(10, 10, 10, 10)
        hd.setSpacing(6)

        hd_title = QLabel("近期台詞（50 句）")
        hd_title.setStyleSheet("font-weight: 700; background: transparent;")
        hd.addWidget(hd_title)

        self.history_list = QListWidget()
        self.history_list.currentRowChanged.connect(self._on_history_selected)
        hd.addWidget(self.history_list, 1)

        self.history_drawer.hide()
        self.splitter.addWidget(self.history_drawer)
        self.splitter.setSizes([560, 180])

        body_layout.addWidget(self.splitter, 1)
        root.addWidget(body, 1)

        status_bar = QStatusBar()
        self.setStatusBar(status_bar)
        self.statusBar().showMessage(
            "1. 來源接遊戲/mpv  →  2. 懸停或點詞查義  →  3. 左鍵直寫 Anki（Shift 編輯 · 覆蓋層常駐）"
        )

    def _rebuild_game_menu(self) -> None:
        self.game_menu.clear()

        act_launch = QAction("啟動遊戲...", self)
        act_launch.triggered.connect(self._launch_game_dialog)
        self.game_menu.addAction(act_launch)

        act_attach = QAction("掛載進行中遊戲", self)
        act_attach.triggered.connect(self._detect_and_attach_running_game)
        self.game_menu.addAction(act_attach)

        act_media = QAction("連接 mpv...", self)
        act_media.triggered.connect(self.open_media_bridge)
        self.game_menu.addAction(act_media)

        act_mgr = QAction("Hook 線程", self)
        act_mgr.triggered.connect(self.open_hook_manager)
        self.game_menu.addAction(act_mgr)

        if self.config.recent_games:
            self.game_menu.addSeparator()
            hdr = QAction("最近遊玩", self)
            hdr.setEnabled(False)
            self.game_menu.addAction(hdr)
            for item in self.config.recent_games[:5]:
                name = item.get("name", "")
                path = item.get("path", "")
                if name and path:
                    act = QAction(name, self)
                    act.triggered.connect(
                        lambda _c=False, p=path, n=name: self._launch_specific_game(Path(p), n)
                    )
                    self.game_menu.addAction(act)

        if hasattr(self, "_tray_menu"):
            self._rebuild_tray_menu()

    def _build_more_menu(self) -> None:
        self.more_menu.clear()

        # 學習
        act_bp = QAction("語法技能樹\tCtrl+B", self)
        act_bp.triggered.connect(self.open_learning_blueprint)
        self.more_menu.addAction(act_bp)

        act_bank = QAction("1T 句庫挖礦\tCtrl+Shift+M", self)
        act_bank.triggered.connect(self.open_sentence_bank)
        self.more_menu.addAction(act_bank)

        self.more_menu.addSeparator()

        # 音訊
        act_voice = QAction("純人聲解包", self)
        act_voice.triggered.connect(lambda: self.open_voice_pack_manager(initial_tab=0))
        self.more_menu.addAction(act_voice)

        act_cond = QAction("角色聽力匯出", self)
        act_cond.triggered.connect(lambda: self.open_voice_pack_manager(initial_tab=1))
        self.more_menu.addAction(act_cond)

        self.more_menu.addSeparator()

        # 系統
        act_dict = QAction("詞典管理", self)
        act_dict.triggered.connect(self.open_dictionary_manager)
        self.more_menu.addAction(act_dict)

        act_wiz = QAction("快速設定", self)
        act_wiz.triggered.connect(self.open_setup_wizard)
        self.more_menu.addAction(act_wiz)

        self.act_theme_toggle = QAction(
            "切換深色主題" if self.config.ui_theme == "sakura" else "切換淺色主題",
            self,
        )
        self.act_theme_toggle.triggered.connect(self._toggle_theme)
        self.more_menu.addAction(self.act_theme_toggle)

        self.act_pause = QAction("暫停監聽", self)
        self.act_pause.setCheckable(True)
        self.act_pause.triggered.connect(self._toggle_listening)
        self.more_menu.addAction(self.act_pause)

        act_cfg = QAction("設定", self)
        act_cfg.triggered.connect(self.open_settings)
        self.more_menu.addAction(act_cfg)

        self.more_menu.addSeparator()

        act_quit = QAction("退出\tCtrl+Q", self)
        act_quit.triggered.connect(self.quit_application)
        self.more_menu.addAction(act_quit)

    def _setup_system_tray(self) -> None:
        """建立系統匣圖示（單螢幕隱形伴讀核心入口；無系統匣環境安全略過）。"""
        import os

        if os.environ.get("QT_QPA_PLATFORM", "").lower() in ("offscreen", "minimal"):
            self._tray_available = False
            return
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._tray_available = False
            return

        icon_path = Path(__file__).resolve().parent.parent / "data" / "galanki.svg"
        icon = QIcon(str(icon_path)) if icon_path.exists() else self.windowIcon()
        self.tray_icon = QSystemTrayIcon(icon, self)
        self.tray_icon.setToolTip("GalAnki（左鍵開關控制台，右鍵選單）")
        self._tray_menu = QMenu(self)
        self._rebuild_tray_menu()
        self.tray_icon.setContextMenu(self._tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()
        self._tray_available = True

    def _rebuild_tray_menu(self) -> None:
        if not hasattr(self, "_tray_menu"):
            return
        menu = self._tray_menu
        menu.clear()

        self._tray_overlay_act = QAction("顯示覆蓋層", self)
        self._tray_overlay_act.setCheckable(True)
        self._tray_overlay_act.setChecked(self.overlay.isVisible())
        self._tray_overlay_act.triggered.connect(self._toggle_overlay)
        menu.addAction(self._tray_overlay_act)

        act_console = QAction("主控制台", self)
        act_console.triggered.connect(self._toggle_console_visibility)
        menu.addAction(act_console)

        menu.addSeparator()

        act_launch = QAction("啟動遊戲...", self)
        act_launch.triggered.connect(self._launch_game_dialog)
        menu.addAction(act_launch)

        act_attach = QAction("掛載遊戲", self)
        act_attach.triggered.connect(self._detect_and_attach_running_game)
        menu.addAction(act_attach)

        act_mpv = QAction("連接 mpv...", self)
        act_mpv.triggered.connect(self.open_media_bridge)
        menu.addAction(act_mpv)

        if self.config.recent_games:
            recent_sub = menu.addMenu("最近遊玩")
            for item in self.config.recent_games[:5]:
                name = item.get("name", "")
                path = item.get("path", "")
                if name and path:
                    a = QAction(name, self)
                    a.triggered.connect(
                        lambda _c=False, p=path, n=name: self._launch_specific_game(Path(p), n)
                    )
                    recent_sub.addAction(a)

        menu.addSeparator()

        tools_sub = menu.addMenu("工具")
        for label, cb in [
            ("語法技能樹", self.open_learning_blueprint),
            ("1T 句庫挖礦", self.open_sentence_bank),
            ("純人聲解包", lambda: self.open_voice_pack_manager(initial_tab=0)),
            ("角色聽力匯出", lambda: self.open_voice_pack_manager(initial_tab=1)),
            ("詞典管理", self.open_dictionary_manager),
            ("設定", self.open_settings),
        ]:
            act = QAction(label, self)
            act.triggered.connect(cb)
            tools_sub.addAction(act)

        stage_sub = menu.addMenu(f"階段（{self.config.stage_mode.upper()}）")
        for mode_id, lbl in [
            ("lv1", "Lv.1 假名"),
            ("lv2", "Lv.2 雙語"),
            ("lv3", "Lv.3 單語"),
        ]:
            a = QAction(lbl, self)
            a.setCheckable(True)
            a.setChecked(self.config.stage_mode == mode_id)
            a.triggered.connect(lambda _c=False, m=mode_id: self._set_stage_mode(m))
            stage_sub.addAction(a)

        anki_txt = "Anki 已連" if self._anki_connected else "啟動 Anki"
        act_anki = QAction(anki_txt, self)
        act_anki.triggered.connect(self._on_anki_pill_clicked)
        menu.addAction(act_anki)

        menu.addSeparator()

        act_quit = QAction("退出", self)
        act_quit.triggered.connect(self.quit_application)
        menu.addAction(act_quit)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._toggle_console_visibility()

    def _toggle_console_visibility(self) -> None:
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.show_console_window()

    def show_console_window(self) -> None:
        """從系統匣或覆蓋層喚出主控制台。"""
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def show_overlay_only_mode(self) -> None:
        """單螢幕隱形伴讀模式：顯示字幕覆蓋層並把主視窗收進系統匣。"""
        self._toggle_overlay(True)
        if self._tray_available:
            self.hide()

    def _on_overlay_btn_clicked(self, checked: bool) -> None:
        if checked:
            self._toggle_overlay(True)
            if self._tray_available and self.config.start_in_tray_overlay:
                self.hide()
                self.undo_toast.show_info("已切換覆蓋層模式（點系統匣圖示可開控制台）", 3000)
        else:
            self._toggle_overlay(False)

    def quit_application(self) -> None:
        """真正退出整個應用程式（清理 KWin 置頂、Hook、mpv 與資料庫連線）。"""
        self._force_quit = True
        self.close()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _setup_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self._shortcut_mine)
        QShortcut(QKeySequence("Ctrl+Shift+Return"), self, activated=self._shortcut_mine_forge)
        QShortcut(QKeySequence("Ctrl+M"), self, activated=self._shortcut_mine)
        QShortcut(QKeySequence("Ctrl+P"), self, activated=self._preview_current_audio)
        QShortcut(QKeySequence("Ctrl+J"), self, activated=self._merge_previous_sentence)
        QShortcut(QKeySequence("Ctrl+Shift+M"), self, activated=self.open_sentence_bank)
        QShortcut(QKeySequence("Ctrl+B"), self, activated=self.open_learning_blueprint)
        QShortcut(QKeySequence("Ctrl+Q"), self, activated=self.quit_application)
        QShortcut(QKeySequence("Ctrl+1"), self, activated=lambda: self._set_stage_mode("lv1"))
        QShortcut(QKeySequence("Ctrl+2"), self, activated=lambda: self._set_stage_mode("lv2"))
        QShortcut(QKeySequence("Ctrl+3"), self, activated=lambda: self._set_stage_mode("lv3"))

    def _shortcut_mine(self) -> None:
        tok = self.lookup_panel._current_token or self._selected_token
        if self._current_record and tok:
            self._mine_record_token(self._current_record, tok)

    def _shortcut_mine_forge(self) -> None:
        tok = self.lookup_panel._current_token or self._selected_token
        if self._current_record and tok:
            self._mine_record_token(self._current_record, tok, force_forge=True)

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

        self.mpv_bridge.configure_immersion(
            auto_speedup=self.config.mpv_auto_speedup,
            fast_speed=self.config.mpv_fast_speed,
            auto_pause_sub_end=self.config.mpv_auto_pause_sub_end,
        )
        self.mpv_bridge.subtitle_received.connect(self._on_mpv_subtitle)
        self.mpv_bridge.connection_changed.connect(self._on_mpv_state_changed)
        self.mpv_bridge.start()

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
        if hasattr(self, "act_theme_toggle"):
            self.act_theme_toggle.setText(
                "切換深色主題" if theme_name == "sakura" else "切換淺色主題"
            )
        self.sentence_widget.set_theme_name(theme_name)
        self.lookup_panel.set_theme_name(theme_name)
        self.overlay.apply_theme(theme_name)
        self.undo_toast.apply_theme(theme_name)

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
        self._rebuild_tray_menu()
        names = {
            "lv1": "Lv.1 五十音破冰（精準假名提示 + 雙語）",
            "lv2": "Lv.2 精細沉浸（1T 高亮 + 雙語）",
            "lv3": "Lv.3 登樓撤梯（日日單語優先）",
        }
        self.statusBar().showMessage(f"已切換階段：{names.get(mode, mode)}", 3500)

    def _toggle_history_drawer(self, checked: bool) -> None:
        self.history_drawer.setVisible(checked)

    # === 遊戲純淨人聲抽取、mpv 無損切片與試聽 ===
    def _current_game_exe_path(self) -> str:
        target = self.hook_manager.attached_target
        if target is not None and target.exe_path:
            return str(target.exe_path)
        if self.config.current_game_name and self.config.recent_games:
            for item in self.config.recent_games:
                if item.get("name") == self.config.current_game_name and item.get("path"):
                    return str(item["path"])
        return ""

    def _resolve_voice_pack_id(self, rec: SentenceRecord | None = None) -> str | None:
        if self.config.active_voice_pack_id:
            return self.config.active_voice_pack_id
        game_name = (rec.media_title if rec else "") or self.config.current_game_name
        matched = self.voice_packs.resolve_pack_for_game(
            game_name=game_name,
            exe_path=self._current_game_exe_path(),
        )
        return matched.pack_id if matched is not None else None

    def _prefetch_voice_pack_in_background(self, rec: SentenceRecord) -> None:
        if rec.source == "mpv":
            return
        pack_id = self._resolve_voice_pack_id(rec)
        if not pack_id:
            return
        snapshot_text = rec.raw_text

        def _match() -> None:
            res = self.voice_packs.match_and_extract_voice(
                text=snapshot_text,
                pack_id=pack_id,
                game_name=rec.media_title or self.config.current_game_name,
                exe_path=self._current_game_exe_path(),
            )
            if not res.matched_line or rec.raw_text != snapshot_text:
                return
            if res.translation and not rec.pack_translation:
                rec.pack_translation = res.translation
            if res.has_voice and res.audio_clip is not None:
                rec.audio_clip = res.audio_clip
                rec.audio_source = "voice_pack"
            elif rec.audio_source == "":
                rec.audio_source = "silent"

        worker = FunctionWorker(_match)
        self._active_workers.append(worker)
        self.thread_pool.start(worker)

    def _extract_audio_for_record(self, rec: SentenceRecord) -> tuple[bytes, str] | None:
        # 1. 若為 mpv 本地影片且帶有準確字幕時間軸，優先由 FFmpeg 無損直接裁切原聲！
        if rec.video_path and rec.sub_start is not None and rec.sub_end is not None:
            if rec.audio_clip is not None:
                return rec.audio_clip
            mpv_clip = extract_video_audio_mp3(
                audio_stream_index=rec.audio_stream_index,
                video_path=rec.external_audio_path or rec.video_path,
                start_sec=rec.sub_start,
                end_sec=rec.sub_end,
            )
            if mpv_clip is not None:
                rec.audio_clip = mpv_clip
                rec.audio_source = "mpv"
                return mpv_clip

        # 2. 已取得純淨原包人聲／mpv 原聲時直接重用
        if rec.audio_clip is not None:
            return rec.audio_clip

        if rec.source == "mpv":
            return None

        # 3. 已索引遊戲本體／解包「台詞 ↔ 純淨聲優人聲」（0% BGM・0% 音效）
        if rec.audio_source == "silent":
            return None
        pack_id = self._resolve_voice_pack_id(rec)
        if pack_id:
            res = self.voice_packs.match_and_extract_voice(
                text=rec.raw_text,
                pack_id=pack_id,
                game_name=rec.media_title or self.config.current_game_name,
                exe_path=self._current_game_exe_path(),
            )
            if res.matched_line:
                if res.translation and not rec.pack_translation:
                    rec.pack_translation = res.translation
                if res.has_voice and res.audio_clip is not None:
                    rec.audio_clip = res.audio_clip
                    rec.audio_source = "voice_pack"
                    return res.audio_clip
                rec.audio_source = "silent"
                return None
        return None

    def _report_preview_failure(self, message: str) -> None:
        self.overlay.show_toast(message)
        self.statusBar().showMessage(message, 5000)

    def _preview_current_audio(self) -> None:
        if self._current_record is None:
            return
        rec = self._current_record
        self.statusBar().showMessage("🔊 正在擷取本句原聲語音...")

        def _do() -> tuple[bytes, str] | None:
            return self._extract_audio_for_record(rec)

        worker = FunctionWorker(_do)
        worker.signals.finished.connect(lambda clip: self._on_audio_preview_ready(clip, rec))
        worker.signals.failed.connect(
            lambda err: self.preview_failed.emit(f"語音試聽失敗：{err}")
        )
        self._active_workers.append(worker)
        self.thread_pool.start(worker)

    def _on_audio_preview_ready(self, clip: tuple[bytes, str] | None, rec: SentenceRecord) -> None:
        if not clip and rec.source == "mpv":
            message = "試聽失敗：缺少字幕起訖時間" if None in (rec.sub_start, rec.sub_end) else "試聽失敗：FFmpeg 無法讀取影片或所選音軌"
            self._report_preview_failure(message)
            return
        if not clip:
            if rec.audio_source == "silent":
                self.statusBar().showMessage("🔇 劇本確認本句為旁白／無配音角色。", 4500)
            else:
                self.statusBar().showMessage(
                    "找不到此句純淨人聲（可至「🎮 遊戲/影片 → 📦 解包遊戲提取純人聲」建立遊戲索引）。",
                    5000,
                )
            return
        mp3_bytes, name = clip
        play_mp3_bytes_async(mp3_bytes, self.preview_failed.emit)
        src_hint = "純淨原包人聲" if rec.audio_source == "voice_pack" else "原聲"
        self.statusBar().showMessage(f"🔊 正在試聽{src_hint}（{len(mp3_bytes) / 1024:.1f} KB · {name}）", 3500)

    # === Anki 同步與製卡 ===
    def _update_budget_pill(self) -> None:
        count = self.store.today_mined_count
        limit = self.config.daily_card_limit
        self.budget_pill.setText(f"{count}/{limit}")
        self.budget_pill.setObjectName("PillWarn" if count >= limit else "PillInfo")
        self.budget_pill.style().unpolish(self.budget_pill)
        self.budget_pill.style().polish(self.budget_pill)

    def _on_anki_pill_clicked(self) -> None:
        if self._anki_connected:
            self.statusBar().showMessage(
                f"✓ Anki 已連線，已同步 {len(self.store.known_words)} 個已知詞彙",
                3000,
            )
            return
        self._anki_launch_attempted = False
        self.anki_pill.setText("啟動 Anki…")
        self.statusBar().showMessage("🚀 正在背景啟動 Anki 並自動最小化…", 5000)
        self._background_sync_anki(allow_auto_launch=True)

    def _background_sync_anki(self, allow_auto_launch: bool = True) -> None:
        def _sync() -> dict:
            launched_msg = ""
            try:
                ver = self.anki.ping()
            except Exception:
                if allow_auto_launch and self.config.auto_launch_anki and not self._anki_launch_attempted:
                    self._anki_launch_attempted = True
                    ok, msg = launch_anki_background(minimize_on_wayland=True)
                    if ok:
                        launched_msg = msg
                        ver = None
                        for _ in range(15):
                            if self._closing:
                                break
                            time.sleep(1.2)
                            try:
                                ver = self.anki.ping()
                                break
                            except Exception:
                                continue
                        if ver is None:
                            raise RuntimeError("背景啟動 Anki 逾時，請確認已安裝 AnkiConnect (2055492159)")
                    else:
                        raise RuntimeError(msg)
                else:
                    raise

            self.anki.ensure_deck(self.config.deck_name)
            self.anki.ensure_note_type(self.config.note_type)
            known = (
                self.anki.fetch_known_words(self.config.deck_name)
                if self.config.auto_sync_known_words
                else set()
            )
            today_count = self.anki.get_today_mined_count(self.config.deck_name)
            return {
                "version": ver,
                "known": known,
                "today": today_count,
                "launched_msg": launched_msg,
            }

        worker = FunctionWorker(_sync)
        worker.signals.finished.connect(self._on_anki_sync_ok)
        worker.signals.failed.connect(self._on_anki_sync_fail)
        self._active_workers.append(worker)
        self.thread_pool.start(worker)

    def _on_anki_sync_ok(self, info: dict) -> None:
        if self._closing:
            return
        self._anki_connected = True
        self.store.known_words.update(info["known"])
        self.store.today_mined_count = int(info["today"])
        self._update_budget_pill()

        self.anki_pill.setText("Anki 已連")
        self.anki_pill.setToolTip(f"已連線 AnkiConnect，已同步 {len(self.store.known_words)} 個學習詞彙")
        self.anki_pill.setObjectName("PillOk")
        self.anki_pill.style().unpolish(self.anki_pill)
        self.anki_pill.style().polish(self.anki_pill)
        self._rebuild_tray_menu()
        if info.get("launched_msg"):
            self.statusBar().showMessage(info["launched_msg"], 5000)

        if self._current_record is not None:
            self._retokenize_current()

    def _on_anki_sync_fail(self, _err: str) -> None:
        self._anki_connected = False
        self.anki_pill.setText("啟動 Anki")
        self.anki_pill.setToolTip("點擊於背景啟動 Anki 並自動最小化")
        self.anki_pill.setObjectName("PillWarn")
        self.anki_pill.style().unpolish(self.anki_pill)
        self.anki_pill.style().polish(self.anki_pill)
        self._rebuild_tray_menu()

    # === 多來源文本進流（Galgame Hook / mpv 影片 / OCR / 剪貼簿） ===
    def _on_ingestion_sentence_meta(self, text: str, source: str, media_title: str) -> None:
        self._ingest_sentence(text, source=source, media_title=media_title)

    def _on_mpv_state_changed(self, connected: bool, msg: str) -> None:
        self.overlay.set_mpv_connected(connected)
        self.mpv_replay_btn.setVisible(connected)
        self.mpv_speed_btn.setVisible(connected)
        if connected:
            self.game_badge.setText(msg)
            self.game_badge.setObjectName("PillOk")
            self.game_badge.style().unpolish(self.game_badge)
            self.game_badge.style().polish(self.game_badge)
            # mpv 連線時原字幕會自動隱藏，覆蓋層自動切為全透明電影字幕（不遮畫面）
            self.overlay.set_backdrop("transparent", save=False)
            if self.overlay.isVisible():
                self.mpv_bridge.set_native_subtitle_visible(False)
        else:
            if self.overlay._backdrop == "transparent" and self.config.overlay_backdrop != "transparent":
                self.overlay.set_backdrop(self.config.overlay_backdrop, save=False)
        self.statusBar().showMessage(msg, 4500)

    def _toggle_mpv_auto_speedup(self, checked: bool) -> None:
        self.config.mpv_auto_speedup = checked
        self.config.save()
        self.mpv_bridge.configure_immersion(
            auto_speedup=self.config.mpv_auto_speedup,
            fast_speed=self.config.mpv_fast_speed,
            auto_pause_sub_end=self.config.mpv_auto_pause_sub_end,
        )
        self.statusBar().showMessage(
            f"⚡ 已開啟無字幕自動 {self.config.mpv_fast_speed:.1f}x 快進（sub-transition）"
            if checked
            else "已關閉無字幕自動倍速，恢復 1.0x 原速播放",
            4000,
        )

    def _replay_mpv_current(self) -> None:
        sub_start = self._current_record.sub_start if self._current_record else None
        if self.mpv_bridge.replay_current_subtitle(sub_start=sub_start):
            self.statusBar().showMessage("🔁 已跳回本句字幕開頭重播", 2500)
        else:
            self.statusBar().showMessage("目前 mpv 尚未記錄字幕時間軸。", 3000)

    def _merge_previous_sentence(self) -> None:
        cur = self._current_record or self.store.latest()
        if cur is None:
            return
        prev = self.store.peek_previous_record(cur.record_id)
        if prev is None:
            self.statusBar().showMessage("目前沒有上一句可合併。", 3000)
            return

        sep = "　" if prev.raw_text.rstrip().endswith(("。", "！", "？", "!", "?", "」", "』")) else ""
        merged_text = prev.raw_text.rstrip() + sep + cur.raw_text.lstrip()
        tokenized = self.tokenizer.tokenize(merged_text, self.store.known_words)
        analysis = self.grammar_engine.analyze(tokenized)
        merged = self.store.merge_previous_into_record(cur.record_id, tokenized, grammar_analysis=analysis)
        if merged is None:
            return

        self._refresh_history_list(select_record_id=merged.record_id)
        self._display_record(merged)
        if self.overlay.isVisible():
            self.overlay.show_record(merged)
            self.overlay.show_toast("➕ 已合併上一句（時間軸原聲同步合併）")
        self.statusBar().showMessage("➕ 已將上一句與本句合併為完整台詞（製卡時將切出合併後完整原聲）", 4500)

    def _on_mpv_subtitle(self, ev: MpvSubtitleEvent) -> None:
        if self._closing or self.ingestion.paused:
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
            mpv_event=ev,
        )

    def _ingest_sentence(
        self,
        text: str,
        source: str,
        media_title: str = "",
        video_path: str = "",
        sub_start: float | None = None,
        sub_end: float | None = None,
        mpv_event: MpvSubtitleEvent | None = None,
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
        if mpv_event is not None:
            rec.audio_stream_index = mpv_event.audio_stream_index
            rec.external_audio_path = mpv_event.external_audio_path
            if mpv_event.chinese_subtitle:
                rec.pack_translation = mpv_event.chinese_subtitle
        # 1. 若已索引遊戲純淨人聲包，新台詞到達時即刻背景抽出 0% BGM 聲優原聲並推進劇情游標
        self._prefetch_voice_pack_in_background(rec)

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
        if rec.pack_translation:
            rec.sentence_translation = rec.pack_translation
        if rec.sentence_translation:
            self.translation_label.setText(f"🌐 語境理解：{rec.sentence_translation}")
            self.translation_label.setVisible(not self.translation_label.isVisible())
            return

        self.translate_btn.setEnabled(False)
        self.translate_btn.setText("核對中…")
        original = rec.raw_text
        worker = FunctionWorker(translate_sentence_free, original)
        worker.signals.finished.connect(lambda tr: self._on_sentence_trans_ok(rec, tr, original))
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
        self.config.overlay_text_align = str(state.get("text_align", self.config.overlay_text_align))
        self.config.overlay_guide_dismissed = bool(state.get("guide_dismissed", False))
        self.config.save()

    def _on_overlay_replace_mode_changed(self, replace_mode: bool) -> None:
        self.config.overlay_replace_mode = replace_mode
        if self.mpv_bridge.is_connected and self.overlay.isVisible():
            self.mpv_bridge.set_native_subtitle_visible(not replace_mode)

    def _translate_record_for_overlay(self, rec: SentenceRecord) -> None:
        if rec.pack_translation:
            self._on_sentence_trans_ok(rec, rec.pack_translation)
            return
        original = rec.raw_text
        worker = FunctionWorker(translate_sentence_free, original)
        worker.signals.finished.connect(lambda tr: self._on_sentence_trans_ok(rec, tr, original))
        worker.signals.failed.connect(self._on_sentence_trans_fail)
        self._active_workers.append(worker)
        self.thread_pool.start(worker)

    def _on_sentence_trans_ok(self, rec: SentenceRecord, trans: str, original: str | None = None) -> None:
        self.translate_btn.setEnabled(True)
        self.translate_btn.setText("句意")
        if original is not None and original != rec.raw_text:
            return
        trans = rec.pack_translation or trans
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
        self.translate_btn.setText("句意")
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

        self._prefetch_voice_pack_in_background(rec)
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
            self.translation_label.setText(f"譯文：{rec.sentence_translation}")
            self.translation_label.show()
        else:
            self.translation_label.hide()

        hit_cnt = len(rec.grammar_analysis.grammar_hits)
        grammar_suffix = f" · 句型 {hit_cnt}" if hit_cnt else ""
        if rec.tokenized.is_1t_sentence:
            self.sentence_badge.setText(f"1T{grammar_suffix}")
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

    def _loop_from_overlay(self, enabled: bool) -> None:
        if not enabled:
            self.mpv_bridge.stop_sentence_loop()
            return
        rec = self._current_record
        if (not rec or rec.source != "mpv" or rec.sub_start is None or rec.sub_end is None
                or not self.mpv_bridge.loop_subtitle(rec.sub_start, rec.sub_end)):
            self.overlay.loop_action.setChecked(False)
            self.overlay.show_toast("請先播放一條有起訖時間的字幕。")

    def _overlay_uses_mpv(self) -> bool:
        return self.mpv_bridge.is_connected and (
            self._current_record is None or self._current_record.source in ("demo", "mpv"))

    def _step_history_prev(self) -> None:
        if self._overlay_uses_mpv():
            self.mpv_bridge.step_subtitle(-1)
            return
        row = self.history_list.currentRow()
        if row + 1 < self.history_list.count():
            self.history_list.setCurrentRow(row + 1)
        elif self.overlay.isVisible():
            self.overlay.show_toast("已經是最早一句歷史", 1400)

    def _step_history_next(self) -> None:
        if self._overlay_uses_mpv():
            self.mpv_bridge.step_subtitle(1)
            return
        row = self.history_list.currentRow()
        if row > 0:
            self.history_list.setCurrentRow(row - 1)
        elif self.overlay.isVisible():
            self.overlay.show_toast("已經是最新一句", 1400)

    def _on_main_token_clicked(self, token: Token, _anchor: QPoint) -> None:
        self._selected_token = token
        self._show_token_in_panel(token)

    def _show_token_in_panel(self, token: Token) -> None:
        self._selected_token = token
        record = self._current_record
        phrase_tok: Token | None = None
        if record is not None:
            pm = find_phrase_for_token(record.raw_text, record.tokenized.tokens, token, self.dictionary)
            if pm is not None:
                phrase_tok = build_phrase_token(pm)
        active_tok = phrase_tok if phrase_tok is not None else token
        already_known = active_tok.lemma in self.store.known_words or active_tok.surface in self.store.known_words
        particle_info = self.grammar_engine.get_particle_info(token.surface)
        self.lookup_panel.show_token(
            token=token,
            dict_preference=self.config.effective_dict_preference(),
            already_known=already_known,
            particle_info=particle_info,
            sentence=record.raw_text if record else "",
            translation=(record.pack_translation or record.sentence_translation) if record else "",
            phrase_token=phrase_tok,
        )
        # 離線即時展開「🌳 聯想記憶樹」（同漢字詞族・常用複合・自他/同音易混・本作品重現）
        req_id = getattr(self, "_assoc_req_id", 0) + 1
        self._assoc_req_id = req_id
        self.lookup_panel.set_word_associations(None)
        match = active_tok.lookup
        lemma = match.lemma if match else active_tok.lemma
        reading = match.reading if match else active_tok.reading
        if (particle_info is not None and phrase_tok is None) or not lemma:
            return
        cur_sent = self._current_record.raw_text if self._current_record else ""
        pack_id = self._resolve_voice_pack_id(self._current_record) or ""
        known_snap = set(self.store.known_words)

        def _build() -> WordAssociationTree:
            return self.word_associations.build_tree(
                lemma=lemma,
                reading=reading,
                known_words=known_snap,
                current_sentence=cur_sent,
                active_pack_id=pack_id,
            )

        worker = FunctionWorker(_build)
        worker.signals.finished.connect(
            lambda tree, rid=req_id: self.lookup_panel.set_word_associations(tree)
            if (not self._closing and getattr(self, "_assoc_req_id", 0) == rid)
            else None
        )
        self._active_workers.append(worker)
        self.thread_pool.start(worker)

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

    def _mine_record_token(
        self,
        record: SentenceRecord,
        token: Token,
        force_forge: bool | None = None,
    ) -> None:
        """預設一鍵直寫精煉記憶卡（自動挑最相關釋義＋原聲＋截圖＋聯想樹，右下角可撤銷）；按住 Shift 點擊才彈出編輯窗。"""
        if self.store.today_mined_count >= self.config.daily_card_limit:
            self.statusBar().showMessage(
                f"💡 今日已達建議上限（{self.config.daily_card_limit} 張），適時切換無阻沉浸享受劇情防爆倉。",
                5000,
            )

        match = token.lookup
        lemma = match.lemma if match else token.lemma
        reading = match.reading if match else token.reading
        pitch = "/".join(match.pitches[:3]) if (match and match.pitches) else ""
        by_lang = match.senses_by_language(max_per_lang=5, convert_tw=True) if match else {"zh": [], "ja": [], "en": []}
        raw_bi_senses = (
            [s.text for s in by_lang["zh"]]
            or [s.text for s in by_lang["en"]]
            or (match.concise_senses("bilingual", max_senses=5, strict=True) if match else [])
        )
        mono_senses = [s.text for s in by_lang["ja"]] or (
            match.concise_senses("monolingual", max_senses=4, strict=True) if match else []
        )
        effective_trans = record.sentence_translation or record.pack_translation
        bi_senses = rank_senses_by_context(
            raw_bi_senses,
            sentence_translation=effective_trans,
            raw_sentence=record.raw_text,
            target_surface=token.surface,
        )
        base_bi = bi_senses[0] if bi_senses else (match.best_concise_meaning("bilingual", strict=True) if match else "")
        contextual_gloss, _ = explain_lookup_in_sentence(token, record.raw_text, effective_trans)
        default_bi = contextual_gloss or (
            format_contextual_meaning(token.surface, lemma, base_bi, match.reasons)
            if (match and base_bi)
            else base_bi
        )
        # 若無可信繁中義項但有日英／日日備援，清楚標註備援語言供收錄或 Shift 精煉確認
        if default_bi and not by_lang["zh"] and not context_gloss_is_chinese(default_bi):
            if by_lang["en"]:
                default_bi = f"（日英備援）{default_bi}"
        elif not default_bi and by_lang["ja"]:
            default_bi = f"（日日備援）{by_lang['ja'][0].text}"
        default_mono = mono_senses[0] if mono_senses else (match.best_concise_meaning("monolingual", strict=True) if match else "")

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
            p_short = contextual_gloss if token.surface == "とか" and contextual_gloss else f"【助詞·{particle.role}】{particle.meaning}"
            bi_senses = [p_short] + [s for s in bi_senses if s != p_short]
            default_bi = p_short
            alt_lemmas = []

        highlighted_sentence = highlight_target_in_sentence(record.raw_text, token.start, token.end)
        analysis = record.grammar_analysis or self.grammar_engine.analyze(record.tokenized)
        source_name = record.media_title or self.config.current_game_name or record.source

        # 先取出音訊、畫面快照與聯想記憶樹供精煉確認或直寫
        audio_clip = self._extract_audio_for_record(record)
        shot_clip = record.screenshot_clip
        include_audio_default = self.config.capture_audio_on_mine and (audio_clip is not None)

        assoc_html = ""
        assoc_preview = ""
        if particle is None and lemma:
            assoc_tree = self.word_associations.build_tree(
                lemma=lemma,
                reading=reading,
                known_words=self.store.known_words,
                current_sentence=record.raw_text,
                active_pack_id=self._resolve_voice_pack_id(record) or "",
            )
            if not assoc_tree.is_empty():
                assoc_html = assoc_tree.to_anki_html()
                assoc_preview = assoc_tree.to_compact_preview_text()

        shift_held = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)
        should_open_forge = (
            force_forge
            if force_forge is not None
            else (self.config.quick_forge_before_mine or shift_held)
        )

        if should_open_forge:
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
                include_audio_default=include_audio_default,
                include_screenshot_default=self.config.capture_screenshot_on_mine or (shot_clip is not None),
                thread_pool=self.thread_pool,
                capture_shot_fn=self.adapter.capture_screenshot_webp,
                alt_lemmas=alt_lemmas,
                audio_source=record.audio_source,
                pack_translation=record.pack_translation,
                related_words_html=assoc_html,
                related_words_preview=assoc_preview,
                parent=self if self.isVisible() else None,
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
                sentence_translation=record.sentence_translation or record.pack_translation,
                include_audio=include_audio_default,
                audio_clip=audio_clip if include_audio_default else None,
                include_screenshot=self.config.capture_screenshot_on_mine,
                screenshot_clip=shot_clip if self.config.capture_screenshot_on_mine else None,
                related_words_html=assoc_html,
            )

        target_final = forged.target_word or lemma
        grammar_html = analysis.to_compact_html(accent_color="#d9468f", muted_color="#7a7a8c", focus_word=target_final)
        was_known_before = target_final in self.store.known_words

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
                target_word=target_final,
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
                related_words_html=forged.related_words_html,
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
                "lemma": target_final,
                "was_known_before": was_known_before,
                "has_audio": bool(audio_bytes),
                "has_shot": bool(screenshot_bytes),
            }

        self.statusBar().showMessage(f"✨ 正在寫入精煉記憶卡「{target_final}」至 Anki...")
        worker = FunctionWorker(_do_mine)
        worker.signals.finished.connect(self._on_mine_success)
        worker.signals.failed.connect(self._on_mine_failed)
        self._active_workers.append(worker)
        self.thread_pool.start(worker)

    def _on_mine_success(self, res: dict) -> None:
        if self._closing:
            return
        rec: SentenceRecord = res["record"]
        lemma: str = res["lemma"]
        note_id: int = int(res.get("note_id") or 0)
        if note_id > 0:
            self._last_mined_was_known[note_id] = bool(res.get("was_known_before", False))
            if len(self._last_mined_was_known) > 30:
                oldest = next(iter(self._last_mined_was_known))
                self._last_mined_was_known.pop(oldest, None)

        badges: list[str] = []
        if res.get("has_audio"):
            badges.append("原聲")
        if res.get("has_shot"):
            badges.append("畫面")
        media_tag = f"（{'＋'.join(badges)}）" if badges else ""

        self.store.mark_mined(rec, lemma)
        self._update_budget_pill()
        self._refresh_history_list(select_record_id=rec.record_id)
        if self._current_record is not None:
            self._retokenize_current()

        msg = f"已收錄「{lemma}」{media_tag} · {self.store.today_mined_count}/{self.config.daily_card_limit}"
        self.statusBar().showMessage(msg, 5500)
        self.undo_toast.show_mined(msg, note_id=note_id, lemma=lemma, duration_ms=6000)

    def _undo_mined_note(self, note_id: int, lemma: str) -> None:
        """一鍵撤銷剛剛直寫入 Anki 的卡片並還原今日計數與熟詞狀態。"""
        def _do_undo() -> dict:
            self.anki.delete_notes([note_id])
            return {"note_id": note_id, "lemma": lemma}

        worker = FunctionWorker(_do_undo)
        worker.signals.finished.connect(self._on_undo_success)
        worker.signals.failed.connect(self._on_undo_failed)
        self._active_workers.append(worker)
        self.thread_pool.start(worker)

    def _on_undo_success(self, res: dict) -> None:
        if self._closing:
            return
        note_id = int(res["note_id"])
        lemma = str(res["lemma"])
        was_known = self._last_mined_was_known.pop(note_id, False)
        if not was_known:
            self.store.known_words.discard(lemma)
        self.store.today_mined_count = max(0, self.store.today_mined_count - 1)
        self._update_budget_pill()
        if self._current_record is not None:
            self._retokenize_current()
        msg = f"↩ 已撤銷「{lemma}」"
        self.statusBar().showMessage(msg, 4000)
        self.undo_toast.show_info(msg, duration_ms=2600)

    def _on_undo_failed(self, err: str) -> None:
        msg = f"⚠ 撤銷失敗：{err}"
        self.statusBar().showMessage(msg, 5000)
        self.undo_toast.show_info(msg, duration_ms=4000)

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
        if not self.isVisible():
            self.undo_toast.show_info(f"⚠ 收錄失敗：{err}", duration_ms=5000)
            return
        QMessageBox.warning(
            self,
            "收錄 Anki 失敗",
            f"{err}\n\n可點擊右上角「↻ 啟動 Anki」或從「☰ 工具 → 一鍵初始化精靈」檢查連線。",
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
            self.game_badge.setText(self.config.current_game_name or "🌸 等待遊戲 / mpv 字幕")
            self.game_badge.setObjectName("PillInfo")
            self.thread_combo.hide()
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
        self.thread_combo.setVisible(len(threads) >= 2)

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
        matched = self.voice_packs.resolve_pack_for_game(
            game_name=target.name,
            exe_path=target.exe_path,
        )
        if matched is not None:
            self.config.active_voice_pack_id = matched.pack_id
            self.statusBar().showMessage(
                f"✨ 已自動配對純淨人聲包《{matched.game_name}》（{matched.voiced_line_count} 句 · 0% BGM）",
                6000,
            )
        self.config.save()

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
            matched = self.voice_packs.resolve_pack_for_game(
                game_name=game_name,
                exe_path=str(exe_path),
            )
            if matched is not None:
                self.config.active_voice_pack_id = matched.pack_id
            self.config.save()
            self._rebuild_game_menu()
            self.game_badge.setText(f"🎮 {game_name}")
            vp_hint = f" · ✨純人聲包《{matched.game_name}》已配對" if matched else ""
            self.statusBar().showMessage(
                f"🎮 已啟動「{game_name}」(PID {pid}){vp_hint}，正在背景自動注入 Hook...",
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

    # === 覆蓋層、學習藍圖、mpv 影片橋接與工具視窗 ===
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
        if hasattr(self, "_tray_overlay_act"):
            self._tray_overlay_act.blockSignals(True)
            self._tray_overlay_act.setChecked(visible)
            self._tray_overlay_act.blockSignals(False)
        if self.mpv_bridge.is_connected:
            if visible and (self.config.overlay_replace_mode or self.overlay._backdrop == "transparent"):
                self.mpv_bridge.set_native_subtitle_visible(False)
            elif not visible:
                self.mpv_bridge.set_native_subtitle_visible(True)

    def _toggle_listening(self, paused: bool) -> None:
        self.ingestion.paused = paused
        self.statusBar().showMessage("已暫停監聽（無阻沉浸）" if paused else "已恢復自動監聽台詞與字幕", 3500)

    def _open_verb_lesson(self, lesson: VerbLesson) -> None:
        self.overlay.popup.hide()
        self.open_learning_blueprint(lesson=lesson)

    def open_learning_blueprint(self, pattern_id: str = "", lesson: VerbLesson | None = None) -> None:
        dlg = LearningBlueprintDialog(
            grammar=self.grammar_engine,
            progress_store=self.progress_store,
            known_words_count=len(self.store.known_words),
            stage_mode=self.config.stage_mode,
            theme_name=self.config.ui_theme,
            parent=self,
        )
        if lesson is not None:
            dlg.verb_explorer.select_lesson(lesson)
            dlg.tabs.setCurrentWidget(dlg.verb_explorer)
        elif isinstance(pattern_id, str) and pattern_id:
            dlg.select_pattern(pattern_id)
        dlg.mine_grammar_requested.connect(self._mine_grammar_card)
        dlg.exec()

    def open_media_bridge(self) -> None:
        dlg = MediaBridgeDialog(
            config=self.config,
            mpv_bridge=self.mpv_bridge,
            parent=self,
        )
        dlg.mpv_launched.connect(lambda: self._toggle_overlay(True) if not self.overlay.isVisible() else None)
        dlg.immersion_settings_changed.connect(self._sync_mpv_immersion_buttons)
        dlg.exec()

    def _sync_mpv_immersion_buttons(self) -> None:
        self.mpv_speed_btn.blockSignals(True)
        self.mpv_speed_btn.setChecked(self.config.mpv_auto_speedup)
        self.mpv_speed_btn.blockSignals(False)

    def open_voice_pack_manager(self, initial_tab: int = 0) -> None:
        dlg = VoicePackDialog(
            config=self.config,
            voice_packs=self.voice_packs,
            thread_pool=self.thread_pool,
            initial_tab=initial_tab,
            parent=self,
        )
        dlg.packs_changed.connect(self._on_voice_packs_changed)
        dlg.exec()

    def open_sentence_bank(self) -> None:
        dlg = SentenceBankDialog(
            config=self.config,
            voice_packs=self.voice_packs,
            scanner=self.sentence_bank_scanner,
            known_words=self.store.known_words,
            thread_pool=self.thread_pool,
            parent=self,
        )
        dlg.mine_item_requested.connect(self._mine_sentence_bank_item)
        dlg.mark_known_requested.connect(self._mark_bank_word_known)
        dlg.exec()

    def _mark_bank_word_known(self, lemma: str) -> None:
        if lemma:
            self.store.known_words.add(lemma)
            if self._current_record is not None:
                self._retokenize_current()
            self.statusBar().showMessage(f"✓ 已將「{lemma}」標記為已知詞", 3000)

    def _mine_sentence_bank_item(self, item: SentenceBankItem) -> None:
        analysis = self.grammar_engine.analyze(item.tokenized)
        rec = SentenceRecord(
            record_id=-1,
            raw_text=item.clean_text,
            tokenized=item.tokenized,
            timestamp=time.time(),
            source="voice_pack",
            media_title=item.pack_name or self.config.current_game_name,
            grammar_analysis=analysis,
            pack_translation=item.translation,
        )
        if item.voice_ids:
            clip = self.voice_packs.extract_single_line_mp3(item.pack_id, item.voice_ids)
            if clip is not None:
                rec.audio_clip = clip
                rec.audio_source = "voice_pack"
            else:
                rec.audio_source = "silent"
        else:
            rec.audio_source = "silent"
        self._mine_record_token(rec, item.target_token)

    def _on_voice_packs_changed(self) -> None:
        if self.config.active_voice_pack_id:
            packs = [
                p
                for p in self.voice_packs.list_packs()
                if p.pack_id == self.config.active_voice_pack_id
            ]
            if packs:
                pk = packs[0]
                self.statusBar().showMessage(
                    f"📦 已啟用純淨人聲索引《{pk.game_name}》（{pk.voiced_line_count} 句有聲台詞 · 0% BGM）",
                    5500,
                )

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
        prev_size = (self.config.overlay_width, self.config.overlay_height)
        prev_align = self.config.overlay_text_align
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
            if (self.config.overlay_width, self.config.overlay_height) != prev_size:
                self.overlay.set_overlay_size(
                    self.config.overlay_width,
                    self.config.overlay_height,
                    save=False,
                )
            if self.config.overlay_text_align != prev_align:
                self.overlay.set_text_align(self.config.overlay_text_align, save=False)
            self._apply_stage_ui()
            self._update_budget_pill()
            self._background_sync_anki()

    def _retokenize_current(self) -> None:
        if self._closing or self._current_record is None:
            return
        self._current_record.tokenized = self.tokenizer.tokenize(
            self._current_record.raw_text, self.store.known_words
        )
        self._current_record.grammar_analysis = self.grammar_engine.analyze(
            self._current_record.tokenized
        )
        self._display_record(self._current_record)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._tray_available and self.config.start_in_tray_overlay and not self._force_quit:
            event.ignore()
            self.hide()
            return
        self._closing = True
        self.hook_manager.detach()
        self.mpv_bridge.stop()
        self.undo_toast.close()
        self.overlay.shutdown_window_pin()
        self.overlay.close()
        if self.tray_icon is not None:
            self.tray_icon.hide()
        self.ingestion.shutdown()
        self.voice_packs.close()
        self.progress_store.close()
        self.dictionary.close()
        super().closeEvent(event)
