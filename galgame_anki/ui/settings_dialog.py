"""設定視窗（Anki、原聲語音、二次元主題與 Hook / 影片 / Wine 啟動）。"""

from __future__ import annotations

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from galgame_anki.config import AppConfig


class SettingsDialog(QDialog):
    """集中管理 Anki、語音錄製、Hook / 字幕監聽與 Wine 日區參數（零付費 API）。"""

    def __init__(
        self,
        config: AppConfig,
        thread_pool: QThreadPool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("⚙ GalAnki 設定")
        self.resize(560, 460)
        self.config = config
        self.thread_pool = thread_pool

        self._build_ui()
        self._load_values()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)

        tabs = QTabWidget()

        # --- Tab 1: Anki・語音與外觀 ---
        anki_tab = QWidget()
        af = QFormLayout(anki_tab)
        af.setSpacing(10)

        self.anki_url_edit = QLineEdit()
        af.addRow("AnkiConnect 位址：", self.anki_url_edit)

        self.deck_edit = QLineEdit()
        af.addRow("目標 Anki 牌組：", self.deck_edit)

        self.note_type_edit = QLineEdit()
        af.addRow("卡片模板名稱：", self.note_type_edit)

        self.daily_limit_spin = QSpinBox()
        self.daily_limit_spin.setRange(1, 200)
        af.addRow("每日製卡預算（防爆倉）：", self.daily_limit_spin)

        self.dict_mode_combo = QComboBox()
        self.dict_mode_combo.addItem("自動跟隨學習階段（Lv.1–2 雙語 / Lv.3 單語）", "auto")
        self.dict_mode_combo.addItem("強制雙語詞典優先", "bilingual")
        self.dict_mode_combo.addItem("強制日日單語詞典優先", "monolingual")
        af.addRow("詞典顯示策略：", self.dict_mode_combo)

        self.auto_sync_chk = QCheckBox("啟動時自動同步 Anki 已有詞彙（啟用 1T 句子高亮與防重）")
        af.addRow(self.auto_sync_chk)

        self.audio_chk = QCheckBox("一鍵製卡時自動錄入遊戲／影片原聲語音 (.mp3 寫入 SentenceAudio)")
        af.addRow(self.audio_chk)

        self.audio_pad_spin = QSpinBox()
        self.audio_pad_spin.setRange(0, 1500)
        self.audio_pad_spin.setSuffix(" ms")
        af.addRow("語音開頭提前緩衝：", self.audio_pad_spin)

        self.audio_max_spin = QSpinBox()
        self.audio_max_spin.setRange(3, 30)
        self.audio_max_spin.setSuffix(" 秒")
        af.addRow("單句語音最長擷取：", self.audio_max_spin)

        self.screenshot_chk = QCheckBox("一鍵製卡時自動附帶當下遊戲／動畫 CG 畫面截圖（音畫情景記憶）")
        af.addRow(self.screenshot_chk)

        self.forge_chk = QCheckBox("收錄前開啟 1 秒「✨ Refold 精煉製卡視窗」（精選單一核心意思＋核對音畫）")
        af.addRow(self.forge_chk)

        self.theme_combo = QComboBox()
        self.theme_combo.addItem("🌸 櫻霧輕透（二次元清新淺色）", "sakura")
        self.theme_combo.addItem("🌙 星夜琉璃（二次元護眼深色）", "starlight")
        af.addRow("介面風格主題：", self.theme_combo)

        tabs.addTab(anki_tab, "Anki・語音與外觀")

        # --- Tab 2: 文本接收、Hook 與 Wine ---
        hook_tab = QWidget()
        hf = QFormLayout(hook_tab)
        hf.setSpacing(10)

        self.ws_enabled_chk = QCheckBox("啟用內建 WebSocket 接收器（對接 Textractor / LunaTranslator / Agent）")
        hf.addRow(self.ws_enabled_chk)

        self.ws_port_spin = QSpinBox()
        self.ws_port_spin.setRange(1024, 65535)
        hf.addRow("WebSocket 連接埠：", self.ws_port_spin)

        self.clip_watch_chk = QCheckBox("啟用背景剪貼簿自動監聽（KDE Wayland 使用 wl-paste --watch）")
        hf.addRow(self.clip_watch_chk)

        self.dedup_chk = QCheckBox("自動清理引擎重複字元（如「ああいい」→「あい」）")
        hf.addRow(self.dedup_chk)

        self.ignore_non_jp_chk = QCheckBox("自動忽略不含日文字元的剪貼簿與字幕內容")
        hf.addRow(self.ignore_non_jp_chk)

        self.wine_cmd_edit = QLineEdit()
        hf.addRow("Wine / 啟動指令：", self.wine_cmd_edit)

        self.wine_locale_edit = QLineEdit()
        hf.addRow("生肉日區 Locale：", self.wine_locale_edit)

        tabs.addTab(hook_tab, "Hook・字幕與 Wine")

        layout.addWidget(tabs, 1)

        # 底部儲存 / 取消
        btns = QHBoxLayout()
        btns.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(cancel_btn)

        save_btn = QPushButton("儲存設定")
        save_btn.setObjectName("PrimaryBtn")
        save_btn.clicked.connect(self._save_and_accept)
        btns.addWidget(save_btn)
        layout.addLayout(btns)

    def _load_values(self) -> None:
        c = self.config
        self.anki_url_edit.setText(c.anki_url)
        self.deck_edit.setText(c.deck_name)
        self.note_type_edit.setText(c.note_type)
        self.daily_limit_spin.setValue(c.daily_card_limit)
        idx = self.dict_mode_combo.findData(c.dict_mode)
        if idx >= 0:
            self.dict_mode_combo.setCurrentIndex(idx)
        self.auto_sync_chk.setChecked(c.auto_sync_known_words)
        self.audio_chk.setChecked(c.capture_audio_on_mine)
        self.audio_pad_spin.setValue(c.audio_pre_pad_ms)
        self.audio_max_spin.setValue(c.audio_max_seconds)
        self.screenshot_chk.setChecked(c.capture_screenshot_on_mine)
        self.forge_chk.setChecked(c.quick_forge_before_mine)
        t_idx = self.theme_combo.findData(c.ui_theme)
        if t_idx >= 0:
            self.theme_combo.setCurrentIndex(t_idx)

        self.ws_enabled_chk.setChecked(c.ws_enabled)
        self.ws_port_spin.setValue(c.ws_port)
        self.clip_watch_chk.setChecked(c.clipboard_watch)
        self.dedup_chk.setChecked(c.dedup_repeated_chars)
        self.ignore_non_jp_chk.setChecked(c.ignore_non_japanese)
        self.wine_cmd_edit.setText(c.wine_command)
        self.wine_locale_edit.setText(c.wine_locale)

    def _save_and_accept(self) -> None:
        c = self.config
        c.anki_url = self.anki_url_edit.text().strip() or "http://127.0.0.1:8765"
        c.deck_name = self.deck_edit.text().strip() or "Galgame日語"
        c.note_type = self.note_type_edit.text().strip() or "GalImmersion-OneClick"
        c.daily_card_limit = self.daily_limit_spin.value()
        c.dict_mode = str(self.dict_mode_combo.currentData() or "auto")
        c.auto_sync_known_words = self.auto_sync_chk.isChecked()
        c.capture_audio_on_mine = self.audio_chk.isChecked()
        c.audio_pre_pad_ms = self.audio_pad_spin.value()
        c.audio_max_seconds = self.audio_max_spin.value()
        c.capture_screenshot_on_mine = self.screenshot_chk.isChecked()
        c.quick_forge_before_mine = self.forge_chk.isChecked()
        c.ui_theme = str(self.theme_combo.currentData() or "sakura")

        c.ws_enabled = self.ws_enabled_chk.isChecked()
        c.ws_port = self.ws_port_spin.value()
        c.clipboard_watch = self.clip_watch_chk.isChecked()
        c.dedup_repeated_chars = self.dedup_chk.isChecked()
        c.ignore_non_japanese = self.ignore_non_jp_chk.isChecked()
        c.wine_command = self.wine_cmd_edit.text().strip() or "wine"
        c.wine_locale = self.wine_locale_edit.text().strip() or "ja_JP.UTF-8"
        c.save()
        self.accept()
