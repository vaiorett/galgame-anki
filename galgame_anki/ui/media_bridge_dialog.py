"""🎬 mpv 本地動畫／影片字幕連線與 AJATT 進階沉浸視窗（相容 Flatpak io.mpv.Mpv 與系統原生 mpv）。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from galgame_anki.adapters.mpv_bridge import (
    MpvSubtitleBridge,
    detect_mpv_backend,
    install_mpv_ipc_config,
    launch_mpv_video,
)
from galgame_anki.config import AppConfig


class MediaBridgeDialog(QDialog):
    """控制 Flatpak / 原生 mpv 字幕連線、原生字幕隱藏、無字幕倍速快進（sub-transition）與句尾自動暫停。"""

    mpv_launched = Signal()
    immersion_settings_changed = Signal()

    def __init__(
        self,
        config: AppConfig,
        mpv_bridge: MpvSubtitleBridge,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("🎬 mpv 本地動畫／影片字幕與進階沉浸設定")
        self.resize(710, 500)
        self.config = config
        self.mpv_bridge = mpv_bridge
        self._mpv_sub_hidden = False

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        intro = QLabel(
            "除了 Galgame 之外，GalAnki 的置頂覆蓋層也能直接讀取 <b>mpv 本地動畫／影片（支援 Flatpak io.mpv.Mpv）</b> "
            "的日語字幕，並結合 <b>AJATT sub-transition（無字幕片段自動倍速）</b> 與 <b>多行合併製卡</b>！"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # === 區塊 1：mpv 連線與設定 ===
        mpv_box = QFrame()
        mpv_box.setObjectName("CardPanel")
        ml = QVBoxLayout(mpv_box)
        ml.setSpacing(9)

        mpv_title = QLabel("1️⃣ mpv 本地動畫／影片字幕連線（無損抽幀＋精準時間軸原聲）")
        mpv_title.setStyleSheet("font-weight: 700; font-size: 14px; background: transparent;")
        ml.addWidget(mpv_title)

        backend = detect_mpv_backend()
        mpv_ok = bool(backend["installed"])
        conn_txt = "🟢 目前已有 mpv 連線中" if self.mpv_bridge.is_connected else "⚪ 等待 mpv 開啟影片"
        inst_txt = (
            f"✅ 已偵測到 {backend['label']}"
            if mpv_ok
            else "尚未安裝 mpv（Flatpak io.mpv.Mpv 或 sudo dnf install mpv）"
        )
        self.mpv_status = QLabel(f"狀態：{inst_txt}　｜　{conn_txt}")
        self.mpv_status.setObjectName("MutedLabel")
        self.mpv_status.setWordWrap(True)
        ml.addWidget(self.mpv_status)

        self.hide_mpv_sub_chk = QCheckBox(
            "啟動／設定 mpv 時隱藏 mpv 內建原字幕（避免出現兩份字幕，只看 GalAnki 互動覆蓋層！）"
        )
        self.hide_mpv_sub_chk.setChecked(True)
        ml.addWidget(self.hide_mpv_sub_chk)

        m_btns = QHBoxLayout()
        cfg_mpv_btn = QPushButton("⚡ 一鍵寫入 mpv.conf（相容 Flatpak）")
        cfg_mpv_btn.setObjectName("PrimaryBtn")
        cfg_mpv_btn.clicked.connect(self._setup_mpv_conf)
        m_btns.addWidget(cfg_mpv_btn)

        open_vid_btn = QPushButton("📂 用 mpv 開啟日語影片...")
        open_vid_btn.setObjectName("MineBtn")
        open_vid_btn.setEnabled(mpv_ok)
        open_vid_btn.clicked.connect(self._open_video_in_mpv)
        m_btns.addWidget(open_vid_btn)

        self.toggle_live_sub_btn = QPushButton("🙈 隱藏進行中 mpv 原字幕")
        self.toggle_live_sub_btn.setEnabled(self.mpv_bridge.is_connected)
        self.toggle_live_sub_btn.clicked.connect(self._toggle_live_mpv_sub)
        m_btns.addWidget(self.toggle_live_sub_btn)

        m_btns.addStretch()
        ml.addLayout(m_btns)
        layout.addWidget(mpv_box)

        # === 區塊 2：AJATT sub-transition & 精讀控制 ===
        imm_box = QFrame()
        imm_box.setObjectName("CardPanel")
        il = QVBoxLayout(imm_box)
        il.setSpacing(8)

        imm_title = QLabel("2️⃣ AJATT 主動濃縮沉浸（無字幕自動快進 · 句尾自動暫停 · 單鍵重播）")
        imm_title.setStyleSheet("font-weight: 700; font-size: 13.5px; background: transparent;")
        il.addWidget(imm_title)

        spd_row = QHBoxLayout()
        self.auto_speedup_chk = QCheckBox("⚡ 無字幕空白片段自動倍速略過（sub-transition，字幕出現瞬間切回 1.0x）")
        self.auto_speedup_chk.setChecked(self.config.mpv_auto_speedup)
        self.auto_speedup_chk.toggled.connect(self._apply_immersion_opts)
        spd_row.addWidget(self.auto_speedup_chk, 1)

        spd_row.addWidget(QLabel("快進倍率："))
        self.fast_speed_spin = QDoubleSpinBox()
        self.fast_speed_spin.setRange(1.2, 4.0)
        self.fast_speed_spin.setSingleStep(0.25)
        self.fast_speed_spin.setValue(self.config.mpv_fast_speed)
        self.fast_speed_spin.setSuffix("x")
        self.fast_speed_spin.valueChanged.connect(self._apply_immersion_opts)
        spd_row.addWidget(self.fast_speed_spin)
        il.addLayout(spd_row)

        self.auto_pause_chk = QCheckBox("⏸ 每句字幕結束自動暫停（精讀查詞模式；適合逐句吃透生詞）")
        self.auto_pause_chk.setChecked(self.config.mpv_auto_pause_sub_end)
        self.auto_pause_chk.toggled.connect(self._apply_immersion_opts)
        il.addWidget(self.auto_pause_chk)

        ctrl_row = QHBoxLayout()
        self.replay_btn = QPushButton("🔁 重播當前台詞")
        self.replay_btn.setEnabled(self.mpv_bridge.is_connected)
        self.replay_btn.clicked.connect(self._replay_current)
        ctrl_row.addWidget(self.replay_btn)

        self.pause_btn = QPushButton("⏯ 暫停／繼續播放")
        self.pause_btn.setEnabled(self.mpv_bridge.is_connected)
        self.pause_btn.clicked.connect(self._toggle_pause)
        ctrl_row.addWidget(self.pause_btn)
        ctrl_row.addStretch()
        il.addLayout(ctrl_row)

        ml_note = QLabel(
            "💡 <b>多句字幕合併：</b>當動畫把一句完整台詞拆成兩條字幕時，點主視窗「➕ 合併上句」或覆蓋層「➕」（快捷鍵 <code>Ctrl+J</code>），"
            "就會把前後兩句文字與影片原聲時間軸一次合併，切出完整不斷尾的 Anki 原聲！"
        )
        ml_note.setObjectName("MutedLabel")
        ml_note.setWordWrap(True)
        il.addWidget(ml_note)
        layout.addWidget(imm_box)

        bottom = QHBoxLayout()
        bottom.addStretch()
        close_btn = QPushButton("關閉")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

    def _apply_immersion_opts(self) -> None:
        self.config.mpv_auto_speedup = self.auto_speedup_chk.isChecked()
        self.config.mpv_fast_speed = float(self.fast_speed_spin.value())
        self.config.mpv_auto_pause_sub_end = self.auto_pause_chk.isChecked()
        self.config.save()
        self.mpv_bridge.configure_immersion(
            auto_speedup=self.config.mpv_auto_speedup,
            fast_speed=self.config.mpv_fast_speed,
            auto_pause_sub_end=self.config.mpv_auto_pause_sub_end,
        )
        self.immersion_settings_changed.emit()

    def _replay_current(self) -> None:
        if not self.mpv_bridge.replay_current_subtitle():
            self.mpv_status.setText("⚠ 尚未收到字幕時間軸，請等下一句字幕出現後再試。")

    def _toggle_pause(self) -> None:
        self.mpv_bridge.toggle_pause()

    def _setup_mpv_conf(self) -> None:
        try:
            paths = install_mpv_ipc_config(hide_native_sub=self.hide_mpv_sub_chk.isChecked())
            joined = "、".join(str(p) for p in paths)
            self.mpv_status.setText(f"✓ 已寫入 mpv 設定：{joined}（重新開啟 mpv 影片立即自動連線！）")
        except Exception as exc:
            QMessageBox.warning(self, "寫入 mpv.conf 失敗", str(exc))

    def _open_video_in_mpv(self) -> None:
        vid, _ = QFileDialog.getOpenFileName(
            self,
            "選擇日語動畫或影片檔",
            str(Path.home()),
            "Video Files (*.mkv *.mp4 *.webm *.avi *.mov);;All Files (*)",
        )
        if not vid:
            return
        try:
            pid = launch_mpv_video(
                Path(vid),
                hide_native_sub=self.hide_mpv_sub_chk.isChecked(),
            )
            self.mpv_status.setText(f"✓ 已啟動 mpv (PID {pid})，字幕出現後將自動同步進覆蓋層！")
            self.mpv_launched.emit()
        except Exception as exc:
            QMessageBox.warning(self, "啟動 mpv 失敗", str(exc))

    def _toggle_live_mpv_sub(self) -> None:
        self._mpv_sub_hidden = not self._mpv_sub_hidden
        ok = self.mpv_bridge.set_native_subtitle_visible(not self._mpv_sub_hidden)
        if ok:
            self.toggle_live_sub_btn.setText(
                "👁 恢復顯示 mpv 原字幕" if self._mpv_sub_hidden else "🙈 隱藏進行中 mpv 原字幕"
            )
            self.mpv_status.setText(
                "✓ 已隱藏 mpv 原生字幕，現在只看 GalAnki 互動覆蓋層！"
                if self._mpv_sub_hidden
                else "✓ 已恢復顯示 mpv 原生字幕。"
            )
