"""使用本機 Mock HTTP 伺服器端到端測試 AnkiConnect 一鍵初始化、附語音與離線語法製卡及 FSRS 偵測。"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from galgame_anki.core.anki_client import AnkiCardPayload, AnkiConnectClient
from galgame_anki.core.anki_templates import BACK_TEMPLATE, CARD_CSS
from galgame_anki.core.dictionary import DictionaryEngine
from galgame_anki.core.grammar_engine import OfflineGrammarEngine
from galgame_anki.core.tokenizer import JapaneseTokenizer


class _MockState:
    def __init__(self) -> None:
        self.decks: set[str] = {"Default"}
        self.models: dict[str, list[str]] = {}
        self.notes: dict[int, dict[str, Any]] = {}
        self.media: dict[str, str] = {}
        self.next_id: int = 1001
        self.media_dir: str = ""
        self.deck_config: dict[str, Any] = {"fsrs": True}


class _MockServerHandler(BaseHTTPRequestHandler):
    state: _MockState

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))

        action = body.get("action")
        params = body.get("params", {})
        st = self.state

        if action == "version":
            self._send_anki(6)
        elif action == "deckNames":
            self._send_anki(sorted(st.decks))
        elif action == "createDeck":
            st.decks.add(params["deck"])
            self._send_anki(1)
        elif action == "modelNames":
            self._send_anki(list(st.models.keys()))
        elif action == "createModel":
            st.models[params["modelName"]] = list(params["inOrderFields"])
            self._send_anki({"id": 1})
        elif action == "modelFieldNames":
            self._send_anki(st.models.get(params["modelName"], []))
        elif action == "modelFieldAdd":
            mname = params["modelName"]
            st.models.setdefault(mname, []).append(params["fieldName"])
            self._send_anki(None)
        elif action in ("updateModelStyling", "updateModelTemplates"):
            self._send_anki(None)
        elif action == "getDeckConfig":
            self._send_anki(st.deck_config)
        elif action == "getMediaDirPath":
            self._send_anki(st.media_dir)
        elif action == "findNotes":
            self._send_anki(list(st.notes.keys()))
        elif action == "notesInfo":
            infos = []
            for nid in params.get("notes", []):
                if nid in st.notes:
                    infos.append(
                        {
                            "noteId": nid,
                            "fields": {
                                k: {"value": v} for k, v in st.notes[nid]["fields"].items()
                            },
                        }
                    )
            self._send_anki(infos)
        elif action == "storeMediaFile":
            st.media[params["filename"]] = params["data"]
            self._send_anki(params["filename"])
        elif action == "addNote":
            nid = st.next_id
            st.next_id += 1
            st.notes[nid] = params["note"]
            self._send_anki(nid)
        elif action == "updateNoteFields":
            nid = params["note"]["id"]
            st.notes[nid]["fields"].update(params["note"]["fields"])
            self._send_anki(None)
        else:
            self._send_json({"result": None, "error": f"unknown action: {action}"})

    def _send_anki(self, result: Any) -> None:
        self._send_json({"result": result, "error": None})

    def _send_json(self, obj: Any) -> None:
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class TestAnkiAndOfflineGrammar(unittest.TestCase):
    def setUp(self) -> None:
        self.state = _MockState()
        handler_cls = type("BoundHandler", (_MockServerHandler,), {"state": self.state})
        self.server = HTTPServer(("127.0.0.1", 0), handler_cls)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def test_one_click_init_mine_with_audio_and_offline_grammar(self) -> None:
        base_url = f"http://127.0.0.1:{self.port}"
        anki = AnkiConnectClient(base_url)

        report = anki.one_click_initialize("Galgame日語")
        self.assertTrue(report.connected)
        self.assertTrue(report.deck_ready)
        self.assertTrue(report.model_ready)
        self.assertTrue(report.model_created)
        self.assertTrue(report.fsrs_enabled)

        with tempfile.TemporaryDirectory() as tmp:
            eng = DictionaryEngine(db_path=Path(tmp) / "d.db", load_starter=True)
            tok = JapaneseTokenizer(eng)
            grammar = OfflineGrammarEngine()
            tokenized = tok.tokenize("外の世界を知りたいという想いは、募る一方だ")
            analysis = grammar.analyze(tokenized)
            grammar_html = analysis.to_compact_html()
            eng.close()

        self.assertIn("一方だ", grammar_html)
        self.assertIn("助詞", grammar_html)

        note_id = anki.add_card(
            deck_name="Galgame日語",
            note_type="GalImmersion-OneClick",
            card=AnkiCardPayload(
                target_word="募る",
                target_reading="つのる",
                pitch_accent="2",
                sentence="外の世界を知りたいという想いは、【募る】一方だ",
                sentence_furigana=tokenized.furigana_html,
                sentence_translation="想了解外面世界的心情越來越強烈。",
                definition_bilingual="越來越強烈",
                definition_monolingual="しだいに激しくなる。",
                source_game="千恋＊万花",
                stage_mode="lv2",
                ai_grammar_notes=grammar_html,
                audio_bytes=b"ID3FAKEMP3",
                audio_filename="gal_voice_123.mp3",
                screenshot_bytes=b"FAKEWEBP",
                screenshot_filename="shot_1.webp",
            ),
        )
        self.assertEqual(note_id, 1001)
        self.assertIn("shot_1.webp", self.state.media)
        self.assertIn("gal_voice_123.mp3", self.state.media)
        self.assertEqual(
            self.state.notes[note_id]["fields"]["SentenceAudio"],
            "[sound:gal_voice_123.mp3]",
        )
        self.assertEqual(
            self.state.notes[note_id]["fields"]["SentenceTranslation"],
            "想了解外面世界的心情越來越強烈。",
        )
        self.assertIn("一方だ", self.state.notes[note_id]["fields"]["AIGrammarNotes"])

        # 確保 Anki 卡片 CSS 嚴格遵循 Anki 原生默認主題、並完整支援三階段分層（Lv.1 讀音 / Lv.2 音畫語境 / Lv.3 單語）
        self.assertNotIn("#141821", CARD_CSS)
        self.assertNotIn("#1b2230", CARD_CSS)
        self.assertNotIn("#60a5fa", CARD_CSS)
        self.assertIn("lv1-reading-hero", BACK_TEMPLATE)
        self.assertIn("SentenceTranslation", BACK_TEMPLATE)
        self.assertIn("句型拆解", BACK_TEMPLATE)

        known = anki.fetch_known_words("Galgame日語")
        self.assertIn("募る", known)

    def test_fsrs_detection_anki26_collection_db_and_empty_weights(self) -> None:
        base_url = f"http://127.0.0.1:{self.port}"
        anki = AnkiConnectClient(base_url)

        # 情境 1：Anki 26.x 使用預設 FSRS 參數，getDeckConfig 的權重陣列為空且無本機 DB，不得誤報 False
        self.state.deck_config = {"fsrsWeights": [], "fsrsParams5": [], "fsrsParams6": []}
        self.state.media_dir = ""
        self.assertIsNone(anki.check_fsrs_status("Galgame日語"))

        # 情境 2：本機 collection.anki2 的 config 表記錄 fsrs = b'true'（即使權重陣列為空）
        with tempfile.TemporaryDirectory() as tmp:
            profile_dir = Path(tmp) / "profile"
            media_dir = profile_dir / "collection.media"
            media_dir.mkdir(parents=True)
            db_path = profile_dir / "collection.anki2"

            conn = sqlite3.connect(str(db_path))
            conn.execute("CREATE TABLE config (key TEXT PRIMARY KEY, val BLOB)")
            conn.execute("INSERT INTO config (key, val) VALUES ('fsrs', ?)", (b"true",))
            conn.commit()
            conn.close()

            self.state.media_dir = str(media_dir)
            self.assertTrue(anki.check_fsrs_status("Galgame日語"))

            # 情境 3：若確實關閉 FSRS（b'false'），能正確偵測為 False
            conn = sqlite3.connect(str(db_path))
            conn.execute("UPDATE config SET val = ? WHERE key = 'fsrs'", (b"false",))
            conn.commit()
            conn.close()
            self.assertFalse(anki.check_fsrs_status("Galgame日語"))


if __name__ == "__main__":
    unittest.main()
