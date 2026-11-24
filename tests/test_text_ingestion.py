"""測試 WebSocket 即時 Hook 接收與文本過濾管線。"""

from __future__ import annotations

import json
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer, QUrl
from PySide6.QtWebSockets import QWebSocket
from PySide6.QtWidgets import QApplication

from galgame_anki.adapters.text_sources import (
    TextIngestionManager,
    extract_sentence_from_payload,
)


class TestTextIngestion(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_extract_payload_formats(self) -> None:
        self.assertEqual(extract_sentence_from_payload("こんにちは"), "こんにちは")
        self.assertEqual(
            extract_sentence_from_payload(json.dumps({"sentence": "外の世界を知りたい"})),
            "外の世界を知りたい",
        )
        self.assertEqual(
            extract_sentence_from_payload(json.dumps({"text": "やがて道となるのだ。"})),
            "やがて道となるのだ。",
        )

    def test_websocket_end_to_end_receiving(self) -> None:
        mgr = TextIngestionManager()
        started = mgr.start_websocket("127.0.0.1", 0)
        self.assertTrue(started)
        assert mgr._ws_server is not None
        port = mgr._ws_server.serverPort()

        received: list[tuple[str, str]] = []
        mgr.sentence_received.connect(lambda txt, src: received.append((txt, src)))

        loop = QEventLoop()
        client = QWebSocket()

        def on_connected() -> None:
            # 1) 非日文應被自動過濾
            client.sendTextMessage("Hello pure English ignored")
            # 2) 模擬 Textractor 重複字元自動還原
            client.sendTextMessage("ききょょううはは")
            # 3) 模擬 Agent/GSM JSON 格式台詞
            client.sendTextMessage(json.dumps({"sentence": "米を炊いて握り飯を作る"}))
            QTimer.singleShot(200, loop.quit)

        client.connected.connect(on_connected)
        client.open(QUrl(f"ws://127.0.0.1:{port}"))
        QTimer.singleShot(2500, loop.quit)
        loop.exec()

        client.close()
        mgr.shutdown()

        texts = [t for t, _ in received]
        self.assertNotIn("Hello pure English ignored", texts)
        self.assertIn("きょうは", texts)
        self.assertIn("米を炊いて握り飯を作る", texts)


if __name__ == "__main__":
    unittest.main()
