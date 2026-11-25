"""AnkiConnect HTTP JSON-RPC 客戶端與一鍵環境初始化引擎。"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from galgame_anki.core.anki_templates import (
    BACK_TEMPLATE,
    CARD_CSS,
    FRONT_TEMPLATE,
    NOTE_FIELDS,
    NOTE_TYPE_NAME,
    build_model_payload,
)
from galgame_anki.core.pitch_accent import render_pitch_graphs_html

ANKICONNECT_ADDON_CODE = "2055492159"


class AnkiConnectError(RuntimeError):
    """AnkiConnect 呼叫失敗或未啟動時拋出的明確錯誤。"""


@dataclass
class AnkiCardPayload:
    target_word: str
    target_reading: str
    pitch_accent: str
    sentence: str
    sentence_furigana: str
    definition_bilingual: str
    definition_monolingual: str
    pitch_graph: str = ""
    sentence_translation: str = ""
    source_game: str = ""
    stage_mode: str = "lv2"
    ai_grammar_notes: str = ""
    related_words_html: str = ""
    audio_bytes: bytes | None = None
    audio_filename: str = ""
    screenshot_bytes: bytes | None = None
    screenshot_filename: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class AnkiInitReport:
    connected: bool
    api_version: int | None = None
    deck_ready: bool = False
    model_ready: bool = False
    model_created: bool = False
    fsrs_enabled: bool | None = None
    known_words_count: int = 0
    messages: list[str] = field(default_factory=list)


class AnkiConnectClient:
    """封裝 AnkiConnect v6 常用操作的輕量客戶端。"""

    def __init__(self, url: str = "http://127.0.0.1:8765", timeout: float = 8.0) -> None:
        self.url = url
        self.timeout = timeout

    def invoke(self, action: str, **params: Any) -> Any:
        payload = {"action": action, "version": 6, "params": params}
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(self.url, json=payload)
                resp.raise_for_status()
                body = resp.json()
        except httpx.HTTPError as exc:
            raise AnkiConnectError(
                f"無法連線至 AnkiConnect ({self.url})。請確認 Anki 已啟動且已安裝外掛 {ANKICONNECT_ADDON_CODE}。"
            ) from exc
        except ValueError as exc:
            raise AnkiConnectError("AnkiConnect 回傳非 JSON 資料。") from exc

        if not isinstance(body, dict) or "error" not in body or "result" not in body:
            raise AnkiConnectError("AnkiConnect 回傳格式異常。")
        if body["error"] is not None:
            raise AnkiConnectError(str(body["error"]))
        return body["result"]

    def ping(self) -> int:
        """回傳 AnkiConnect 版本號，若無法連線則拋出 AnkiConnectError。"""
        return int(self.invoke("version"))

    def ensure_deck(self, deck_name: str) -> None:
        decks = self.invoke("deckNames")
        if deck_name not in decks:
            self.invoke("createDeck", deck=deck_name)

    def ensure_note_type(self, note_type: str = NOTE_TYPE_NAME) -> bool:
        """確保三階段卡片模板存在；若已存在則自動補齊缺失欄位與最新樣式。回傳是否新建。"""
        models = self.invoke("modelNames")
        if note_type not in models:
            self.invoke("createModel", **build_model_payload(note_type))
            return True

        # 已存在時，確保所需欄位完整並更新樣式與模板
        existing_fields = self.invoke("modelFieldNames", modelName=note_type)
        for field_name in NOTE_FIELDS:
            if field_name not in existing_fields:
                self.invoke("modelFieldAdd", modelName=note_type, fieldName=field_name)

        self.invoke("updateModelStyling", model={"name": note_type, "css": CARD_CSS})
        self.invoke(
            "updateModelTemplates",
            model={
                "name": note_type,
                "templates": {
                    "Galgame 語境卡": {
                        "Front": FRONT_TEMPLATE,
                        "Back": BACK_TEMPLATE,
                    }
                },
            },
        )
        return False

    def _read_local_collection_fsrs(self) -> bool | None:
        """透過 getMediaDirPath 定位本機 collection.anki2，以無鎖唯讀模式讀取真實的 FSRS 開關。"""
        try:
            media_dir = self.invoke("getMediaDirPath")
        except AnkiConnectError:
            return None
        if not media_dir:
            return None

        db_path = Path(str(media_dir)).parent / "collection.anki2"
        if not db_path.exists():
            return None

        uri = f"file:{quote(str(db_path), safe='/')}?mode=ro&immutable=1"
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(uri, uri=True)
            # Anki 23.10–26.x (schema 18+)：全域 FSRS 開關存在 config 資料表的 key='fsrs'
            try:
                row = conn.execute("SELECT val FROM config WHERE key = 'fsrs'").fetchone()
                if row is not None and row[0] is not None:
                    raw_val = row[0]
                    if isinstance(raw_val, (bytes, bytearray)):
                        text_val = raw_val.decode("utf-8", errors="ignore").strip().lower()
                    else:
                        text_val = str(raw_val).strip().lower()
                    if text_val == "true":
                        return True
                    if text_val == "false":
                        return False
            except sqlite3.Error:
                pass

            # 舊版相容：檢查 col.conf JSON
            try:
                row2 = conn.execute("SELECT conf FROM col").fetchone()
                if row2 and row2[0]:
                    conf = json.loads(row2[0])
                    if isinstance(conf, dict) and "fsrs" in conf:
                        return bool(conf["fsrs"])
            except (sqlite3.Error, ValueError):
                pass
        except sqlite3.Error:
            return None
        finally:
            if conn is not None:
                conn.close()
        return None

    def check_fsrs_status(self, deck_name: str) -> bool | None:
        """檢查 Anki 是否已開啟 FSRS（相容 Anki 23.10–26.x 全域設定與預設權重）。"""
        # 1. 優先讀取本機 Anki collection.anki2 的真實全域 FSRS 設定
        local_fsrs = self._read_local_collection_fsrs()
        if local_fsrs is not None:
            return local_fsrs

        # 2. 若無法讀取本機資料庫（例如遠端 AnkiConnect），再檢查 getDeckConfig
        try:
            cfg = self.invoke("getDeckConfig", deck=deck_name)
        except AnkiConnectError:
            return None
        if not isinstance(cfg, dict):
            return None
        if "fsrs" in cfg:
            return bool(cfg["fsrs"])
        # 注意：Anki 預設 FSRS 權重在 getDeckConfig 中會顯示為空陣列 []，
        # 因此只在有非空自訂權重時確定為 True，空陣列時回傳 None 避免誤報未啟用。
        for key in ("fsrsParams6", "fsrsParams5", "fsrsWeights"):
            if cfg.get(key):
                return True
        return None

    def one_click_initialize(
        self,
        deck_name: str,
        note_type: str = NOTE_TYPE_NAME,
    ) -> AnkiInitReport:
        """一鍵完成 Anki 環境檢查、牌組建立、三階段模板建立、FSRS 檢查與已知詞同步。"""
        report = AnkiInitReport(connected=False)
        try:
            ver = self.ping()
        except AnkiConnectError as exc:
            report.messages.append(str(exc))
            return report

        report.connected = True
        report.api_version = ver
        report.messages.append(f"✓ 已連線 AnkiConnect (v{ver})")

        self.ensure_deck(deck_name)
        report.deck_ready = True
        report.messages.append(f"✓ 牌組「{deck_name}」已就緒")

        created = self.ensure_note_type(note_type)
        report.model_ready = True
        report.model_created = created
        if created:
            report.messages.append(f"✓ 已建立三階段卡片模板「{note_type}」")
        else:
            report.messages.append(f"✓ 已驗證並同步卡片模板「{note_type}」")

        fsrs = self.check_fsrs_status(deck_name)
        report.fsrs_enabled = fsrs
        if fsrs is True:
            report.messages.append("✓ FSRS 記憶演算法已啟用")
        elif fsrs is False:
            report.messages.append("⚠ 偵測到 FSRS 尚未啟用：強烈建議在 Anki 牌組選項中開啟 FSRS（對應攻略重點）")
        else:
            report.messages.append("ℹ 請確認 Anki 牌組選項中的「FSRS」已開啟（原攻略極力推薦防爆倉）")

        known = self.fetch_known_words(deck_name)
        report.known_words_count = len(known)
        report.messages.append(f"✓ 已同步 {len(known)} 個既有學習詞彙（用於 1T 句子高亮與防重）")
        return report

    def fetch_known_words(self, deck_name: str | None = None, limit: int = 8000) -> set[str]:
        """從 Anki 牌組同步已建卡的單詞，用於 1T (i+1) 未知詞判定。"""
        query = f'deck:"{deck_name}"' if deck_name else "deck:*"
        try:
            note_ids = self.invoke("findNotes", query=query)
        except AnkiConnectError:
            return set()
        if not note_ids:
            return set()

        note_ids = note_ids[:limit]
        known: set[str] = set()
        # 分批查詢避免單次請求過大
        for start in range(0, len(note_ids), 500):
            batch = note_ids[start : start + 500]
            infos = self.invoke("notesInfo", notes=batch)
            for info in infos:
                fields = info.get("fields", {})
                # 支援我們自家模板以及常見 Yomitan / 基礎模板欄位名
                for candidate_key in ("TargetWord", "Word", "Expression", "Vocab", "正面", "Front"):
                    val_obj = fields.get(candidate_key)
                    if val_obj and val_obj.get("value"):
                        clean = _strip_html(str(val_obj["value"])).strip()
                        # 只收錄長度合理像單詞的欄位（避免把整句當成單詞）
                        if clean and len(clean) <= 16 and " " not in clean:
                            known.add(clean)
                            break
        return known

    def get_today_mined_count(self, deck_name: str) -> int:
        """取得今日在指定牌組已建立的卡片數。"""
        try:
            ids = self.invoke("findNotes", query=f'deck:"{deck_name}" added:1')
            return len(ids) if isinstance(ids, list) else 0
        except AnkiConnectError:
            return 0

    def find_duplicates(self, deck_name: str, target_word: str) -> list[int]:
        """檢查牌組中是否已存在相同目標詞。"""
        escaped = target_word.replace('"', '\\"')
        query = f'deck:"{deck_name}" "TargetWord:{escaped}"'
        try:
            ids = self.invoke("findNotes", query=query)
            return list(ids) if isinstance(ids, list) else []
        except AnkiConnectError:
            return []

    def store_media_bytes(self, filename: str, data: bytes) -> str:
        encoded = base64.b64encode(data).decode("ascii")
        return str(self.invoke("storeMediaFile", filename=filename, data=encoded))

    def add_card(
        self,
        deck_name: str,
        note_type: str,
        card: AnkiCardPayload,
        allow_duplicate: bool = True,
    ) -> int:
        """一鍵將卡片（含可選語音與截圖）寫入 Anki，回傳 note_id。"""
        audio_field = ""
        if card.audio_bytes and card.audio_filename:
            stored = self.store_media_bytes(card.audio_filename, card.audio_bytes)
            audio_field = f"[sound:{stored}]"

        screenshot_field = ""
        if card.screenshot_bytes and card.screenshot_filename:
            stored = self.store_media_bytes(card.screenshot_filename, card.screenshot_bytes)
            screenshot_field = f'<img src="{stored}">'

        tags = ["galgame-anki", f"stage-{card.stage_mode}"]
        if card.source_game:
            safe_game = re.sub(r"\s+", "_", card.source_game.strip())
            if safe_game:
                tags.append(f"game::{safe_game}")
        for t in card.tags:
            if t and t not in tags:
                tags.append(t)

        pitch_graph_html = card.pitch_graph or render_pitch_graphs_html(
            reading=card.target_reading,
            pitches=card.pitch_accent,
            use_css_colors=True,
        )

        note = {
            "deckName": deck_name,
            "modelName": note_type,
            "fields": {
                "TargetWord": card.target_word,
                "TargetReading": card.target_reading,
                "PitchAccent": card.pitch_accent,
                "PitchGraph": pitch_graph_html,
                "Sentence": card.sentence,
                "SentenceFurigana": card.sentence_furigana,
                "SentenceTranslation": card.sentence_translation,
                "DefinitionBilingual": card.definition_bilingual,
                "DefinitionMonolingual": card.definition_monolingual,
                "SentenceAudio": audio_field,
                "Screenshot": screenshot_field,
                "AIGrammarNotes": card.ai_grammar_notes,
                "RelatedWords": card.related_words_html,
                "SourceGame": card.source_game,
                "StageMode": card.stage_mode,
            },
            "options": {
                "allowDuplicate": allow_duplicate,
                "duplicateScope": "deck",
            },
            "tags": tags,
        }
        note_id = self.invoke("addNote", note=note)
        return int(note_id)

    def update_ai_grammar_notes(self, note_id: int, ai_html: str) -> None:
        """非同步背景完成 AI 拆解後，自動回填至既有的 Anki 卡片。"""
        self.invoke(
            "updateNoteFields",
            note={
                "id": note_id,
                "fields": {"AIGrammarNotes": ai_html},
            },
        )

    def delete_notes(self, note_ids: list[int]) -> None:
        """刪除剛寫入的 Anki 卡片（供「一鍵直寫 → ↩ 撤銷」功能使用）。"""
        ids = [int(nid) for nid in note_ids if int(nid) > 0]
        if ids:
            self.invoke("deleteNotes", notes=ids)


def _strip_html(raw: str) -> str:
    return re.sub(r"<[^>]+>", "", raw)


def _anki_addons_candidates() -> list[Path]:
    return [
        Path.home() / ".local" / "share" / "Anki2" / "addons21",
        Path.home() / ".var" / "app" / "net.ankiweb.Anki" / "data" / "Anki2" / "addons21",
        Path.home() / "AppData" / "Roaming" / "Anki2" / "addons21",
    ]


def detect_local_anki_addons_dir() -> Path | None:
    """偵測 Linux / Flatpak / Windows 本機 Anki2 addons21 資料夾路徑。"""
    for path in _anki_addons_candidates():
        if (path / ANKICONNECT_ADDON_CODE).exists():
            return path
    for path in _anki_addons_candidates():
        if path.exists():
            return path
    return None


def is_ankiconnect_installed_locally() -> bool:
    return any((p / ANKICONNECT_ADDON_CODE).exists() for p in _anki_addons_candidates())


def detect_anki_launch_command() -> list[str] | None:
    """偵測本機 Anki 啟動指令（Linux 優先支援 Flatpak net.ankiweb.Anki 與原生 anki）。"""
    if os.name == "nt":
        exe = shutil.which("anki") or shutil.which("anki.exe")
        if exe:
            return [exe]
        local_app = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Anki" / "anki.exe"
        if local_app.exists():
            return [str(local_app)]
        return None

    # Linux: 優先檢查 Flatpak net.ankiweb.Anki
    flatpak = shutil.which("flatpak")
    if flatpak:
        if (Path.home() / ".var" / "app" / "net.ankiweb.Anki").exists():
            return [flatpak, "run", "net.ankiweb.Anki"]
        try:
            res = subprocess.run(
                [flatpak, "info", "net.ankiweb.Anki"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
                check=False,
            )
            if res.returncode == 0:
                return [flatpak, "run", "net.ankiweb.Anki"]
        except (OSError, subprocess.SubprocessError):
            pass

    native = shutil.which("anki")
    if native:
        return [native]
    return None


def launch_anki_background(minimize_on_wayland: bool = True) -> tuple[bool, str]:
    """在背景啟動 Anki（並於 KDE Plasma Wayland 自動最小化一次，不佔筆電螢幕）。"""
    if os.environ.get("QT_QPA_PLATFORM", "").lower() in ("offscreen", "minimal"):
        return False, "測試模式略過自動啟動 Anki"

    cmd = detect_anki_launch_command()
    if not cmd:
        return False, "本機未偵測到 Anki（請確認已安裝 Flatpak 或原生 Anki）"

    if minimize_on_wayland:
        try:
            from galgame_anki.adapters.window_pin import minimize_anki_once_kwin

            minimize_anki_once_kwin(window_ms=18000)
        except Exception:
            pass

    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True, f"已於背景啟動 Anki（{' '.join(cmd)}）並自動最小化"
    except OSError as exc:
        return False, f"啟動 Anki 失敗：{exc}"
