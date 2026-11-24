"""解包遊戲劇本↔純淨聲優人聲對照引擎（0% BGM、0% 音效、支援 Kirikiri XP3 直讀與已解包資料夾）。

核心能力：
1. **Kirikiri XP3 (.xp3 + CxDec + PSB .scn) 免拆滿硬碟直讀索引**：
   直接掃描遊戲目錄中的 `.xp3` 封包建立 SQLite 對照索引（僅佔約 2 MB），
   製卡或試聽時即時從封包抽出單一 `.opus` / `.ogg` / `.wav` 聲優原聲並轉成乾淨 `.mp3`。
2. **通用已解包資料夾掛載**：
   支援透過 GARbro / FreeMote / KrkrExtract 解包後的目錄（`.scn.json`、`.scn`、`.ks` 與 `.ogg`/`.opus`/`.wav`/`.mp3`）。
3. **劇情進度游標追蹤防撞車**：
   遇到「……うん」「はい」等重複短句時，優先比對距離目前遊玩進度最近的那一句，避免配錯音檔。
4. **區分旁白與未配音句子**：
   命中劇本但該句為旁白無語音時，明確回報 `has_voice=False`（避免誤錄純 BGM）。
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from galgame_anki.adapters.xp3_psb import (
    CxDecryptor,
    CxSchemeParams,
    PsbDialogueEntry,
    Xp3EntryMeta,
    clean_psb_dialogue_text,
    ensure_formats_dat,
    extract_dialogues_from_psb_obj,
    find_cx_scheme_for_game,
    list_xp3_entries,
    parse_psb_tree,
    read_xp3_entry_bytes,
)
from galgame_anki.config import get_data_dir

AUDIO_EXTS = {".opus", ".ogg", ".wav", ".mp3", ".flac", ".m4a"}
_STRIP_RUBY_KS_RE = re.compile(r"\[[^\]]*\]")
_WS_RE = re.compile(r"[\s\u3000・]+")
_OUTER_QUOTE_RE = re.compile(r"^[「『（(【\[]+|[」』）)】\]]+$")
_KS_VOICE_RE = re.compile(
    r"""(?:@(?:vo|voice|playvoice)\s+(?:[^\n]*?\b(?:s|storage|file)\s*=\s*["']?([A-Za-z0-9_#.-]+)["']?|([A-Za-z0-9_#.-]+))|\[(?:vo|voice|playvoice)\b[^\]]*?(?:s|storage|file)\s*=\s*["']?([A-Za-z0-9_#.-]+)["']?[^\]]*\])""",
    re.IGNORECASE,
)


def default_voice_packs_db_path() -> Path:
    d = get_data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / "voice_packs.db"


def normalize_dialogue_for_match(text: str) -> tuple[str, str]:
    """回傳 `(norm_exact, norm_core)` 以兼容有／無書名號、換行、說話者前綴與注音標記的台詞比對。"""
    cleaned = clean_psb_dialogue_text(text)
    cleaned = _WS_RE.sub("", cleaned)
    if not cleaned:
        return "", ""

    # 若 Hook 連帶抓到「キャサリン「起きた？」」形式，抽取引號主體作為核心比對鍵
    core = cleaned
    for q_open, q_close in (("「", "」"), ("『", "』"), ("（", "）")):
        qi = core.find(q_open)
        if 0 < qi <= 16 and core.endswith(q_close):
            core = core[qi:]
            break
    core = _OUTER_QUOTE_RE.sub("", core).strip("…・、。！？!?")
    return cleaned, core or cleaned


@dataclass
class VoicePackSummary:
    pack_id: str
    game_name: str
    game_dir: str
    mode: str  # xp3_cx | unpacked
    line_count: int
    voiced_line_count: int
    asset_count: int
    created_at: float


@dataclass
class MatchedVoiceResult:
    matched_line: bool
    has_voice: bool = False
    pack_id: str = ""
    pack_title: str = ""
    seq_index: int = -1
    speaker: str = ""
    voice_ids: list[str] = field(default_factory=list)
    translation: str = ""
    audio_clip: tuple[bytes, str] | None = None


def _encode_audio_bytes_to_mp3(audio_bytes: bytes, ext: str = ".ogg") -> bytes | None:
    """將單一或串流語音位元組轉為 24kHz mono 64kbps MP3（支援 Opus/Ogg/WAV）。"""
    if not audio_bytes:
        return None
    suffix = ext.lower() if ext.startswith(".") else f".{ext.lower()}"
    if suffix == ".opus":
        # FFmpeg 對 Ogg-Opus 容器以副檔名 .opus / .ogg 皆可直接解析
        fmt_hint = ".opus"
    else:
        fmt_hint = suffix

    with tempfile.NamedTemporaryFile(prefix="galanki_vp_", suffix=fmt_hint, delete=True) as tf:
        tf.write(audio_bytes)
        tf.flush()
        cmd = [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            tf.name,
            "-ac",
            "1",
            "-ar",
            "24000",
            "-b:a",
            "64k",
            "-f",
            "mp3",
            "pipe:1",
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, timeout=15, check=False)
            if res.returncode == 0 and len(res.stdout) > 256:
                return res.stdout
        except (OSError, subprocess.SubprocessError):
            return None
    return None


def _mix_multi_audio_to_mp3(items: list[tuple[bytes, str]]) -> bytes | None:
    """合聲台詞專用：把多個角色純人聲混音為單一 24kHz mono MP3。"""
    if not items:
        return None
    if len(items) == 1:
        return _encode_audio_bytes_to_mp3(items[0][0], items[0][1])

    tmp_files: list[Path] = []
    try:
        cmd: list[str] = ["ffmpeg", "-y", "-v", "error"]
        for raw_b, ext in items[:4]:
            suffix = ext if ext.startswith(".") else f".{ext}"
            fd, p_str = tempfile.mkstemp(prefix="galanki_mix_", suffix=suffix)
            p = Path(p_str)
            with open(fd, "wb") as fh:
                fh.write(raw_b)
            tmp_files.append(p)
            cmd.extend(["-i", str(p)])
        n_in = len(tmp_files)
        cmd.extend(
            [
                "-filter_complex",
                f"amix=inputs={n_in}:duration=longest:normalize=0",
                "-ac",
                "1",
                "-ar",
                "24000",
                "-b:a",
                "64k",
                "-f",
                "mp3",
                "pipe:1",
            ]
        )
        res = subprocess.run(cmd, capture_output=True, timeout=20, check=False)
        if res.returncode == 0 and len(res.stdout) > 256:
            return res.stdout
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        for p in tmp_files:
            p.unlink(missing_ok=True)
    return _encode_audio_bytes_to_mp3(items[0][0], items[0][1])


def _extract_dialogues_from_ks(text: str, scene_file: str, start_seq: int = 0) -> list[PsbDialogueEntry]:
    """從明文 Kirikiri `.ks` 腳本解析台詞與緊鄰的語音標籤。"""
    entries: list[PsbDialogueEntry] = []
    seq = start_seq
    pending_voices: list[str] = []
    buf: list[str] = []

    def _flush() -> None:
        nonlocal seq, pending_voices, buf
        joined = "".join(buf).strip()
        buf = []
        if not joined:
            return
        clean = _STRIP_RUBY_KS_RE.sub("", joined)
        clean = re.sub(r"\s+", "", clean)
        if clean:
            entries.append(
                PsbDialogueEntry(
                    seq_index=seq,
                    scene_file=scene_file,
                    speaker="",
                    raw_text=joined,
                    clean_text=clean,
                    voice_ids=list(pending_voices),
                )
            )
            seq += 1
        pending_voices = []

    for raw_ln in text.splitlines():
        ln = raw_ln.strip()
        if not ln or ln.startswith(";"):
            continue
        for m in _KS_VOICE_RE.finditer(ln):
            raw_vid = m.group(1) or m.group(2) or m.group(3)
            if raw_vid:
                vid = Path(raw_vid).stem
                if vid:
                    pending_voices.append(vid)
        if ln.startswith("*") or ln.startswith("@"):
            continue
        # 移除行內標籤後檢查是否剩餘台詞文字
        stripped = _STRIP_RUBY_KS_RE.sub("", ln).strip()
        if stripped:
            buf.append(stripped)
            if "[p" in ln or "[l" in ln or "[plc" in ln:
                _flush()
        elif "[p" in ln or "[plc" in ln:
            _flush()
    _flush()
    return entries


class VoicePackManager:
    """管理所有遊戲的純淨人聲索引（SQLite 持久化）＋ 執行期語音配對與擷取。"""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or default_voice_packs_db_path()
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        self._cursors: dict[str, int] = {}
        self._decryptor_cache: dict[str, CxDecryptor | None] = {}

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS voice_packs (
                pack_id TEXT PRIMARY KEY,
                game_name TEXT NOT NULL,
                game_dir TEXT NOT NULL,
                mode TEXT NOT NULL,
                scheme_json TEXT DEFAULT '',
                line_count INTEGER DEFAULT 0,
                voiced_line_count INTEGER DEFAULT 0,
                asset_count INTEGER DEFAULT 0,
                created_at REAL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS voice_assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pack_id TEXT NOT NULL,
                stem_lower TEXT NOT NULL,
                ext TEXT NOT NULL,
                archive_path TEXT DEFAULT '',
                internal_name TEXT DEFAULT '',
                disk_path TEXT DEFAULT '',
                adler32 INTEGER DEFAULT 0,
                flags INTEGER DEFAULT 0,
                orig_size INTEGER DEFAULT 0,
                segments_json TEXT DEFAULT '[]'
            );
            CREATE INDEX IF NOT EXISTS idx_assets_pack_stem ON voice_assets(pack_id, stem_lower);

            CREATE TABLE IF NOT EXISTS dialogue_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pack_id TEXT NOT NULL,
                seq_index INTEGER NOT NULL,
                scene_file TEXT DEFAULT '',
                speaker TEXT DEFAULT '',
                raw_text TEXT NOT NULL,
                clean_text TEXT NOT NULL,
                norm_exact TEXT NOT NULL,
                norm_core TEXT NOT NULL,
                voice_ids_json TEXT DEFAULT '[]',
                translation TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_lines_pack_exact ON dialogue_lines(pack_id, norm_exact);
            CREATE INDEX IF NOT EXISTS idx_lines_pack_core ON dialogue_lines(pack_id, norm_core);
            """
        )
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def compute_pack_id(game_dir: Path) -> str:
        return hashlib.sha1(str(game_dir.resolve()).encode("utf-8")).hexdigest()[:16]

    def list_packs(self) -> list[VoicePackSummary]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT pack_id, game_name, game_dir, mode, line_count, voiced_line_count, asset_count, created_at "
                "FROM voice_packs ORDER BY created_at DESC"
            ).fetchall()
            return [
                VoicePackSummary(
                    pack_id=r["pack_id"],
                    game_name=r["game_name"],
                    game_dir=r["game_dir"],
                    mode=r["mode"],
                    line_count=r["line_count"],
                    voiced_line_count=r["voiced_line_count"],
                    asset_count=r["asset_count"],
                    created_at=r["created_at"],
                )
                for r in rows
            ]

    def delete_pack(self, pack_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM dialogue_lines WHERE pack_id = ?", (pack_id,))
            self._conn.execute("DELETE FROM voice_assets WHERE pack_id = ?", (pack_id,))
            self._conn.execute("DELETE FROM voice_packs WHERE pack_id = ?", (pack_id,))
            self._conn.commit()
            self._cursors.pop(pack_id, None)
            self._decryptor_cache.pop(pack_id, None)

    def resolve_pack_for_game(
        self,
        game_name: str = "",
        exe_path: str = "",
    ) -> VoicePackSummary | None:
        """根據當前遊戲名稱或執行檔路徑自動配對已建立的純人聲索引包。"""
        packs = self.list_packs()
        if not packs:
            return None
        if exe_path:
            exe_dir = str(Path(exe_path).resolve().parent)
            for p in packs:
                if Path(p.game_dir).resolve() == Path(exe_dir):
                    return p
        if game_name:
            gn_norm = re.sub(r"[\W_]+", "", game_name.lower())
            for p in packs:
                pn_norm = re.sub(r"[\W_]+", "", p.game_name.lower())
                dn_norm = re.sub(r"[\W_]+", "", Path(p.game_dir).name.lower())
                if gn_norm and (gn_norm in pn_norm or pn_norm in gn_norm or gn_norm in dn_norm or dn_norm in gn_norm):
                    return p
        return None

    def reset_cursor(self, pack_id: str) -> None:
        self._cursors[pack_id] = 0

    def detect_game_dir_capabilities(self, game_dir: Path) -> dict[str, Any]:
        """檢查資料夾是否包含 Kirikiri `.xp3` 封包或已解包劇本與語音。"""
        xp3_files = sorted(game_dir.glob("*.xp3")) if game_dir.is_dir() else []
        loose_scripts = []
        loose_audio_cnt = 0
        if game_dir.is_dir():
            for p in game_dir.rglob("*"):
                if not p.is_file():
                    continue
                suf = p.suffix.lower()
                if suf in AUDIO_EXTS:
                    loose_audio_cnt += 1
                elif p.name.lower().endswith(".scn.json") or suf in (".scn", ".ks"):
                    loose_scripts.append(p)
        scheme = find_cx_scheme_for_game(game_dir) if xp3_files else None
        return {
            "xp3_count": len(xp3_files),
            "xp3_names": [x.name for x in xp3_files],
            "loose_script_count": len(loose_scripts),
            "loose_audio_count": loose_audio_cnt,
            "cx_scheme_title": scheme.title if scheme else "",
        }

    def index_game_directory(
        self,
        game_dir: Path,
        formats_dat_path: Path | None = None,
        progress_cb: Callable[[str], None] | None = None,
    ) -> VoicePackSummary:
        """一鍵對遊戲目錄建立「劇本台詞 ↔ 純淨人聲」索引（支援 XP3 直讀與已解包目錄）。"""
        game_dir = game_dir.resolve()
        if not game_dir.is_dir():
            raise FileNotFoundError(f"找不到遊戲目錄：{game_dir}")

        xp3_files = sorted(game_dir.glob("*.xp3"))
        if xp3_files:
            return self._index_xp3_game(game_dir, xp3_files, formats_dat_path, progress_cb)
        return self._index_unpacked_dir(game_dir, progress_cb)

    def _index_xp3_game(
        self,
        game_dir: Path,
        xp3_files: list[Path],
        formats_dat_path: Path | None,
        progress_cb: Callable[[str], None] | None,
    ) -> VoicePackSummary:
        scheme = find_cx_scheme_for_game(game_dir, formats_dat_path=formats_dat_path)
        if scheme is None:
            if progress_cb:
                progress_cb("尚未在本機找到該遊戲的解密參數，嘗試檢查／下載 Formats.dat...")
            dat_file = ensure_formats_dat(formats_dat_path, progress_cb=progress_cb)
            scheme = find_cx_scheme_for_game(game_dir, formats_dat_path=dat_file)

        decryptor: CxDecryptor | None = CxDecryptor(scheme) if scheme is not None else None
        scheme_json = ""
        if scheme is not None:
            scheme_json = json.dumps(
                {
                    "title": scheme.title,
                    "mask": scheme.mask,
                    "offset": scheme.offset,
                    "prolog_order": scheme.prolog_order,
                    "odd_branch_order": scheme.odd_branch_order,
                    "even_branch_order": scheme.even_branch_order,
                    "control_block": scheme.control_block,
                },
                ensure_ascii=False,
            )

        assets: list[tuple[str, str, str, str, int, int, int, str]] = []
        script_metas: list[Xp3EntryMeta] = []

        for xp3 in xp3_files:
            if progress_cb:
                progress_cb(f"正在掃描封包索引：{xp3.name}...")
            try:
                entries = list_xp3_entries(xp3)
            except Exception:
                continue
            for m in entries:
                suf = Path(m.internal_name).suffix.lower()
                if suf in AUDIO_EXTS:
                    assets.append(
                        (
                            Path(m.internal_name).stem.lower(),
                            suf,
                            m.archive_path,
                            m.internal_name,
                            m.adler32,
                            m.flags,
                            m.orig_size,
                            json.dumps(m.segments),
                        )
                    )
                elif suf in (".scn", ".ks"):
                    script_metas.append(m)

        if not script_metas:
            # 若 .xp3 內無劇本（例如加密或外部腳本），嘗試回退掃描目錄下已解包檔案
            return self._index_unpacked_dir(game_dir, progress_cb)

        # 優先讀取 .scn (PSB 完整劇情腳本)，若全遊戲無 .scn 再讀取 .ks
        scn_metas = sorted((m for m in script_metas if m.internal_name.lower().endswith(".scn")), key=lambda x: x.internal_name)
        chosen_metas = scn_metas if scn_metas else sorted(script_metas, key=lambda x: x.internal_name)

        lines: list[PsbDialogueEntry] = []
        seq_cursor = 0
        for idx, sm in enumerate(chosen_metas, start=1):
            if progress_cb and (idx == 1 or idx % 5 == 0 or idx == len(chosen_metas)):
                progress_cb(f"正在解析劇本與語音關聯 ({idx}/{len(chosen_metas)})：{sm.internal_name}")
            raw_bytes = read_xp3_entry_bytes(sm, decryptor=decryptor)
            if sm.internal_name.lower().endswith(".scn"):
                try:
                    tree = parse_psb_tree(raw_bytes)
                    extracted = extract_dialogues_from_psb_obj(tree, sm.internal_name, start_seq=seq_cursor)
                    lines.extend(extracted)
                    seq_cursor += len(extracted)
                except Exception:
                    continue
            else:
                txt = raw_bytes.decode("utf-16le", errors="ignore") if raw_bytes.startswith(b"\xff\xfe") else raw_bytes.decode("utf-8", errors="ignore")
                extracted = _extract_dialogues_from_ks(txt, sm.internal_name, start_seq=seq_cursor)
                lines.extend(extracted)
                seq_cursor += len(extracted)

        if not lines:
            raise RuntimeError(
                f"在 {game_dir.name} 的 XP3 中無法解析到日文劇本（可能需要其他解密外掛，或請改用 GARbro/FreeMote 解包資料夾）。"
            )

        game_name = scheme.title if scheme else game_dir.name
        return self._save_pack(
            game_name=game_name,
            game_dir=game_dir,
            mode="xp3_cx",
            scheme_json=scheme_json,
            assets=assets,
            lines=lines,
        )

    def _index_unpacked_dir(
        self,
        game_dir: Path,
        progress_cb: Callable[[str], None] | None,
    ) -> VoicePackSummary:
        if progress_cb:
            progress_cb(f"正在掃描已解包資料夾：{game_dir.name}...")

        assets: list[tuple[str, str, str, str, int, int, int, str]] = []
        scn_json_files: list[Path] = []
        scn_files: list[Path] = []
        ks_files: list[Path] = []

        for p in sorted(game_dir.rglob("*")):
            if not p.is_file():
                continue
            low = p.name.lower()
            suf = p.suffix.lower()
            if suf in AUDIO_EXTS:
                assets.append((p.stem.lower(), suf, str(p), p.name, 0, 0, p.stat().st_size, "[]"))
            elif low.endswith(".scn.json") or (suf == ".json" and "scn" in low):
                scn_json_files.append(p)
            elif suf == ".scn":
                scn_files.append(p)
            elif suf == ".ks":
                ks_files.append(p)

        lines: list[PsbDialogueEntry] = []
        seq = 0
        for p in scn_json_files:
            try:
                obj = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
                extracted = extract_dialogues_from_psb_obj(obj, p.name, start_seq=seq)
                lines.extend(extracted)
                seq += len(extracted)
            except Exception:
                continue

        if not lines:
            for p in scn_files:
                try:
                    obj = parse_psb_tree(p.read_bytes())
                    extracted = extract_dialogues_from_psb_obj(obj, p.name, start_seq=seq)
                    lines.extend(extracted)
                    seq += len(extracted)
                except Exception:
                    continue

        if not lines:
            for p in ks_files:
                try:
                    raw = p.read_bytes()
                    txt = raw.decode("utf-16le", errors="ignore") if raw.startswith(b"\xff\xfe") else raw.decode("utf-8", errors="ignore")
                    extracted = _extract_dialogues_from_ks(txt, p.name, start_seq=seq)
                    lines.extend(extracted)
                    seq += len(extracted)
                except Exception:
                    continue

        if not lines or not assets:
            raise RuntimeError(
                f"資料夾內缺少可用的劇本或語音檔（找到劇本句 {len(lines)} 筆、語音檔 {len(assets)} 筆）。\n"
                "請選擇包含 `.xp3` 封包的遊戲本體目錄，或已解包出 `.scn` / `.scn.json` / `.ks` ＋ `.ogg`/`.opus` 的資料夾。"
            )

        return self._save_pack(
            game_name=game_dir.name,
            game_dir=game_dir,
            mode="unpacked",
            scheme_json="",
            assets=assets,
            lines=lines,
        )

    def _save_pack(
        self,
        game_name: str,
        game_dir: Path,
        mode: str,
        scheme_json: str,
        assets: list[tuple[str, str, str, str, int, int, int, str]],
        lines: list[PsbDialogueEntry],
    ) -> VoicePackSummary:
        pack_id = self.compute_pack_id(game_dir)
        now = time.time()
        voiced_cnt = sum(1 for ln in lines if ln.voice_ids)

        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM dialogue_lines WHERE pack_id = ?", (pack_id,))
            cur.execute("DELETE FROM voice_assets WHERE pack_id = ?", (pack_id,))
            cur.execute(
                """
                INSERT OR REPLACE INTO voice_packs
                    (pack_id, game_name, game_dir, mode, scheme_json, line_count, voiced_line_count, asset_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (pack_id, game_name, str(game_dir), mode, scheme_json, len(lines), voiced_cnt, len(assets), now),
            )

            asset_rows = []
            for stem_l, ext, arc_or_disk, iname, adlr, flags, osz, segs_json in assets:
                if mode == "xp3_cx":
                    asset_rows.append((pack_id, stem_l, ext, arc_or_disk, iname, "", adlr, flags, osz, segs_json))
                else:
                    asset_rows.append((pack_id, stem_l, ext, "", iname, arc_or_disk, 0, 0, osz, "[]"))

            cur.executemany(
                """
                INSERT INTO voice_assets
                    (pack_id, stem_lower, ext, archive_path, internal_name, disk_path, adler32, flags, orig_size, segments_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                asset_rows,
            )

            line_rows = []
            for ln in lines:
                n_exact, n_core = normalize_dialogue_for_match(ln.clean_text)
                if not n_exact:
                    continue
                line_rows.append(
                    (
                        pack_id,
                        ln.seq_index,
                        ln.scene_file,
                        ln.speaker,
                        ln.raw_text,
                        ln.clean_text,
                        n_exact,
                        n_core,
                        json.dumps(ln.voice_ids, ensure_ascii=False),
                        ln.translation,
                    )
                )
            cur.executemany(
                """
                INSERT INTO dialogue_lines
                    (pack_id, seq_index, scene_file, speaker, raw_text, clean_text, norm_exact, norm_core, voice_ids_json, translation)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                line_rows,
            )
            self._conn.commit()
            self._decryptor_cache.pop(pack_id, None)
            self._cursors[pack_id] = 0

        return VoicePackSummary(
            pack_id=pack_id,
            game_name=game_name,
            game_dir=str(game_dir),
            mode=mode,
            line_count=len(lines),
            voiced_line_count=voiced_cnt,
            asset_count=len(assets),
            created_at=now,
        )

    def _get_decryptor(self, pack_id: str) -> CxDecryptor | None:
        with self._lock:
            if pack_id in self._decryptor_cache:
                return self._decryptor_cache[pack_id]
            row = self._conn.execute("SELECT scheme_json FROM voice_packs WHERE pack_id = ?", (pack_id,)).fetchone()
            dec: CxDecryptor | None = None
            if row and row["scheme_json"]:
                try:
                    d = json.loads(row["scheme_json"])
                    dec = CxDecryptor(
                        CxSchemeParams(
                            title=d["title"],
                            mask=int(d["mask"]),
                            offset=int(d["offset"]),
                            prolog_order=list(d["prolog_order"]),
                            odd_branch_order=list(d["odd_branch_order"]),
                            even_branch_order=list(d["even_branch_order"]),
                            control_block=list(d["control_block"]),
                        )
                    )
                except Exception:
                    dec = None
            self._decryptor_cache[pack_id] = dec
            return dec

    def _pick_best_candidate(
        self,
        pack_id: str,
        rows: list[sqlite3.Row],
    ) -> sqlite3.Row:
        if len(rows) == 1:
            return rows[0]
        cursor = self._cursors.get(pack_id, 0)

        def _score(r: sqlite3.Row) -> tuple[int, int, int]:
            seq = int(r["seq_index"])
            has_v = 0 if json.loads(r["voice_ids_json"] or "[]") else 1
            if cursor - 2 <= seq <= cursor + 150:
                return (0, has_v, abs(seq - cursor))
            if seq > cursor:
                return (1, has_v, seq - cursor)
            return (2, has_v, cursor - seq)

        return min(rows, key=_score)

    def _load_asset_bytes(self, pack_id: str, voice_id: str) -> tuple[bytes, str] | None:
        stem = Path(voice_id).stem.lower()
        with self._lock:
            row = self._conn.execute(
                "SELECT ext, archive_path, internal_name, disk_path, adler32, flags, orig_size, segments_json "
                "FROM voice_assets WHERE pack_id = ? AND stem_lower = ? LIMIT 1",
                (pack_id, stem),
            ).fetchone()
        if row is None:
            return None

        ext = str(row["ext"] or ".ogg")
        if row["disk_path"]:
            p = Path(row["disk_path"])
            if p.exists():
                return p.read_bytes(), ext
            return None

        if row["archive_path"]:
            arc = Path(row["archive_path"])
            if not arc.exists():
                return None
            meta = Xp3EntryMeta(
                archive_path=str(arc),
                internal_name=str(row["internal_name"]),
                adler32=int(row["adler32"]),
                flags=int(row["flags"]),
                orig_size=int(row["orig_size"]),
                segments=[tuple(int(x) for x in s) for s in json.loads(row["segments_json"] or "[]")],
            )
            decryptor = self._get_decryptor(pack_id)
            return read_xp3_entry_bytes(meta, decryptor=decryptor), ext
        return None

    def match_and_extract_voice(
        self,
        text: str,
        pack_id: str | None = None,
        game_name: str = "",
        exe_path: str = "",
    ) -> MatchedVoiceResult:
        """比對台詞並從遊戲本體／解包資源即時抽出純淨聲優原聲 MP3（0% BGM）。"""
        if not text or not text.strip():
            return MatchedVoiceResult(matched_line=False)

        pack: VoicePackSummary | None = None
        if pack_id:
            for p in self.list_packs():
                if p.pack_id == pack_id:
                    pack = p
                    break
        if pack is None:
            pack = self.resolve_pack_for_game(game_name=game_name, exe_path=exe_path)
        if pack is None:
            return MatchedVoiceResult(matched_line=False)

        n_exact, n_core = normalize_dialogue_for_match(text)
        if not n_exact:
            return MatchedVoiceResult(matched_line=False)

        with self._lock:
            rows = self._conn.execute(
                "SELECT seq_index, speaker, voice_ids_json, translation FROM dialogue_lines "
                "WHERE pack_id = ? AND norm_exact = ?",
                (pack.pack_id, n_exact),
            ).fetchall()
            if not rows and n_core:
                rows = self._conn.execute(
                    "SELECT seq_index, speaker, voice_ids_json, translation FROM dialogue_lines "
                    "WHERE pack_id = ? AND norm_core = ?",
                    (pack.pack_id, n_core),
                ).fetchall()
            if not rows and len(n_core) >= 8:
                # 備援子字串比對：容許 Hook 前後多夾雜少許符號或局部行斷開
                rows = self._conn.execute(
                    "SELECT seq_index, speaker, voice_ids_json, translation FROM dialogue_lines "
                    "WHERE pack_id = ? AND norm_core LIKE ? LIMIT 10",
                    (pack.pack_id, f"%{n_core}%"),
                ).fetchall()

        if not rows:
            return MatchedVoiceResult(matched_line=False, pack_id=pack.pack_id, pack_title=pack.game_name)

        best = self._pick_best_candidate(pack.pack_id, rows)
        seq = int(best["seq_index"])
        cur = self._cursors.get(pack.pack_id, 0)
        # 只有單一命中或與當前遊玩游標距離合理時推進游標，防止極短詞跳走章節
        if len(rows) == 1 or abs(seq - cur) <= 150 or cur == 0:
            self._cursors[pack.pack_id] = seq

        voice_ids = [str(v) for v in (json.loads(best["voice_ids_json"] or "[]")) if v]
        speaker = str(best["speaker"] or "")
        trans = str(best["translation"] or "")
        if not voice_ids:
            return MatchedVoiceResult(
                matched_line=True,
                has_voice=False,
                pack_id=pack.pack_id,
                pack_title=pack.game_name,
                seq_index=seq,
                speaker=speaker,
                translation=trans,
            )

        raw_assets: list[tuple[bytes, str]] = []
        for vid in voice_ids:
            loaded = self._load_asset_bytes(pack.pack_id, vid)
            if loaded is not None:
                raw_assets.append(loaded)

        if not raw_assets:
            return MatchedVoiceResult(
                matched_line=True,
                has_voice=False,
                pack_id=pack.pack_id,
                pack_title=pack.game_name,
                seq_index=seq,
                speaker=speaker,
                voice_ids=voice_ids,
                translation=trans,
            )

        mp3 = _mix_multi_audio_to_mp3(raw_assets)
        if not mp3:
            return MatchedVoiceResult(
                matched_line=True,
                has_voice=False,
                pack_id=pack.pack_id,
                pack_title=pack.game_name,
                seq_index=seq,
                speaker=speaker,
                voice_ids=voice_ids,
                translation=trans,
            )

        safe_stem = re.sub(r"[^\w-]+", "_", voice_ids[0])[:32]
        fname = f"galanki_pure_{safe_stem}_{int(time.time() * 1000)}.mp3"
        return MatchedVoiceResult(
            matched_line=True,
            has_voice=True,
            pack_id=pack.pack_id,
            pack_title=pack.game_name,
            seq_index=seq,
            speaker=speaker,
            voice_ids=voice_ids,
            translation=trans,
            audio_clip=(mp3, fname),
        )

    def get_sample_voiced_lines(self, pack_id: str, limit: int = 8) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq_index, speaker, clean_text, voice_ids_json, translation FROM dialogue_lines "
                "WHERE pack_id = ? AND voice_ids_json != '[]' ORDER BY seq_index ASC LIMIT ?",
                (pack_id, limit),
            ).fetchall()
        return [
            {
                "seq": r["seq_index"],
                "speaker": r["speaker"],
                "text": r["clean_text"],
                "voices": json.loads(r["voice_ids_json"] or "[]"),
                "translation": r["translation"],
            }
            for r in rows
        ]

    def export_pack_assets(
        self,
        pack_id: str,
        output_dir: Path,
        progress_cb: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """將該索引包的純淨語音原檔與台詞對照 JSON 實體匯出至指定資料夾。"""
        output_dir.mkdir(parents=True, exist_ok=True)
        voice_dir = output_dir / "voice"
        voice_dir.mkdir(parents=True, exist_ok=True)
        decryptor = self._get_decryptor(pack_id)

        with self._lock:
            assets = self._conn.execute(
                "SELECT stem_lower, ext, archive_path, internal_name, disk_path, adler32, flags, orig_size, segments_json "
                "FROM voice_assets WHERE pack_id = ?",
                (pack_id,),
            ).fetchall()
            lines = self._conn.execute(
                "SELECT seq_index, scene_file, speaker, clean_text, voice_ids_json, translation "
                "FROM dialogue_lines WHERE pack_id = ? ORDER BY seq_index ASC",
                (pack_id,),
            ).fetchall()

        exported_voices = 0
        for idx, row in enumerate(assets, start=1):
            if progress_cb and (idx == 1 or idx % 200 == 0 or idx == len(assets)):
                progress_cb(f"正在匯出純淨語音檔 ({idx}/{len(assets)})...")
            iname = Path(str(row["internal_name"] or f"{row['stem_lower']}{row['ext']}")).name
            dest = voice_dir / iname
            if row["disk_path"]:
                src = Path(row["disk_path"])
                if src.exists() and not dest.exists():
                    dest.write_bytes(src.read_bytes())
                    exported_voices += 1
            elif row["archive_path"]:
                meta = Xp3EntryMeta(
                    archive_path=str(row["archive_path"]),
                    internal_name=str(row["internal_name"]),
                    adler32=int(row["adler32"]),
                    flags=int(row["flags"]),
                    orig_size=int(row["orig_size"]),
                    segments=[tuple(int(x) for x in s) for s in json.loads(row["segments_json"] or "[]")],
                )
                dest.write_bytes(read_xp3_entry_bytes(meta, decryptor=decryptor))
                exported_voices += 1

        script_json_path = output_dir / "dialogue_voice_index.json"
        payload = [
            {
                "seq": r["seq_index"],
                "scene": r["scene_file"],
                "speaker": r["speaker"],
                "text": r["clean_text"],
                "voices": json.loads(r["voice_ids_json"] or "[]"),
                "translation": r["translation"],
            }
            for r in lines
        ]
        script_json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "output_dir": str(output_dir),
            "exported_voices": exported_voices,
            "script_json": str(script_json_path),
            "line_count": len(lines),
        }
