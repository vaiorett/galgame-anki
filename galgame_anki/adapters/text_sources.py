"""即時文本接收引擎：WebSocket Server (對接 LunaHook / Textractor / Agent) + Wayland/Qt 剪貼簿監聽。"""

from __future__ import annotations

import json
import os
import shutil
import time
from typing import Any

from PySide6.QtCore import QObject, QProcess, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtNetwork import QHostAddress
from PySide6.QtWebSockets import QWebSocket, QWebSocketServer

from galgame_anki.core.tokenizer import clean_hooked_text, is_japanese_text


def parse_ws_payload(raw_message: str) -> tuple[str, str, str]:
    """解析純文字或常見 Hooker JSON 格式，回傳 (sentence, source, media_title)。"""
    text = raw_message.strip()
    if not text:
        return "", "ws", ""
    if text.startswith("{") and text.endswith("}"):
        try:
            obj: Any = json.loads(text)
            if isinstance(obj, dict):
                src = str(obj.get("source") or "ws").strip() or "ws"
                title = str(obj.get("title") or obj.get("media_title") or "").strip()
                for key in ("sentence", "text", "line", "content", "message"):
                    val = obj.get(key)
                    if isinstance(val, str) and val.strip():
                        return val.strip(), src, title
        except json.JSONDecodeError:
            pass
    return text, "ws", ""


def extract_sentence_from_payload(raw_message: str) -> str:
    """解析純文字或常見 Hooker JSON 格式（Agent / LunaTranslator / GSM）。"""
    sentence, _, _ = parse_ws_payload(raw_message)
    return sentence


