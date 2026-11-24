"""🎬 mpv 影片字幕一鍵連線視窗（完整支援 Flatpak io.mpv.Mpv 與系統原生 mpv）。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
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
    """讓置頂覆蓋層一鍵讀取 Flatpak / 原生 mpv 本地動畫與影片字幕（自動隱藏原字幕、無損抽幀與切原聲）。"""

    mpv_launched = Signal()

    def __init__(
        self,
        config: AppConfig,
        mpv_bridge: MpvSubtitleBridge,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("🎬 mpv 本地動畫／影片字幕連線")
        self.resize(650, 310)
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
            "的日語字幕，並自動隱藏 mpv 內建原字幕（絕不看兩份字幕）！"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        mpv_box = QFrame()
        mpv_box.setObjectName("CardPanel")
        ml = QVBoxLayout(mpv_box)
        ml.setSpacing(9)

        mpv_title = QLabel("🎬 mpv 本地動畫／影片字幕（無損抽幀＋精準時間軸原聲！）")
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

        ml_note = QLabel(
            "💡 寫入 mpv.conf 後，日後直接雙擊任何日語動畫用 Flatpak mpv 播放就會自動同步；播放中隨時在 mpv 按鍵盤「v」也可切換隱藏原字幕，不影響 GalAnki 讀取字幕與無損切原聲！"
        )
        ml_note.setObjectName("MutedLabel")
        ml_note.setWordWrap(True)
        ml.addWidget(ml_note)
        layout.addWidget(mpv_box)

        bottom = QHBoxLayout()
        bottom.addStretch()
        close_btn = QPushButton("關閉")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

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
