"""系統化學習藍圖進度持久化（自動紀錄在遊戲/影片中遇見與掌握的 N5→N1 語法與閱讀量）。"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from galgame_anki.config import get_data_dir
from galgame_anki.core.grammar_engine import GrammarHit


class LearningProgressStore:
    """本地 SQLite 追蹤個人 N5→N1 語法藍圖點亮進度、待拆解難句隊列與累計閱讀句數。"""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or (get_data_dir() / "learning_progress.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=NORMAL;
                PRAGMA busy_timeout=5000;

                CREATE TABLE IF NOT EXISTS grammar_progress (
                    pattern_id TEXT PRIMARY KEY,
                    level TEXT NOT NULL,
                    seen_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'new', -- new | seen | learning | mastered
                    last_example TEXT NOT NULL DEFAULT '',
                    last_source TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS study_examples (
                    topic TEXT NOT NULL, lemma TEXT NOT NULL, sentence TEXT NOT NULL,
                    excerpt TEXT NOT NULL, saved_at REAL NOT NULL,
                    PRIMARY KEY(topic, lemma, sentence)
                );
                CREATE TABLE IF NOT EXISTS study_stats (
                    key TEXT PRIMARY KEY,
                    value INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS study_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sentence TEXT NOT NULL,
                    focus_surface TEXT NOT NULL DEFAULT '',
                    focus_lemma TEXT NOT NULL DEFAULT '',
                    source_name TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending', -- pending | done
                    created_at REAL NOT NULL DEFAULT 0,
                    UNIQUE(sentence, focus_surface)
                );
                """
            )
            self._conn.commit()

    def keep_study_example(self, topic: str, lemma: str, sentence: str, excerpt: str) -> None:
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO study_examples VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(topic, lemma, sentence) DO UPDATE SET "
                    "excerpt=excluded.excerpt, saved_at=excluded.saved_at",
                    (topic, lemma, sentence, excerpt, time.time()),
                )

    def study_examples(self, topic: str):
        with self._lock:
            return self._conn.execute(
                "SELECT lemma, sentence, excerpt FROM study_examples WHERE topic=? "
                "ORDER BY saved_at DESC",
                (topic,),
            ).fetchall()

    def enqueue_study_sentence(
        self,
        sentence: str,
        focus_surface: str = "",
        focus_lemma: str = "",
        source_name: str = "",
    ) -> int:
        """把遊玩或看番時遇到的難句／想研究變形的句子暫存到「待拆解隊列」，玩完再系統拆解。"""
        clean_sent = (sentence or "").strip()
        if not clean_sent:
            return 0
        now = time.time()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO study_queue (sentence, focus_surface, focus_lemma, source_name, status, created_at)
                VALUES (?, ?, ?, ?, 'pending', ?)
                ON CONFLICT(sentence, focus_surface) DO UPDATE SET
                    focus_lemma = CASE WHEN excluded.focus_lemma != '' THEN excluded.focus_lemma ELSE focus_lemma END,
                    source_name = CASE WHEN excluded.source_name != '' THEN excluded.source_name ELSE source_name END,
                    status = 'pending',
                    created_at = excluded.created_at
                """,
                (clean_sent, (focus_surface or "").strip(), (focus_lemma or "").strip(), (source_name or "").strip(), now),
            )
            self._conn.commit()
            row = cur.execute(
                "SELECT id FROM study_queue WHERE sentence = ? AND focus_surface = ?",
                (clean_sent, (focus_surface or "").strip()),
            ).fetchone()
            return int(row["id"]) if row else 0

    def list_study_queue(self, include_done: bool = False, limit: int = 60) -> list[dict[str, Any]]:
        """列出待拆解難句隊列（最新加入優先）。"""
        with self._lock:
            if include_done:
                rows = self._conn.execute(
                    "SELECT id, sentence, focus_surface, focus_lemma, source_name, status, created_at "
                    "FROM study_queue ORDER BY CASE WHEN status='pending' THEN 0 ELSE 1 END, created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT id, sentence, focus_surface, focus_lemma, source_name, status, created_at "
                    "FROM study_queue WHERE status = 'pending' ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    def mark_study_queue_done(self, item_id: int, done: bool = True) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE study_queue SET status = ? WHERE id = ?",
                ("done" if done else "pending", int(item_id)),
            )
            self._conn.commit()

    def remove_study_queue_item(self, item_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM study_queue WHERE id = ?", (int(item_id),))
            self._conn.commit()

    def record_sentence_seen(self, hits: list[GrammarHit], sentence: str, source_name: str = "") -> None:
        """每讀到一句台詞（Galgame / mpv），自動點亮對應語法節點並留存真實例句。"""
        now = time.time()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO study_stats (key, value) VALUES ('sentences_read', 1)
                ON CONFLICT(key) DO UPDATE SET value = value + 1
                """
            )
            for h in hits:
                existing = cur.execute(
                    "SELECT seen_count, status FROM grammar_progress WHERE pattern_id = ?",
                    (h.pattern_id,),
                ).fetchone()
                if existing is None:
                    cur.execute(
                        """
                        INSERT INTO grammar_progress
                        (pattern_id, level, seen_count, status, last_example, last_source, updated_at)
                        VALUES (?, ?, 1, 'seen', ?, ?, ?)
                        """,
                        (h.pattern_id, h.level, sentence, source_name, now),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE grammar_progress
                        SET seen_count = seen_count + 1,
                            last_example = ?,
                            last_source = CASE WHEN ? != '' THEN ? ELSE last_source END,
                            updated_at = ?
                        WHERE pattern_id = ?
                        """,
                        (sentence, source_name, source_name, now, h.pattern_id),
                    )
            self._conn.commit()

    def set_grammar_status(self, pattern_id: str, level: str, status: str) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO grammar_progress (pattern_id, level, seen_count, status, updated_at)
                VALUES (?, ?, 0, ?, ?)
                ON CONFLICT(pattern_id) DO UPDATE SET status = ?, updated_at = ?
                """,
                (pattern_id, level, status, now, status, now),
            )
            self._conn.commit()

    def get_all_grammar_progress(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM grammar_progress").fetchall()
            return {r["pattern_id"]: dict(r) for r in rows}

    def get_sentences_read(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM study_stats WHERE key = 'sentences_read'"
            ).fetchone()
            return int(row["value"]) if row else 0

