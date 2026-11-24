"""純 Python Kirikiri XP3（含 CxDec 解密）＋ M2 PSB (.scn) 劇本與純淨人聲讀取引擎。

設計重點：
1. **免外掛 / 免拆滿硬碟**：不依賴 Windows 版 GARbro / FreeMote / KrkrExtract，直接在 Linux / Windows
   從遊戲的 `.xp3` 封包讀取 `.scn`（PSB）劇本與對應 `.opus` / `.ogg` / `.wav` 聲優原聲索引，
   製卡或試聽時僅針對該句台詞即時抽出純人聲（0% BGM、0% 音效），節省數 GB 硬碟空間。
2. **零著作權金鑰隨碼公開**：不於原始碼硬編碼遊戲加密表；需要解密 `CxEncryption` 封包時，
   由使用者在介面一鍵下載／匯入公開社群資料庫 `Formats.dat` 至本機 XDG 資料夾快取使用。
"""

from __future__ import annotations

import io
import json
import re
import struct
import urllib.request
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from galgame_anki.config import get_data_dir

DEFAULT_FORMATS_DAT_URLS = (
    "https://raw.githubusercontent.com/crskycode/GARbro/master/ArcFormats/Resources/Formats.dat",
    "https://raw.githubusercontent.com/esperz2019/GARbro/ad53926dd1bc59a8adaa941116ffcd2982cd513d/ArcFormats/Resources/Formats.dat",
)

# 清除 PSB 劇本中僅供排版/注音的標記（如 [だんたん,1]断端 -> 断端、%i1Atri%id -> Atri）
_PSB_RUBY_RE = re.compile(r"\[([^,\]]+),\s*\d+\]")
_PSB_ESC_RE = re.compile(r"%[a-zA-Z]\d*|\\n")


@dataclass
class CxSchemeParams:
    """Kirikiri CxEncryption 解密參數。"""

    title: str
    mask: int
    offset: int
    prolog_order: list[int]
    odd_branch_order: list[int]
    even_branch_order: list[int]
    control_block: list[int]


@dataclass
class Xp3EntryMeta:
    """XP3 封包內單一檔案的索引指標（不載入內容即可快速建表或單檔讀取）。"""

    archive_path: str
    internal_name: str
    adler32: int
    flags: int
    orig_size: int
    segments: list[tuple[int, int, int, int]]  # (compressed_flags, offset, orig_sz, archived_sz)


@dataclass
class PsbDialogueEntry:
    """從 .scn (PSB) 劇本提取的單句台詞與純人聲對應。"""

    seq_index: int
    scene_file: str
    speaker: str
    raw_text: str
    clean_text: str
    voice_ids: list[str] = field(default_factory=list)
    translation: str = ""


def cached_formats_dat_path() -> Path:
    d = get_data_dir() / "garbro"
    d.mkdir(parents=True, exist_ok=True)
    return d / "Formats.dat"


def cached_schemes_dir() -> Path:
    d = get_data_dir() / "garbro" / "schemes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_formats_dat(
    target_path: Path | None = None,
    progress_cb: Callable[[str], None] | None = None,
) -> Path:
    """若本機尚未快取 Formats.dat，從開源 GARbro 倉庫下載並儲存至本機 XDG 資料夾。"""
    dest = target_path or cached_formats_dat_path()
    if dest.exists() and dest.stat().st_size > 100_000:
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    last_err: Exception | None = None
    for url in DEFAULT_FORMATS_DAT_URLS:
        try:
            if progress_cb:
                progress_cb("正在下載公開解密設定資料庫 (Formats.dat)...")
            req = urllib.request.Request(url, headers={"User-Agent": "GalAnki/1.0"})
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = resp.read()
            if data.startswith(b"GARbroDB") and len(data) > 100_000:
                tmp = dest.with_suffix(".tmp")
                tmp.write_bytes(data)
                tmp.replace(dest)
                if progress_cb:
                    progress_cb("✓ 已快取 Formats.dat")
                return dest
        except Exception as exc:
            last_err = exc
    raise RuntimeError(f"無法下載 Formats.dat：{last_err}")


def clean_psb_dialogue_text(raw: str) -> str:
    """將 PSB 劇本內的控制碼、換行符與ルビ標記還原成與遊戲畫面／Hook 一致的純淨日文。"""
    if not raw:
        return ""
    s = _PSB_RUBY_RE.sub("", raw)
    s = s.replace("\r\n", "").replace("\n", "")
    s = _PSB_ESC_RE.sub("", s)
    return s.strip()