class TextIngestionManager(QObject):
    """統一管理多來源 Galgame 文本串流，輸出已清理與去重的乾淨日文台詞。"""

    sentence_received = Signal(str, str)            # (cleaned_sentence, source_name)
    sentence_meta_received = Signal(str, str, str)  # (cleaned_sentence, source_name, media_title)
    ws_clients_changed = Signal(int)                # connected websocket client count
    status_changed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ws_server: QWebSocketServer | None = None
        self._ws_clients: list[QWebSocket] = []
        self._wl_process: QProcess | None = None
        self._clipboard_connected: bool = False

        self.dedup_repeated: bool = True
        self.ignore_non_japanese: bool = True
        self.paused: bool = False
        self._last_text: str = ""
        self._last_emit_ts: float = 0.0
        self._wl_buffer: bytes = b""

    def start_websocket(self, host: str = "127.0.0.1", port: int = 6677) -> bool:
        self.stop_websocket()
        server = QWebSocketServer(
            "GalAnkiTextHooker",
            QWebSocketServer.SslMode.NonSecureMode,
            self,
        )
        addr = QHostAddress(host)
        if not server.listen(addr, port):
            self.status_changed.emit(f"WebSocket 監聽失敗 ({host}:{port})：{server.errorString()}")
            return False

        server.newConnection.connect(self._on_ws_new_connection)
        self._ws_server = server
        self.status_changed.emit(f"WebSocket 已就緒：ws://{host}:{port}")
        return True

    def stop_websocket(self) -> None:
        for sock in list(self._ws_clients):
            try:
                sock.disconnected.disconnect()
                sock.textMessageReceived.disconnect()
            except (RuntimeError, TypeError):
                pass
            try:
                sock.close()
            except RuntimeError:
                pass
        self._ws_clients.clear()
        if self._ws_server is not None:
            self._ws_server.close()
            self._ws_server.deleteLater()
            self._ws_server = None

    @property
    def is_ws_listening(self) -> bool:
        return self._ws_server is not None and self._ws_server.isListening()

    @property
    def ws_client_count(self) -> int:
        return len(self._ws_clients)

    def _on_ws_new_connection(self) -> None:
        if not self._ws_server:
            return
        client = self._ws_server.nextPendingConnection()
        if client is None:
            return
        self._ws_clients.append(client)
        client.textMessageReceived.connect(lambda msg, c=client: self._on_ws_message(msg, c))
        client.disconnected.connect(lambda c=client: self._on_ws_disconnected(c))
        self.ws_clients_changed.emit(len(self._ws_clients))
        self.status_changed.emit(f"Hook 客戶端已連線（目前 {len(self._ws_clients)} 個連線）")

    def _on_ws_disconnected(self, client: QWebSocket) -> None:
        if client in self._ws_clients:
            self._ws_clients.remove(client)
            self.ws_clients_changed.emit(len(self._ws_clients))
        try:
            client.deleteLater()
        except RuntimeError:
            pass

    def _on_ws_message(self, message: str, client: QWebSocket | None = None) -> None:
        raw = message.strip()
        if raw.startswith("{") and raw.endswith("}"):
            try:
                obj = json.loads(raw)
                if isinstance(obj, dict) and obj.get("type") == "ping":
                    if client is not None:
                        try:
                            client.sendTextMessage(json.dumps({"type": "pong", "app": "GalAnki"}))
                        except RuntimeError:
                            pass
                    return
            except json.JSONDecodeError:
                pass
        extracted, source, title = parse_ws_payload(message)
        self._handle_raw_text(extracted, source=source, media_title=title)

    def start_clipboard_watch(self) -> str:
        """啟動剪貼簿監聽：KDE Wayland 優先採用 wl-paste --watch（支援背景無焦點攔截）。"""
        self.stop_clipboard_watch()

        is_wayland = os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
        wl_paste = shutil.which("wl-paste")

        if is_wayland and wl_paste:
            proc = QProcess(self)
            # 每次剪貼簿變更時輸出內容並附帶明確分隔符，確保多行台詞不拆散
            proc.setProgram(wl_paste)
            proc.setArguments(
                [
                    "--type",
                    "text/plain;charset=utf-8",
                    "--watch",
                    "sh",
                    "-c",
                    'cat; printf "\\036"',
                ]
            )
            proc.readyReadStandardOutput.connect(self._on_wl_paste_output)
            proc.start()
            if proc.waitForStarted(1500):
                self._wl_process = proc
                self.status_changed.emit("剪貼簿監聽中（KDE Wayland 背景模式）")
                return "wayland-wl-paste"

        # 備援：Qt 內建剪貼簿訊號（X11 / Windows / 無 wl-paste 環境）
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.dataChanged.connect(self._on_qt_clipboard_changed)
            self._clipboard_connected = True
            self.status_changed.emit("剪貼簿監聽中（Qt 標準模式）")
            return "qt-clipboard"

        return "unavailable"

    def stop_clipboard_watch(self) -> None:
        if self._wl_process is not None:
            self._wl_process.kill()
            self._wl_process.waitForFinished(1000)
            self._wl_process.deleteLater()
            self._wl_process = None
            self._wl_buffer = b""
        if self._clipboard_connected:
            clipboard = QGuiApplication.clipboard()
            if clipboard is not None:
                try:
                    clipboard.dataChanged.disconnect(self._on_qt_clipboard_changed)
                except (RuntimeError, TypeError):
                    pass
            self._clipboard_connected = False

    def _on_wl_paste_output(self) -> None:
        if not self._wl_process:
            return
        chunk = bytes(self._wl_process.readAllStandardOutput())
        self._wl_buffer += chunk
        while b"\x1e" in self._wl_buffer:
            raw_part, self._wl_buffer = self._wl_buffer.split(b"\x1e", 1)
            text = raw_part.decode("utf-8", errors="ignore")
            self._handle_raw_text(text, source="clipboard")

    def _on_qt_clipboard_changed(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            return
        text = clipboard.text()
        self._handle_raw_text(text, source="clipboard")

    def submit_manual_text(self, text: str, source: str = "manual", media_title: str = "") -> None:
        """供手動輸入測試或匯入攻略範例句使用。"""
        self._handle_raw_text(text, source=source, media_title=media_title, force=True)

    def _handle_raw_text(
        self,
        raw: str,
        source: str,
        media_title: str = "",
        force: bool = False,
    ) -> None:
        if self.paused and not force:
            return
        cleaned = clean_hooked_text(raw, dedup_repeated=self.dedup_repeated)
        if not cleaned or len(cleaned) > 400:
            return
        if self.ignore_non_japanese and not force and not is_japanese_text(cleaned):
            return

        now = time.monotonic()
        if not force and cleaned == self._last_text and (now - self._last_emit_ts) < 1.5:
            return

        self._last_text = cleaned
        self._last_emit_ts = now
        self.sentence_received.emit(cleaned, source)
        self.sentence_meta_received.emit(cleaned, source, media_title)

    def shutdown(self) -> None:
        self.stop_websocket()
        self.stop_clipboard_watch()