# === .NET NRBF (BinaryFormatter) 解析器：讀取 GARbro Formats.dat ===
class _NrbfRef:
    __slots__ = ("oid",)

    def __init__(self, oid: int) -> None:
        self.oid = oid


def _read_7bit_str(f: io.BytesIO) -> str:
    length = 0
    shift = 0
    while True:
        b = f.read(1)[0]
        length |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return f.read(length).decode("utf-8", errors="replace")


def _read_nrbf_primitive(f: io.BytesIO, pt: int) -> Any:
    if pt == 1:
        return bool(f.read(1)[0])
    if pt == 2:
        return f.read(1)[0]
    if pt == 3:
        return f.read(2).decode("utf-16le", errors="replace")
    if pt == 5:
        return _read_7bit_str(f)
    if pt == 6:
        return struct.unpack("<d", f.read(8))[0]
    if pt == 7:
        return struct.unpack("<h", f.read(2))[0]
    if pt == 8:
        return struct.unpack("<i", f.read(4))[0]
    if pt == 9:
        return struct.unpack("<q", f.read(8))[0]
    if pt == 10:
        return struct.unpack("<b", f.read(1))[0]
    if pt == 11:
        return struct.unpack("<f", f.read(4))[0]
    if pt in (12, 13):
        return struct.unpack("<Q", f.read(8))[0]
    if pt == 14:
        return struct.unpack("<H", f.read(2))[0]
    if pt == 15:
        return struct.unpack("<I", f.read(4))[0]
    if pt == 16:
        return struct.unpack("<Q", f.read(8))[0]
    if pt == 18:
        return _read_7bit_str(f)
    raise ValueError(f"Unsupported NRBF primitive type {pt}")


def _parse_formats_dat_nrbf(raw: bytes) -> dict[Any, Any]:
    f = io.BytesIO(raw)
    objs: dict[Any, Any] = {}
    classes: dict[int, tuple[str, list[str], list[int], list[Any]]] = {}

    def read_mti(mc: int) -> tuple[list[int], list[Any]]:
        bts = list(f.read(mc))
        ais: list[Any] = []
        for bt in bts:
            if bt in (0, 7):
                ais.append(f.read(1)[0])
            elif bt in (1, 2, 5, 6):
                ais.append(None)
            elif bt == 3:
                ais.append(_read_7bit_str(f))
            elif bt == 4:
                ais.append((_read_7bit_str(f), struct.unpack("<i", f.read(4))[0]))
            else:
                raise ValueError(f"Unsupported NRBF binary type {bt}")
        return bts, ais

    def read_arr(cnt: int, bt: int, ai: Any) -> list[Any]:
        arr: list[Any] = []
        i = 0
        while i < cnt:
            if bt == 0:
                arr.append(_read_nrbf_primitive(f, ai))
                i += 1
            else:
                v = read_next()
                if isinstance(v, tuple) and v and v[0] == "NULLM":
                    arr.extend([None] * v[1])
                    i += v[1]
                else:
                    arr.append(v)
                    i += 1
        return arr

    def read_next() -> Any:
        rec = f.read(1)
        if not rec:
            return None
        rt = rec[0]
        if rt == 0:
            return ("HDR", f.read(16))
        if rt == 1:
            oid, mid = struct.unpack("<ii", f.read(8))
            nm, mns, bts, ais = classes[mid]
            d: dict[str, Any] = {"__class__": nm}
            for mn, bt, ai in zip(mns, bts, ais):
                d[mn] = _read_nrbf_primitive(f, ai) if bt == 0 else read_next()
            objs[oid] = d
            return _NrbfRef(oid)
        if rt in (4, 5):
            oid = struct.unpack("<i", f.read(4))[0]
            nm = _read_7bit_str(f)
            mc = struct.unpack("<i", f.read(4))[0]
            mns = [_read_7bit_str(f) for _ in range(mc)]
            bts, ais = read_mti(mc)
            if rt == 5:
                f.read(4)
            classes[oid] = (nm, mns, bts, ais)
            d = {"__class__": nm}
            for mn, bt, ai in zip(mns, bts, ais):
                d[mn] = _read_nrbf_primitive(f, ai) if bt == 0 else read_next()
            objs[oid] = d
            return _NrbfRef(oid)
        if rt == 6:
            oid = struct.unpack("<i", f.read(4))[0]
            objs[oid] = _read_7bit_str(f)
            return _NrbfRef(oid)
        if rt == 7:
            oid = struct.unpack("<i", f.read(4))[0]
            at = f.read(1)[0]
            rk = struct.unpack("<i", f.read(4))[0]
            lens = [struct.unpack("<i", f.read(4))[0] for _ in range(rk)]
            if at in (3, 4, 5):
                f.read(4 * rk)
            bt = f.read(1)[0]
            ai: Any = None
            if bt in (0, 7):
                ai = f.read(1)[0]
            elif bt == 3:
                ai = _read_7bit_str(f)
            elif bt == 4:
                ai = (_read_7bit_str(f), struct.unpack("<i", f.read(4))[0])
            tot = 1
            for l in lens:
                tot *= l
            objs[oid] = read_arr(tot, bt, ai)
            return _NrbfRef(oid)
        if rt == 8:
            return _read_nrbf_primitive(f, f.read(1)[0])
        if rt == 9:
            return _NrbfRef(struct.unpack("<i", f.read(4))[0])
        if rt == 10:
            return None
        if rt == 11:
            return ("END",)
        if rt == 12:
            struct.unpack("<i", f.read(4))
            _read_7bit_str(f)
            return read_next()
        if rt == 13:
            return ("NULLM", f.read(1)[0])
        if rt == 14:
            return ("NULLM", struct.unpack("<i", f.read(4))[0])
        if rt == 15:
            oid, cnt = struct.unpack("<ii", f.read(8))
            pt = f.read(1)[0]
            if pt == 2:
                arr: list[Any] = list(f.read(cnt))
            elif pt == 15:
                arr = list(struct.unpack(f"<{cnt}I", f.read(4 * cnt)))
            elif pt == 8:
                arr = list(struct.unpack(f"<{cnt}i", f.read(4 * cnt)))
            else:
                arr = [_read_nrbf_primitive(f, pt) for _ in range(cnt)]
            objs[oid] = arr
            return _NrbfRef(oid)
        if rt in (16, 17):
            oid, cnt = struct.unpack("<ii", f.read(8))
            objs[oid] = read_arr(cnt, 2, None)
            return _NrbfRef(oid)
        raise ValueError(f"Unhandled NRBF record type {rt} at {f.tell() - 1}")

    while f.tell() < len(raw):
        if read_next() == ("END",):
            break
    return objs


def find_cx_scheme_for_game(
    game_dir: Path,
    formats_dat_path: Path | None = None,
) -> CxSchemeParams | None:
    """從 Formats.dat 依遊戲執行檔名／資料夾名稱自動尋找對應的 Kirikiri CxEncryption 參數。"""
    # 1. 若已提取單一遊戲 JSON 快取，優先秒讀
    safe_dir_key = re.sub(r"[^\w.-]+", "_", game_dir.name).strip("_") or "game"
    scheme_cache = cached_schemes_dir() / f"{safe_dir_key}.json"
    if scheme_cache.exists():
        try:
            d = json.loads(scheme_cache.read_text(encoding="utf-8"))
            return CxSchemeParams(
                title=d["title"],
                mask=int(d["mask"]),
                offset=int(d["offset"]),
                prolog_order=[int(x) for x in d["prolog_order"]],
                odd_branch_order=[int(x) for x in d["odd_branch_order"]],
                even_branch_order=[int(x) for x in d["even_branch_order"]],
                control_block=[int(x) for x in d["control_block"]],
            )
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            pass

    dat_path = formats_dat_path or cached_formats_dat_path()
    if not dat_path.exists():
        return None
    blob = dat_path.read_bytes()
    if not blob.startswith(b"GARbroDB") or len(blob) < 16:
        raise ValueError("無效的 Formats.dat 檔案格式。")
    raw = zlib.decompress(blob[12:])
    objs = _parse_formats_dat_nrbf(raw)

    def deref(v: Any) -> Any:
        while isinstance(v, _NrbfRef):
            v = objs.get(v.oid)
        return v

    root = objs[1]
    exe_names = {p.name.lower() for p in game_dir.glob("*.exe")}
    matched_title: str | None = None

    gm = deref(root.get("GameMap"))
    if isinstance(gm, dict) and "KeyValuePairs" in gm:
        for rp in deref(gm["KeyValuePairs"]):
            p = deref(rp)
            if not isinstance(p, dict):
                continue
            k = str(deref(p.get("key")) or "").lower()
            if k in exe_names:
                matched_title = str(deref(p.get("value")) or "")
                break

    sm = deref(root.get("SchemeMap"))
    known_schemes: dict[str, dict[str, Any]] = {}
    if isinstance(sm, dict) and "KeyValuePairs" in sm:
        for rp in deref(sm["KeyValuePairs"]):
            p = deref(rp)
            if not isinstance(p, dict):
                continue
            if str(deref(p.get("key")) or "") == "XP3":
                ks = deref(deref(p.get("value")).get("KnownSchemes"))
                if isinstance(ks, dict) and "KeyValuePairs" in ks:
                    for p2 in deref(ks["KeyValuePairs"]):
                        p2 = deref(p2)
                        if not isinstance(p2, dict):
                            continue
                        t = str(deref(p2.get("key")) or "")
                        crypt = deref(p2.get("value"))
                        if t and isinstance(crypt, dict):
                            known_schemes[t] = crypt

    chosen_crypt: dict[str, Any] | None = None
    chosen_title = ""
    if matched_title and matched_title in known_schemes:
        chosen_title = matched_title
        chosen_crypt = known_schemes[matched_title]
    else:
        # 備援：以資料夾名稱或 exe 主檔名進行正規化模糊比對
        dir_norm = re.sub(r"[\W_]+", "", game_dir.name.lower())
        exe_norms = [re.sub(r"[\W_]+", "", Path(e).stem.lower()) for e in exe_names]
        for t, crypt in known_schemes.items():
            tn = re.sub(r"[\W_]+", "", t.lower())
            if tn and (tn in dir_norm or any(tn in en or en in tn for en in exe_norms if len(en) >= 4)):
                chosen_title = t
                chosen_crypt = crypt
                break

    if not chosen_crypt or "ControlBlock" not in chosen_crypt:
        return None
    if "CxEncryption" not in str(chosen_crypt.get("__class__", "")):
        return None

    cb = deref(chosen_crypt.get("ControlBlock"))
    if not isinstance(cb, list) or len(cb) < 1024:
        return None

    params = CxSchemeParams(
        title=chosen_title,
        mask=int(chosen_crypt["m_mask"]),
        offset=int(chosen_crypt["m_offset"]),
        prolog_order=[int(x) for x in deref(chosen_crypt["PrologOrder"])],
        odd_branch_order=[int(x) for x in deref(chosen_crypt["OddBranchOrder"])],
        even_branch_order=[int(x) for x in deref(chosen_crypt["EvenBranchOrder"])],
        control_block=[int(x) for x in cb[:1024]],
    )
    try:
        scheme_cache.write_text(
            json.dumps(
                {
                    "title": params.title,
                    "mask": params.mask,
                    "offset": params.offset,
                    "prolog_order": params.prolog_order,
                    "odd_branch_order": params.odd_branch_order,
                    "even_branch_order": params.even_branch_order,
                    "control_block": params.control_block,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass
    return params


class CxDecryptor:
    """KiriKiri CxEncryption 32-bit x86 字節碼解密器（移植自 MIT 授權 GARbro KiriKiriCx.cs）。"""

    def __init__(self, params: CxSchemeParams) -> None:
        self.mask = params.mask & 0xFFFFFFFF
        self.offset = params.offset & 0xFFFFFFFF
        self.prolog = params.prolog_order
        self.odd = params.odd_branch_order
        self.even = params.even_branch_order
        # ControlBlock 於 CxEncryption.Init() 以按位取反存入，執行 MOV_EAX_INDIRECT 時再取反
        self.cb_inv = [(~int(x)) & 0xFFFFFFFF for x in params.control_block]
        self._prog_cache: dict[int, list[tuple[str, int]]] = {}

    def _compile_prog(self, seed: int) -> list[tuple[str, int]]:
        cached = self._prog_cache.get(seed)
        if cached is not None:
            return cached

        st: dict[str, Any] = {"seed": seed & 0xFFFFFFFF, "len": 0, "ops": []}

        def rnd() -> int:
            s = st["seed"]
            st["seed"] = (1103515245 * s + 12345) & 0xFFFFFFFF
            return (st["seed"] ^ ((s << 16) & 0xFFFFFFFF) ^ (s >> 16)) & 0xFFFFFFFF

        def emit_nop(n: int) -> bool:
            if st["len"] + n > 0x80:
                return False
            st["len"] += n
            return True

        def emit(op: str, n: int = 1) -> bool:
            if st["len"] + n > 0x80:
                return False
            st["len"] += n
            st["ops"].append((op, 0))
            return True

        def emit_u32(v: int) -> bool:
            if st["len"] + 4 > 0x80:
                return False
            st["len"] += 4
            st["ops"][-1] = (st["ops"][-1][0], v & 0xFFFFFFFF)
            return True

        def emit_prolog() -> bool:
            sel = self.prolog[rnd() % 3]
            if sel == 2:
                return (
                    emit_nop(5)
                    and emit("MOV_EAX_IMMED", 2)
                    and emit_u32(rnd() & 0x3FF)
                    and emit("MOV_EAX_INDIRECT", 0)
                )
            if sel == 1:
                return emit("MOV_EAX_EDI", 2)
            return emit("MOV_EAX_IMMED", 1) and emit_u32(rnd())

        def emit_even() -> bool:
            sel = self.even[rnd() & 7]
            if sel == 0:
                return emit("NOT_EAX", 2)
            if sel == 1:
                return emit("DEC_EAX", 1)
            if sel == 2:
                return emit("NEG_EAX", 2)
            if sel == 3:
                return emit("INC_EAX", 1)
            if sel == 4:
                return (
                    emit_nop(5)
                    and emit("AND_EAX_IMMED", 1)
                    and emit_u32(0x3FF)
                    and emit("MOV_EAX_INDIRECT", 3)
                )
            if sel == 5:
                return (
                    emit("PUSH_EBX", 1)
                    and emit("MOV_EBX_EAX", 2)
                    and emit("AND_EBX_IMMED", 2)
                    and emit_u32(0xAAAAAAAA)
                    and emit("AND_EAX_IMMED", 1)
                    and emit_u32(0x55555555)
                    and emit("SHR_EBX_1", 2)
                    and emit("SHL_EAX_1", 2)
                    and emit("OR_EAX_EBX", 2)
                    and emit("POP_EBX", 1)
                )
            if sel == 6:
                return emit("XOR_EAX_IMMED", 1) and emit_u32(rnd())
            op = "ADD_EAX_IMMED" if (rnd() & 1) else "SUB_EAX_IMMED"
            return emit(op, 1) and emit_u32(rnd())

        def emit_odd() -> bool:
            sel = self.odd[rnd() % 6]
            if sel == 0:
                return (
                    emit("PUSH_ECX", 1)
                    and emit("MOV_ECX_EBX", 2)
                    and emit("AND_ECX_0F", 3)
                    and emit("SHR_EAX_CL", 2)
                    and emit("POP_ECX", 1)
                )
            if sel == 1:
                return (
                    emit("PUSH_ECX", 1)
                    and emit("MOV_ECX_EBX", 2)
                    and emit("AND_ECX_0F", 3)
                    and emit("SHL_EAX_CL", 2)
                    and emit("POP_ECX", 1)
                )
            if sel == 2:
                return emit("ADD_EAX_EBX", 2)
            if sel == 3:
                return emit("NEG_EAX", 2) and emit("ADD_EAX_EBX", 2)
            if sel == 4:
                return emit("IMUL_EAX_EBX", 3)
            return emit("SUB_EAX_EBX", 2)

        def emit_body(stg: int) -> bool:
            if stg == 1:
                return emit_prolog()
            if not emit("PUSH_EBX", 1):
                return False
            if not (emit_body(stg - 1) if (rnd() & 1) else emit_body2(stg - 1)):
                return False
            if not emit("MOV_EBX_EAX", 2):
                return False
            if not (emit_body(stg - 1) if (rnd() & 1) else emit_body2(stg - 1)):
                return False
            return emit_odd() and emit("POP_EBX", 1)

        def emit_body2(stg: int) -> bool:
            if stg == 1:
                return emit_prolog()
            rc = emit_body(stg - 1) if (rnd() & 1) else emit_body2(stg - 1)
            return rc and emit_even()

        for stage in range(5, 0, -1):
            st["len"] = 0
            st["ops"] = []
            if (
                emit_nop(5)
                and emit("MOV_EDI_ARG", 4)
                and emit_body(stage)
                and emit_nop(5)
                and emit("RETN", 1)
            ):
                self._prog_cache[seed] = st["ops"]
                return st["ops"]
        raise RuntimeError(f"CxEncryption bytecode generation failed for seed {seed}")

    def _exec_ops(self, ops: list[tuple[str, int]], arg: int) -> int:
        eax = ebx = ecx = 0
        edi = arg & 0xFFFFFFFF
        stack: list[int] = []
        for op, imm in ops:
            if op == "MOV_EDI_ARG":
                edi = arg & 0xFFFFFFFF
            elif op == "PUSH_EBX":
                stack.append(ebx)
            elif op == "POP_EBX":
                ebx = stack.pop()
            elif op == "PUSH_ECX":
                stack.append(ecx)
            elif op == "POP_ECX":
                ecx = stack.pop()
            elif op == "MOV_EBX_EAX":
                ebx = eax
            elif op == "MOV_EAX_EDI":
                eax = edi
            elif op == "MOV_ECX_EBX":
                ecx = ebx
            elif op == "MOV_EAX_EBX":
                eax = ebx
            elif op == "AND_ECX_0F":
                ecx &= 0x0F
            elif op == "SHR_EBX_1":
                ebx = (ebx >> 1) & 0xFFFFFFFF
            elif op == "SHL_EAX_1":
                eax = (eax << 1) & 0xFFFFFFFF
            elif op == "SHR_EAX_CL":
                eax = (eax >> (ecx & 31)) & 0xFFFFFFFF
            elif op == "SHL_EAX_CL":
                eax = (eax << (ecx & 31)) & 0xFFFFFFFF
            elif op == "OR_EAX_EBX":
                eax |= ebx
            elif op == "NOT_EAX":
                eax = (~eax) & 0xFFFFFFFF
            elif op == "NEG_EAX":
                eax = (-eax) & 0xFFFFFFFF
            elif op == "DEC_EAX":
                eax = (eax - 1) & 0xFFFFFFFF
            elif op == "INC_EAX":
                eax = (eax + 1) & 0xFFFFFFFF
            elif op == "ADD_EAX_EBX":
                eax = (eax + ebx) & 0xFFFFFFFF
            elif op == "SUB_EAX_EBX":
                eax = (eax - ebx) & 0xFFFFFFFF
            elif op == "IMUL_EAX_EBX":
                eax = (eax * ebx) & 0xFFFFFFFF
            elif op == "ADD_EAX_IMMED":
                eax = (eax + imm) & 0xFFFFFFFF
            elif op == "SUB_EAX_IMMED":
                eax = (eax - imm) & 0xFFFFFFFF
            elif op == "AND_EBX_IMMED":
                ebx &= imm
            elif op == "AND_EAX_IMMED":
                eax &= imm
            elif op == "XOR_EAX_IMMED":
                eax ^= imm
            elif op == "MOV_EAX_IMMED":
                eax = imm
            elif op == "MOV_EAX_INDIRECT":
                eax = self.cb_inv[eax]
            elif op == "RETN":
                return eax
        raise RuntimeError("CxEncryption program missing RETN")

    def decrypt(self, entry_hash: int, data: bytes) -> bytes:
        buf = bytearray(data)
        ad = entry_hash & 0xFFFFFFFF

        def decode_slice(key: int, off0: int, pos: int, cnt: int) -> None:
            if cnt <= 0:
                return
            ops = self._compile_prog(key & 0x7F)
            h = (key >> 7) & 0xFFFFFFFF
            r1 = self._exec_ops(ops, h)
            r2 = self._exec_ops(ops, (~h) & 0xFFFFFFFF)
            k1 = (r2 >> 16) & 0xFFFF
            k2 = r2 & 0xFFFF
            k3 = r1 & 0xFF
            if k1 == k2:
                k2 += 1
            if k3 == 0:
                k3 = 1
            if off0 <= k2 < off0 + cnt:
                buf[pos + k2 - off0] ^= (r1 >> 16) & 0xFF
            if off0 <= k1 < off0 + cnt:
                buf[pos + k1 - off0] ^= (r1 >> 8) & 0xFF
            end = pos + cnt
            chunk_int = int.from_bytes(buf[pos:end], "little")
            xor_int = int.from_bytes(bytes([k3]) * cnt, "little")
            buf[pos:end] = (chunk_int ^ xor_int).to_bytes(cnt, "little")

        base = (ad & self.mask) + self.offset
        total = len(buf)
        pos = 0
        cur_off = 0
        if cur_off < base:
            bl = min(base - cur_off, total)
            decode_slice(ad, cur_off, pos, bl)
            cur_off += bl
            pos += bl
            total -= bl
        if total > 0:
            decode_slice(((ad >> 16) ^ ad) & 0xFFFFFFFF, cur_off, pos, total)
        return bytes(buf)


def list_xp3_entries(xp3_path: Path) -> list[Xp3EntryMeta]:
    """解析單一 `.xp3` 封包索引，回傳所有內含檔案指標（相容標準 File 與 hnfn 明文名表）。"""
    with xp3_path.open("rb") as f:
        magic = f.read(11)
        if magic != b"XP3\r\n \n\x1a\x8bg\x01":
            raise ValueError(f"非標準 XP3 封包標頭：{xp3_path.name}")
        idx_off = struct.unpack("<Q", f.read(8))[0]
        f.seek(idx_off)
        while True:
            fl = f.read(1)[0]
            if fl & 0x80:
                _, idx_off = struct.unpack("<QQ", f.read(16))
                f.seek(idx_off)
                continue
            if fl & 1:
                csz, _rsz = struct.unpack("<QQ", f.read(16))
                raw = zlib.decompress(f.read(csz))
            else:
                rsz = struct.unpack("<Q", f.read(8))[0]
                raw = f.read(rsz)
            break

    pos = 0
    files_by_adlr: dict[int, list[tuple[str, int, int, list[tuple[int, int, int, int]]]]] = {}
    hnfn_names: list[tuple[int, str]] = []
    ordered_metas: list[Xp3EntryMeta] = []

    while pos + 12 <= len(raw):
        tag = raw[pos : pos + 4]
        sz = struct.unpack("<Q", raw[pos + 4 : pos + 12])[0]
        chunk = raw[pos + 12 : pos + 12 + sz]
        pos += 12 + sz

        if tag == b"hnfn" and len(chunk) >= 6:
            ad, nlen = struct.unpack("<IH", chunk[:6])
            name = chunk[6 : 6 + nlen * 2].decode("utf-16le", errors="replace")
            hnfn_names.append((ad, name))
        elif tag == b"File":
            sp = 0
            fname = ""
            flags = 0
            orig_sz = 0
            segs: list[tuple[int, int, int, int]] = []
            adlr = 0
            while sp + 12 <= len(chunk):
                st = chunk[sp : sp + 4]
                ss = struct.unpack("<Q", chunk[sp + 4 : sp + 12])[0]
                sc = chunk[sp + 12 : sp + 12 + ss]
                sp += 12 + ss
                if st == b"info" and len(sc) >= 22:
                    flags, orig_sz, _asz, nlen = struct.unpack("<IQQH", sc[:22])
                    fname = sc[22 : 22 + nlen * 2].decode("utf-16le", errors="replace")
                elif st == b"segm":
                    for i in range(0, len(sc), 28):
                        segs.append(struct.unpack("<IQQQ", sc[i : i + 28]))
                elif st == b"adlr" and len(sc) >= 4:
                    adlr = struct.unpack("<I", sc[:4])[0]
            files_by_adlr.setdefault(adlr, []).append((fname, flags, orig_sz, segs))

    if hnfn_names:
        seen_names: set[str] = set()
        for ad, name in hnfn_names:
            cands = files_by_adlr.get(ad)
            if not cands or name in seen_names:
                continue
            seen_names.add(name)
            _hashed, flags, orig_sz, segs = cands[0]
            ordered_metas.append(
                Xp3EntryMeta(
                    archive_path=str(xp3_path),
                    internal_name=name,
                    adler32=ad,
                    flags=flags,
                    orig_size=orig_sz,
                    segments=segs,
                )
            )
    else:
        for adlr, items in files_by_adlr.items():
            for fname, flags, orig_sz, segs in items:
                ordered_metas.append(
                    Xp3EntryMeta(
                        archive_path=str(xp3_path),
                        internal_name=fname,
                        adler32=adlr,
                        flags=flags,
                        orig_size=orig_sz,
                        segments=segs,
                    )
                )
    return ordered_metas


def read_xp3_entry_bytes(
    meta: Xp3EntryMeta,
    decryptor: CxDecryptor | None = None,
) -> bytes:
    """從 `.xp3` 讀取單一檔案並於記憶體即時解密還原。"""
    buf = bytearray()
    with Path(meta.archive_path).open("rb") as f:
        for cflags, offset, _osz, asz in meta.segments:
            f.seek(offset)
            d = f.read(asz)
            if cflags & 1:
                d = zlib.decompress(d)
            buf.extend(d)
    raw = bytes(buf)
    if meta.flags and decryptor is not None:
        return decryptor.decrypt(meta.adler32, raw)
    return raw


# === M2 PSB (.scn) 二進位劇本解析器 ===
def _read_psb_int_arr(data: bytes, offset: int) -> tuple[list[int], int]:
    tp = data[offset]
    size_bytes = tp - 0x0C
    if not (1 <= size_bytes <= 8):
        raise ValueError(f"無效 PSB 整數陣列標記 {tp:#x} @ {offset:#x}")
    count = int.from_bytes(data[offset + 1 : offset + 1 + size_bytes], "little")
    elem_tp = data[offset + 1 + size_bytes]
    elem_sz = elem_tp - 0x0C
    start = offset + 1 + size_bytes + 1
    if count == 0:
        return [], start
    vals = [
        int.from_bytes(data[start + i * elem_sz : start + (i + 1) * elem_sz], "little")
        for i in range(count)
    ]
    return vals, start + count * elem_sz


def parse_psb_tree(data: bytes) -> dict[str, Any] | list[Any] | None:
    """解析 M2 PSB v2/v3（含 MDF 壓縮封裝）為 Python 字典／陣列樹。"""
    if data[:4] == b"mdf\x00" and len(data) > 8:
        data = zlib.decompress(data[8:])
    if data[:4] != b"PSB\x00":
        raise ValueError("非標準 PSB 劇本檔案")

    _magic, _ver, enc, _hdr_len, off_names, off_str, off_str_data, _ch_off, _ch_len, _ch_dat, off_entries = struct.unpack(
        "<4sHHIIIIIIII", data[:40]
    )
    if enc != 0:
        raise ValueError("PSB 標頭已加密，請改用 FreeMote 解密後匯入")

    charset, p = _read_psb_int_arr(data, off_names)
    names_data, p = _read_psb_int_arr(data, p)
    name_indexes, _ = _read_psb_int_arr(data, p)

    names: list[str] = []
    for index in name_indexes:
        bs = bytearray()
        c = names_data[index]
        while c != 0:
            code = names_data[c]
            bs.append((c - charset[code]) & 0xFF)
            c = code
        bs.reverse()
        names.append(bs.decode("utf-8", errors="replace"))

    str_offsets, _ = _read_psb_int_arr(data, off_str)

    def get_str(sid: int) -> str:
        s0 = off_str_data + str_offsets[sid]
        s1 = data.find(b"\x00", s0)
        if s1 == -1:
            s1 = len(data)
        return data[s0:s1].decode("utf-8", errors="replace")

    def read_obj(o: int) -> Any:
        t = data[o]
        if t == 0x01:
            return None
        if t == 0x02:
            return True
        if t == 0x03:
            return False
        if t == 0x04:
            return 0
        if 0x05 <= t <= 0x0C:
            n = t - 0x04
            return int.from_bytes(data[o + 1 : o + 1 + n], "little", signed=True)
        if 0x15 <= t <= 0x18:
            return get_str(int.from_bytes(data[o + 1 : o + 1 + (t - 0x14)], "little"))
        if t == 0x1D:
            return 0.0
        if t == 0x1E:
            return struct.unpack("<f", data[o + 1 : o + 5])[0]
        if t == 0x1F:
            return struct.unpack("<d", data[o + 1 : o + 9])[0]
        if t == 0x20:
            offsets, body = _read_psb_int_arr(data, o + 1)
            return [read_obj(body + x) for x in offsets]
        if t == 0x21:
            ki, p2 = _read_psb_int_arr(data, o + 1)
            vo, body = _read_psb_int_arr(data, p2)
            return {names[ki[i]]: read_obj(body + vo[i]) for i in range(len(ki))}
        return None

    return read_obj(off_entries)


def extract_dialogues_from_psb_obj(
    root: Any,
    scene_file: str,
    start_seq: int = 0,
) -> list[PsbDialogueEntry]:
    """從 PSB / FreeMote JSON 樹結構抽取依序排列的日文台詞與聲優語音檔名。"""
    results: list[PsbDialogueEntry] = []
    if not isinstance(root, dict):
        return results

    seq = start_seq
    scenes = root.get("scenes")
    if not isinstance(scenes, list):
        return results

    for sc in scenes:
        if not isinstance(sc, dict):
            continue
        texts = sc.get("texts")
        if not isinstance(texts, list):
            continue
        for entry in texts:
            if not isinstance(entry, list) or len(entry) < 3:
                continue
            speaker = str(entry[0] or "").strip() if entry[0] is not None else ""
            langs = entry[2]
            raw_jp = ""
            trans = ""
            if isinstance(langs, list) and langs:
                first_lang = langs[0]
                if isinstance(first_lang, list) and len(first_lang) >= 2 and isinstance(first_lang[1], str):
                    raw_jp = first_lang[1]
                elif isinstance(first_lang, str):
                    raw_jp = first_lang
                # 優先擷取繁體／簡體中文字幕作參考譯文（僅在按下「🌐 核對句意」或製卡確認窗時按需取用）
                for idx in (3, 2):
                    if (
                        len(langs) > idx
                        and isinstance(langs[idx], list)
                        and len(langs[idx]) >= 2
                        and isinstance(langs[idx][1], str)
                        and langs[idx][1].strip()
                    ):
                        trans = langs[idx][1].replace("\\n", "\n").strip()
                        break
            if not raw_jp:
                continue

            clean_jp = clean_psb_dialogue_text(raw_jp)
            if not clean_jp:
                continue

            voice_ids: list[str] = []
            if len(entry) >= 4 and isinstance(entry[3], list):
                for v in entry[3]:
                    if isinstance(v, dict):
                        vid = v.get("voice")
                        if isinstance(vid, str) and vid.strip():
                            voice_ids.append(vid.strip())

            results.append(
                PsbDialogueEntry(
                    seq_index=seq,
                    scene_file=scene_file,
                    speaker=speaker,
                    raw_text=raw_jp,
                    clean_text=clean_jp,
                    voice_ids=voice_ids,
                    translation=trans,
                )
            )
            seq += 1
    return results
