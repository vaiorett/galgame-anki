"""本地離線詞典引擎（SQLite + 完整相容 Yomitan .zip 詞典格式）。

特性：
1. 內建開箱即用的精選雙語+單語+音調詞庫（含原攻略實戰例句詞彙）。
2. 直接匯入任何社群 Yomitan `.zip` 詞典（term_bank_*.json + term_meta_bank_*.json）。
3. 支援一鍵從 GitHub Releases 下載免費開源 Yomitan 詞典（日中、日日、JMdict）。
4. 支援雙語與單語詞典分組回傳，無縫實現原攻略「登樓撤梯」三階段學習。
"""

from __future__ import annotations

import json
import re
import sqlite3
import tempfile
import threading
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx

from galgame_anki.config import get_data_dir
from galgame_anki.core.deinflector import RULE_NONE, deinflect, pos_matches_rule

ProgressCallback = Callable[[str, int], None]

# 100% 實測可用的最佳免費開源 Yomitan 詞典直連清單（免受 GitHub API 403 限流）
FREE_DICTIONARY_CATALOG: list[dict[str, Any]] = [
    {
        "id": "wty-ja-zh",
        "name": "⭐ Wiktionary 日中雙語詞典（Lv.1–2 中文主力・約 6.6 MB）",
        "kind": "bilingual",
        "priority": 5,
        "match_keywords": ["wty-ja-zh"],
        "direct_url": "https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/ja/zh/wty-ja-zh.zip",
        "description": "免費開源日中雙語釋義，查詞與收錄 Anki 時會優先顯示中文解釋。",
        "recommended_bundle": True,
    },
    {
        "id": "wty-ja-ja",
        "name": "⭐ Wiktionary 日日單語詞典（Lv.3 登樓撤梯・約 13.3 MB）",
        "kind": "monolingual",
        "priority": 15,
        "match_keywords": ["wty-ja-ja"],
        "direct_url": "https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/ja/ja/wty-ja-ja.zip",
        "description": "免費開源日日單語釋義，切至 Lv.3 時自動切換為日語解釋。",
        "recommended_bundle": True,
    },
    {
        "id": "jitendex",
        "name": "⭐ Jitendex 頂級日英詞典（20 萬+詞條超強斷詞・約 37 MB）",
        "kind": "bilingual",
        "priority": 25,
        "match_keywords": ["jitendex"],
        "direct_url": "https://github.com/stephenmk/stephenmk.github.io/releases/latest/download/jitendex-yomitan.zip",
        "description": "覆蓋率極高的開源詞典（英文釋義）；若不習慣英文可在上方勾選關閉，只保留中文詞典。",
        "recommended_bundle": True,
    },
    {
        "id": "kanjium-pitch",
        "name": "⭐ Kanjium 日語聲調詞典（高低音調核・約 3 MB）",
        "kind": "meta",
        "priority": 40,
        "match_keywords": ["kanjium"],
        "direct_url": "https://github.com/toasted-nutbread/yomichan-pitch-accent-dictionary/releases/download/1.0.0/kanjium_pitch_accents.zip",
        "description": "自動標註每個詞的標準日語高低音調 [0]/[1]/[2] 並寫入 Anki 卡片。",
        "recommended_bundle": True,
    },
    {
        "id": "jpdb-freq",
        "name": "⭐ JPDB v2.2 詞頻表（ACG / Galgame 常用度・約 4 MB）",
        "kind": "meta",
        "priority": 45,
        "match_keywords": ["jpdb"],
        "direct_url": "https://github.com/Kuuuube/yomitan-dictionaries/releases/download/yomitan-permalink/JPDB_v2.2_Frequency_Kana.zip",
        "description": "標示單詞在動漫・小說・Galgame 中的出現頻率 #排名，幫你優先挑高頻詞收錄。",
        "recommended_bundle": True,
    },
    {
        "id": "jmdict-en",
        "name": "JMdict 日英輕量標準版（約 14.6 MB・可選）",
        "kind": "bilingual",
        "priority": 30,
        "match_keywords": ["jmdict"],
        "direct_url": "https://github.com/yomidevs/jmdict-yomitan/releases/latest/download/JMdict_english_without_proper_names.zip",
        "description": "經典輕量版開源日英詞典；已裝日中詞典時可視個人習慣於上方開啟或停用。",
        "recommended_bundle": False,
    },
]


@dataclass
class DictEntry:
    term: str
    reading: str
    pos: str
    glosses: list[str]
    dict_name: str
    dict_kind: str  # bilingual | monolingual
    score: int = 0
    pitches: list[str] = field(default_factory=list)
    frequency: int | None = None


@dataclass
class LookupMatch:
    surface: str
    lemma: str
    reading: str
    reasons: tuple[str, ...]
    bilingual_entries: list[DictEntry] = field(default_factory=list)
    monolingual_entries: list[DictEntry] = field(default_factory=list)
    pitches: list[str] = field(default_factory=list)
    frequency: int | None = None
    alt_matches: list["LookupMatch"] = field(default_factory=list)

    @property
    def all_entries(self) -> list[DictEntry]:
        return self.bilingual_entries + self.monolingual_entries

    def concise_senses(
        self,
        preference: str = "bilingual",
        max_senses: int = 6,
        strict: bool = False,
    ) -> list[str]:
        """提煉乾淨的獨立單行核心義項（遵循 SuperMemo 最小資訊原則，絕不把整本詞典硬搬進卡片）。"""
        target = self.monolingual_entries if preference == "monolingual" else self.bilingual_entries
        if not target and not strict:
            target = self.bilingual_entries or self.monolingual_entries
        if not target:
            return []
        return extract_concise_senses(target, max_senses=max_senses)

    def best_concise_meaning(self, preference: str = "bilingual", strict: bool = False) -> str:
        """回傳最適合放進 Anki 卡片的 1 行重點核心釋義（嚴格去重，絕不把多本詞典重複義項拼接在一起）。"""
        senses = self.concise_senses(preference, max_senses=4, strict=strict)
        if senses:
            if preference == "bilingual":
                zh_senses = [s for s in senses if _is_chinese_gloss(s)]
                if zh_senses:
                    senses = zh_senses
            first = senses[0]
            # 僅當首義項極短（<= 3 字且不含多義分隔）且第二義項完全不重疊、亦偏短時才補充 1 條近義
            if (
                len(first) <= 3
                and not re.search(r"[、，,；;。！？]", first)
                and len(senses) > 1
                and len(senses[1]) <= 10
                and not _senses_semantically_overlap(first, senses[1])
            ):
                return f"{first}、{senses[1]}"
            return first
        if strict:
            return ""
        return self.primary_definition(preference)

    def primary_definition(self, preference: str = "bilingual") -> str:
        """依目前階段偏好組裝乾淨簡潔的釋義文字（自動去雜訊與重定向存根）。"""
        senses = self.concise_senses(preference, max_senses=4)
        if senses:
            return "\n".join(senses)
        target = self.monolingual_entries if preference == "monolingual" else self.bilingual_entries
        if not target:
            target = self.bilingual_entries or self.monolingual_entries
        lines: list[str] = []
        for entry in target[:2]:
            for g in entry.glosses[:3]:
                cleaned = _clean_gloss_noise(g)
                if cleaned and cleaned not in lines:
                    lines.append(cleaned)
        return "\n".join(lines[:4])


_INFLECTION_STUB_RE = re.compile(
    r"(?:^[\u3040-\u9fff々ー]*\s*(?:continuative|stem|attributive|imperative|hypothetical|"
    r"past tense|conjunctive|negative|passive|causative|potential|inflection of|form of|verb form|adverbial form)|"
    r"redirected from|alternative kanji|alternative spelling|alternative form of|obsolete spelling)",
    re.IGNORECASE,
)

_SKIP_SENSE_LINE_RE = re.compile(
    r"^(?:例：|　例：|see:|cf\.|compare|\d{4}[，,年]|Wiktionary|redirected from|.*redirected from|"
    r".*alternative kanji|.*alternative spelling|平假名音[节節]|片假名音[节節]|音[节節][ぁ-んァ-ン]|"
    r"[ぁ-んァ-ンー\s・]+$|(?:男性|女性)?人名$|姓氏$|地名$|名字$|大阿[尔爾]克那|須弥山|ૐ|和文通話表|"
    r"〈[^〉]+〉|.*（[★🅁①-⑨]）$|as\s+[\u3040-\u9fff]|"
    r"(?:非形式的|形式的|口語|文語|俗語|雅語|古語|廃語|方言|卑語|敬語|謙譲語|丁寧語|尊敬語|"
    r"自五|他五|自他五|自一|他一|自他一|自サ|他サ|自動|他動|自動詞|他動詞|自他動|"
    r"名詞|動詞|形容詞|形容動詞|副詞|感動詞|接続詞|連体詞|代名詞|接尾辞|接頭辞|"
    r"形|形動|名|副|感|連|接|代|接尾|接頭)$)",
    re.IGNORECASE,
)

_CITATION_LINE_RE = re.compile(
    r"(?:→ISBN|ISBN\b|虛構作品|虚构作品|頁：|ページ：|^\d{4}年\s*\d+月|收錄於|收录于|"
    r"^[（\(][^）\)]{1,20}『[^』]+』[）\)]$)"
)
_INLINE_CIRCLED_SPLIT_RE = re.compile(r"\s*(?=[①-⑨])")
_ASCII_DOMAIN_TAG_RE = re.compile(r"^[A-Za-z][A-Za-z\s/-]{0,20}$")

_INLINE_FURIGANA_RE = re.compile(r"[\u4e00-\u9fff々]\([ぁ-んァ-ヶー]{1,6}\)")
_LEADING_POS_NOISE_RE = re.compile(
    r"^(?:(?:[1-9][\.\、\s]+|[①-⑨]\s*)|(?:(?:名|動|自|他|形|形動|副|サ|五|一|感|連|接)[・?？/\s]+)+)+"
)
_S2T_NORM = str.maketrans(
    "学语词义觉听说见现关开门间时书车东飞机气电话读写买卖进过还这个们实际边点头脸声响乐欢爱恋梦变动运经验结构组织认识记忆决定选择应该帮助给让离别对错难简单复杂强弱轻长远广满静温热凉干湿脏净坏伤惊担忧烦恼愿望兴够准备计划练习试战胜负护救杀醒飞游击碎断烧扫画唱弹照录播传送借换赔赚费贵贱穷约规则律权责任务业职馆院厂场港桥路镇城国陆岛湾雾雷风云阳阴树叶种麦鸟鸡鸭鹅猪猫马龙龟虫兽严厉征应体烫发内众",
    "學語詞義覺聽說見現關開門間時書車東飛機氣電話讀寫買賣進過還這個們實際邊點頭臉聲響樂歡愛戀夢變動運經驗結構組織認識記憶決定選擇應該幫助給讓離別對錯難簡單複雜強弱輕長遠廣滿靜溫熱涼乾濕髒淨壞傷驚擔憂煩惱願望興夠準備計劃練習試戰勝負護救殺醒飛游擊碎斷燒掃畫唱彈照錄播傳送借換賠賺費貴賤窮約規則律權責任務業職館院廠場港橋路鎮城國陸島灣霧雷風雲陽陰樹葉種麥鳥雞鴨鵝豬貓馬龍龜蟲獸嚴厲徵應體燙發內眾",
)
_ATOM_SPLIT_RE = re.compile(r"[、，,；;／/\s。！？!?·・]+")
_PAREN_STRIP_RE = re.compile(r"[（\(][^）\)]*[）\)]")


def _is_chinese_gloss(text: str) -> bool:
    """判斷釋義是否為真正的中文釋義（排除英文用法如 'as 下から, 下より, etc.' 與純日文短句）。"""
    if not text:
        return False
    hanzi = len(re.findall(r"[\u4e00-\u9fff]", text))
    if hanzi == 0:
        return False
    ascii_letters = len(re.findall(r"[A-Za-z]", text))
    if ascii_letters >= hanzi * 2 and ascii_letters >= 4:
        return False
    kana = len(re.findall(r"[ぁ-んァ-ヶ]", text))
    if kana > hanzi and kana >= 2:
        return False
    return True


def _sense_atoms(text: str) -> set[str]:
    """將一條釋義拆成標準化語意原子集合（簡繁歸一、去括號補充），供跨詞典去重使用。"""
    no_paren = _PAREN_STRIP_RE.sub("", text).translate(_S2T_NORM).lower()
    atoms: set[str] = set()
    for part in _ATOM_SPLIT_RE.split(no_paren):
        p = part.strip("「」『』\"'.,:-_~〜")
        if p:
            atoms.add(p)
    return atoms


def _senses_semantically_overlap(a: str, b: str) -> bool:
    """判斷兩條釋義是否實質重複或互為子集（例如「外面、外部、戶外」與「外面」）。"""
    aa = _sense_atoms(a)
    bb = _sense_atoms(b)
    if not aa or not bb:
        return False
    if bb <= aa or aa <= bb:
        return True
    inter = len(aa & bb)
    if inter == 0:
        return False
    return (inter / min(len(aa), len(bb))) >= 0.6


def _extract_senses_dedup(entries: list[DictEntry], max_senses: int = 6) -> list[str]:
    return extract_concise_senses(entries, max_senses=max_senses)

_ETYMOLOGY_HEAD_RE = re.compile(r"^(?:词源|詞源|語源|语源|Etymology\b)")
_ETYMOLOGY_CONT_RE = re.compile(
    r"(?:词源|詞源|語源|语源|派生自|源自|原意|上古|中古|中世|室町|平安|江戸|江户|古旧|同源|原始日語|原始日语)"
)
_WTY_EX_COUNT_RE = re.compile(r"(\d+)\s*例")
_HAS_KANJI_RE = re.compile(r"[\u4e00-\u9fff々]")
_HAS_JP_KANA_RE = re.compile(r"[ぁ-んァ-ン]")


def _clean_gloss_noise(text: str) -> str:
    """移除 Wiktionary 雜訊頁尾、重定向標記、冗長詞源開場白並美化例句換行。"""
    raw_lines = [
        ln.strip()
        for ln in text.splitlines()
        if ln.strip()
        and "Wiktionary | Kaikki" not in ln
        and "redirected from" not in ln.lower()
    ]
    if not raw_lines:
        return ""

    lines = list(raw_lines)
    if _ETYMOLOGY_HEAD_RE.match(lines[0]):
        idx = 1
        while idx < len(lines) and _ETYMOLOGY_CONT_RE.search(lines[idx]):
            idx += 1
        if idx < len(lines):
            lines = lines[idx:]

    formatted: list[str] = []
    for ln in lines:
        # 保留例句數標記供 extract_concise_senses 精準略過後續 N 組例句＋譯文
        pretty = _WTY_EX_COUNT_RE.sub(r"\n　例[\1]：", ln)
        formatted.append(pretty)
    return "\n".join(formatted).strip()


_EX_COUNT_MARKER_RE = re.compile(r"^[　\s]*例\[(\d+)\]：")


def extract_concise_senses(entries: list[DictEntry], max_senses: int = 6) -> list[str]:
    """從多本詞典中提煉出乾淨、不重複、去例句雜訊且聚焦重點的獨立核心義項。"""
    senses: list[str] = []
    seen_norm: set[str] = set()
    seen_atoms: set[str] = set()
    is_mono_only = bool(entries) and all(e.dict_kind == "monolingual" for e in entries)
    head_terms = {e.term for e in entries if e.term}

    for entry in entries[:6]:
        for g in entry.glosses[:5]:
            cleaned = _clean_gloss_noise(g)
            if not cleaned:
                continue
            skip_remaining = 0
            for raw_ln in cleaned.splitlines():
                ln = raw_ln.strip()
                if not ln:
                    continue
                # 跳過 Wiktionary 引用文獻／ISBN 出處行，且不消耗例句跳行額度
                if _CITATION_LINE_RE.search(ln):
                    continue
                em = _EX_COUNT_MARKER_RE.match(ln)
                if em:
                    # 雙語 Wiktionary 每組例句為「日文＋中文譯文」2 行；日日詞典僅 1 行日文例句
                    n_ex = int(em.group(1))
                    if is_mono_only:
                        skip_remaining = max(0, min(8, n_ex - 1))
                    else:
                        skip_remaining = max(1, min(12, n_ex * 2 - 1))
                    continue
                if ln.startswith("例：") or ln.startswith("　例："):
                    skip_remaining = max(skip_remaining, 0 if is_mono_only else 1)
                    continue
                if skip_remaining > 0:
                    skip_remaining -= 1
                    continue
                if _SKIP_SENSE_LINE_RE.match(ln):
                    continue
                if is_mono_only and _ASCII_DOMAIN_TAG_RE.match(ln):
                    continue
                if _INFLECTION_STUB_RE.search(ln):
                    continue
                # 雙語模式過濾掉 Wiktionary 夾雜的帶振假名日文例句（如「この学(がっ)校(こう)は...」）
                if not is_mono_only and _INLINE_FURIGANA_RE.search(ln):
                    skip_remaining = max(skip_remaining, 1)
                    continue
                # 雙語詞條中排除整句日文例句（含假名且帶日文句末標點），並同步跳過緊接其後的中文譯文
                if not is_mono_only:
                    kana_cnt = len(re.findall(r"[ぁ-んァ-ン]", ln))
                    if kana_cnt >= 3 and ln.endswith(("。", "！", "？", "‼", "!", "?")):
                        skip_remaining = max(skip_remaining, 1)
                        continue
                    # 排除短日文異體／交叉引用（如「分る」「募集する」「言い募る」）
                    if kana_cnt >= 1 and len(ln) <= 6 and not re.search(r"[，,、。；;]", ln):
                        continue

                if re.match(r"^[\u3040-\u9fff々ー]+【[ぁ-んァ-ンー]+】$", ln):
                    continue

                # 若單行內含多個編號義項（如「①越來越強烈 ②募集、招募」），拆分為獨立候選義項以便個別去重與語境排序
                sub_parts = (
                    [p.strip() for p in _INLINE_CIRCLED_SPLIT_RE.split(ln) if p.strip()]
                    if ("①" in ln and "②" in ln)
                    else [ln]
                )
                for part in sub_parts:
                    # 清理前綴殘留詞性標記（如「名?自 サ 应招」→「应招」、「1. 热空气。」→「热空气」）
                    cur = re.sub(
                        r"^(?:[15]-dan|ichidan|godan|suru|kuru)(?:in)?transitive\s*",
                        "",
                        part,
                        flags=re.IGNORECASE,
                    ).strip()
                    if not is_mono_only:
                        cur = _LEADING_POS_NOISE_RE.sub("", cur).strip()
                    else:
                        cur = re.sub(r"^[①-⑨]\s*", "", cur).strip()
                    cur = cur.strip("。；;")
                    if not cur or cur in head_terms or _SKIP_SENSE_LINE_RE.match(cur):
                        continue

                    compact = cur[:72] + ("…" if len(cur) > 72 else "")
                    norm = re.sub(r"[\s，,、。；;・]+", "", compact.translate(_S2T_NORM).lower())
                    if not norm or norm in seen_norm:
                        continue

                    # 語意原子子集／高重疊去重：徹底消除「外面、外部、戶外」之後又出現「外面」的重複現象！
                    atoms = _sense_atoms(compact)
                    if atoms:
                        if atoms <= seen_atoms:
                            continue
                        if any(_senses_semantically_overlap(compact, prev) for prev in senses):
                            continue

                    seen_norm.add(norm)
                    seen_atoms.update(atoms)
                    senses.append(compact)
                    if len(senses) >= max_senses * 3:
                        break
                if len(senses) >= max_senses * 3:
                    break
        if len(senses) >= max_senses * 3:
            break

    # 雙語模式：只要已有真正中文核心釋義，就直接隱藏多餘英文詞典重複義項，讓 Anki 卡片與膠囊聚焦中文重點！
    if not is_mono_only:
        zh_senses = [s for s in senses if _is_chinese_gloss(s)]
        if zh_senses:
            return zh_senses[:max_senses]
    return senses[:max_senses]


def _is_inflection_stub_entries(entries: list[DictEntry]) -> bool:
    """判斷查到的詞條是否全都是屈折存根、重定向或方言/口語縮略干擾（如「怖く -> 怖いcontinuative」或方言「したら」）。"""
    if not entries:
        return False
    # 若最高優先條目全部標記為 dialect / non-lemma / col / abbr，應優先嘗試標準語活用還原（如 したら -> する）
    if all(
        any(tag in e.pos.lower() for tag in ("dialect", "non-lemma", "char", "col", "abbr"))
        for e in entries[:2]
    ):
        return True
    for entry in entries:
        for g in entry.glosses:
            for ln in _clean_gloss_noise(g).splitlines():
                if ln.strip() and not _INFLECTION_STUB_RE.search(ln):
                    return False
    return True


def _extract_yomitan_text(node: Any) -> str:
    """遞迴解析 Yomitan structured-content 與純字串釋義為乾淨文本。"""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        parts = [_extract_yomitan_text(item) for item in node]
        return "".join(p for p in parts if p)
    if isinstance(node, dict):
        node_type = node.get("type")
        if node_type == "structured-content":
            return _extract_yomitan_text(node.get("content"))
        if node_type == "text":
            return str(node.get("text", ""))
        tag = node.get("tag", "")
        content = _extract_yomitan_text(node.get("content"))
        if tag in ("li", "div", "p", "br"):
            return f"{content}\n" if content else "\n"
        if tag == "rt":
            return f"({content})" if content else ""
        return content
    return str(node)


def _guess_dict_kind(title: str, sample_gloss: str = "") -> str:
    """自動推斷匯入詞典是雙語還是日日單語。"""
    lower = title.lower()
    if any(k in lower for k in ("ja-ja", "大辞林", "大辞泉", "明鏡", "新明解", "広辞苑", "三省堂", "国語", "旺文社", "岩波", "monolingual")):
        return "monolingual"
    if any(k in lower for k in ("ja-zh", "zh", "中", "jmdict", "jitendex", "en", "雙語", "双语", "漢", "汉")):
        return "bilingual"
    # 若釋義含大量假名且幾乎無中文常見簡繁助字/英文單字，視為單語
    kana_count = len(re.findall(r"[ぁ-んァ-ン]", sample_gloss))
    if kana_count >= max(6, len(sample_gloss) * 0.25):
        return "monolingual"
    return "bilingual"


class DictionaryEngine:
    """本地 SQLite 詞典檢索與管理引擎。"""

    def __init__(self, db_path: Path | None = None, load_starter: bool = True) -> None:
        self.db_path = db_path or (get_data_dir() / "dictionaries.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        if load_starter:
            self.ensure_starter_loaded()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;

            CREATE TABLE IF NOT EXISTS dictionaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                kind TEXT NOT NULL DEFAULT 'bilingual',
                revision TEXT DEFAULT '',
                entry_count INTEGER NOT NULL DEFAULT 0,
                priority INTEGER NOT NULL DEFAULT 100,
                enabled INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS terms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dict_id INTEGER NOT NULL,
                term TEXT NOT NULL,
                reading TEXT NOT NULL,
                pos TEXT NOT NULL DEFAULT '',
                score INTEGER NOT NULL DEFAULT 0,
                glosses_json TEXT NOT NULL,
                FOREIGN KEY (dict_id) REFERENCES dictionaries(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS term_meta (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dict_id INTEGER NOT NULL,
                term TEXT NOT NULL,
                reading TEXT NOT NULL DEFAULT '',
                mode TEXT NOT NULL,
                data_json TEXT NOT NULL,
                FOREIGN KEY (dict_id) REFERENCES dictionaries(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_terms_term ON terms(term);
            CREATE INDEX IF NOT EXISTS idx_terms_reading ON terms(reading);
            CREATE INDEX IF NOT EXISTS idx_meta_term ON term_meta(term);
            """
        )
        # 安全遷移既有資料庫：補上 enabled 欄位（不遺失任何已安裝詞典）
        cols = {row["name"] for row in cur.execute("PRAGMA table_info(dictionaries)").fetchall()}
        if "enabled" not in cols:
            cur.execute("ALTER TABLE dictionaries ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1")
        self._conn.commit()

    def list_dictionaries(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, kind, revision, entry_count, priority, enabled "
                "FROM dictionaries ORDER BY priority ASC, id ASC"
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["enabled"] = bool(d.get("enabled", 1))
            out.append(d)
        return out

    def set_dictionary_enabled(self, dict_id: int, enabled: bool) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE dictionaries SET enabled = ? WHERE id = ?",
                (1 if enabled else 0, dict_id),
            )
            self._conn.commit()

    def move_dictionary_priority(self, dict_id: int, direction: int) -> None:
        """上移 (-1) 或下移 (+1) 詞典優先順序。"""
        with self._lock:
            dicts = self.list_dictionaries()
            idx = next((i for i, d in enumerate(dicts) if d["id"] == dict_id), -1)
            if idx < 0:
                return
            target_idx = idx + (-1 if direction < 0 else 1)
            if not (0 <= target_idx < len(dicts)):
                return
            dicts[idx], dicts[target_idx] = dicts[target_idx], dicts[idx]
            for pos, d in enumerate(dicts):
                self._conn.execute(
                    "UPDATE dictionaries SET priority = ? WHERE id = ?",
                    ((pos + 1) * 10, d["id"]),
                )
            self._conn.commit()

    def apply_dictionary_preset(self, preset: str) -> None:
        """一鍵套用學習階段詞典啟用組合：zh_focus（只開中文＋音調詞頻）/ mono_focus（日日單語）/ all。"""
        with self._lock:
            dicts = self.list_dictionaries()
            for d in dicts:
                name_low = d["name"].lower()
                kind = d["kind"]
                if preset == "all":
                    enabled = True
                elif preset == "zh_focus":
                    # 保留中文雙語詞典 + 音調/詞頻 + 內建精選中文；關閉純英文 JMdict / Jitendex 避免雜訊！
                    is_en_only = ("jmdict" in name_low or "jitendex" in name_low)
                    enabled = (kind == "meta") or (kind == "bilingual" and not is_en_only)
                elif preset == "mono_focus":
                    # Lv.3 登樓撤梯：以日日單語 + 音調/詞頻為主，只留日中當安全網，關掉英文
                    is_en_only = ("jmdict" in name_low or "jitendex" in name_low)
                    enabled = (kind in ("monolingual", "meta")) or (kind == "bilingual" and not is_en_only)
                else:
                    enabled = True
                self._conn.execute(
                    "UPDATE dictionaries SET enabled = ? WHERE id = ?",
                    (1 if enabled else 0, d["id"]),
                )
            self._conn.commit()

    def find_installed_for_catalog(self, catalog_id: str) -> dict[str, Any] | None:
        """檢查目錄中的開源詞典是否已經安裝於本機，若已安裝回傳其資訊。"""
        spec = next((c for c in FREE_DICTIONARY_CATALOG if c["id"] == catalog_id), None)
        if not spec:
            return None
        keywords = [k.lower() for k in spec.get("match_keywords", [catalog_id])]
        for d in self.list_dictionaries():
            low = d["name"].lower()
            if any(kw in low for kw in keywords):
                return d
        return None

    def delete_dictionary(self, dict_id: int) -> None:
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("DELETE FROM terms WHERE dict_id = ?", (dict_id,))
            self._conn.execute("DELETE FROM term_meta WHERE dict_id = ?", (dict_id,))
            self._conn.execute("DELETE FROM dictionaries WHERE id = ?", (dict_id,))
            self._conn.commit()

    def ensure_starter_loaded(self) -> None:
        """確保內建口語核心詞庫（優先序 #1）與攻略精選詞庫已載入。"""
        self._load_builtin_json_dict(
            json_name="core_spoken_dict.json",
            bi_name="內建口語核心詞庫（雙語）",
            mono_name="內建口語核心詞庫（單語）",
            revision="core-spoken-1",
            bi_priority=1,
            mono_priority=2,
        )
        self._load_builtin_json_dict(
            json_name="starter_dict.json",
            bi_name="內建精選雙語詞庫",
            mono_name="內建精選單語詞庫",
            revision="starter-1",
            bi_priority=900,
            mono_priority=901,
        )

    def _load_builtin_json_dict(
        self,
        json_name: str,
        bi_name: str,
        mono_name: str,
        revision: str,
        bi_priority: int,
        mono_priority: int,
    ) -> None:
        existing = self._conn.execute(
            "SELECT id FROM dictionaries WHERE name = ?", (bi_name,)
        ).fetchone()
        if existing:
            return

        data_path = Path(__file__).resolve().parent.parent / "data" / json_name
        if not data_path.exists():
            return
        items = json.loads(data_path.read_text(encoding="utf-8"))

        cur = self._conn.cursor()
        cur.execute(
            "INSERT INTO dictionaries (name, kind, revision, entry_count, priority, enabled) VALUES (?, ?, ?, ?, ?, 1)",
            (bi_name, "bilingual", revision, len(items), bi_priority),
        )
        bi_id = cur.lastrowid

        cur.execute(
            "INSERT INTO dictionaries (name, kind, revision, entry_count, priority, enabled) VALUES (?, ?, ?, ?, ?, 1)",
            (mono_name, "monolingual", revision, len(items), mono_priority),
        )
        mono_id = cur.lastrowid

        bi_rows = []
        mono_rows = []
        meta_rows = []
        for item in items:
            term = item["term"]
            reading = item["reading"]
            pos = item.get("pos", "")
            zh = item.get("zh", "")
            ja = item.get("ja", "")
            if zh:
                bi_rows.append((bi_id, term, reading, pos, 50, json.dumps([zh], ensure_ascii=False)))
            if ja:
                mono_rows.append((mono_id, term, reading, pos, 50, json.dumps([ja], ensure_ascii=False)))
            pitch = item.get("pitch")
            if pitch is not None:
                meta_rows.append((bi_id, term, reading, "pitch", json.dumps({"pitches": [str(pitch)]})))
            freq = item.get("freq")
            if freq is not None:
                meta_rows.append((bi_id, term, reading, "freq", json.dumps({"frequency": int(freq)})))

        cur.executemany(
            "INSERT INTO terms (dict_id, term, reading, pos, score, glosses_json) VALUES (?, ?, ?, ?, ?, ?)",
            bi_rows + mono_rows,
        )
        cur.executemany(
            "INSERT INTO term_meta (dict_id, term, reading, mode, data_json) VALUES (?, ?, ?, ?, ?)",
            meta_rows,
        )
        self._conn.commit()

    def import_yomitan_zip(
        self,
        zip_path: Path,
        kind_override: str | None = None,
        progress: ProgressCallback | None = None,
        priority_override: int | None = None,
    ) -> dict[str, Any]:
        """匯入任意標準 Yomitan .zip 詞典（完整支援詞條、音調與詞頻檔）。"""
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            if "index.json" not in names:
                raise ValueError("此壓縮檔不含 index.json，非標準 Yomitan 詞典格式。")

            index_data = json.loads(zf.read("index.json").decode("utf-8"))
            title = str(index_data.get("title") or zip_path.stem)
            revision = str(index_data.get("revision") or "")

            term_banks = sorted(n for n in names if re.match(r"^(?:.*/)?term_bank_\d+\.json$", n))
            meta_banks = sorted(n for n in names if re.match(r"^(?:.*/)?term_meta_bank_\d+\.json$", n))

            sample_gloss = ""
            if term_banks:
                first_entries = json.loads(zf.read(term_banks[0]).decode("utf-8"))
                for row in first_entries[:10]:
                    if len(row) >= 6:
                        txt = _extract_yomitan_text(row[5]).strip()
                        if txt:
                            sample_gloss += txt + " "

            if kind_override:
                kind = kind_override
            elif not term_banks and meta_banks:
                kind = "meta"
            else:
                kind = _guess_dict_kind(title, sample_gloss)

            # 若同名詞典已存在，先刪除重建
            existing = self._conn.execute(
                "SELECT id FROM dictionaries WHERE name = ?", (title,)
            ).fetchone()
            if existing:
                self.delete_dictionary(existing["id"])

            if priority_override is not None:
                priority = priority_override
            elif kind == "bilingual":
                priority = 5 if any(k in title.lower() for k in ("zh", "中", "漢", "汉")) else 20
            elif kind == "monolingual":
                priority = 15
            else:
                priority = 40

            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO dictionaries (name, kind, revision, entry_count, priority) VALUES (?, ?, ?, ?, ?)",
                (title, kind, revision, 0, priority),
            )
            dict_id = cur.lastrowid

            total_files = max(1, len(term_banks) + len(meta_banks))
            processed = 0
            total_terms = 0
            total_meta = 0
            batch: list[tuple[Any, ...]] = []

            for bank_name in term_banks:
                entries = json.loads(zf.read(bank_name).decode("utf-8"))
                for row in entries:
                    if not isinstance(row, list) or len(row) < 6:
                        continue
                    term = str(row[0] or "").strip()
                    reading = str(row[1] or "").strip() or term
                    tags = str(row[2] or "")
                    rules = str(row[3] or "")
                    pos = ",".join(p for p in (rules, tags) if p)
                    score = int(row[4] or 0) if isinstance(row[4], (int, float)) else 0
                    raw_glosses = row[5]
                    gloss_list: list[str] = []
                    if isinstance(raw_glosses, list):
                        for g in raw_glosses:
                            txt = re.sub(r"\n{2,}", "\n", _extract_yomitan_text(g)).strip()
                            if txt:
                                gloss_list.append(txt)
                    else:
                        txt = _extract_yomitan_text(raw_glosses).strip()
                        if txt:
                            gloss_list.append(txt)

                    if not term or not gloss_list:
                        continue
                    # 某些日中詞典（如 wty-ja-zh）將假名讀音寫在首行「詞【かな】」而留空 reading 欄，自動補提取
                    if reading == term and gloss_list:
                        rm = re.search(r"【([ぁ-んァ-ンー]+)】", gloss_list[0].splitlines()[0])
                        if rm:
                            reading = rm.group(1)
                    batch.append(
                        (dict_id, term, reading, pos, score, json.dumps(gloss_list, ensure_ascii=False))
                    )
                    total_terms += 1
                    if len(batch) >= 5000:
                        cur.executemany(
                            "INSERT INTO terms (dict_id, term, reading, pos, score, glosses_json) VALUES (?, ?, ?, ?, ?, ?)",
                            batch,
                        )
                        batch.clear()

                processed += 1
                if progress:
                    progress(f"匯入詞條 {title} ({processed}/{total_files})", int(processed * 100 / total_files))

            if batch:
                cur.executemany(
                    "INSERT INTO terms (dict_id, term, reading, pos, score, glosses_json) VALUES (?, ?, ?, ?, ?, ?)",
                    batch,
                )
                batch.clear()

            meta_batch: list[tuple[Any, ...]] = []
            for bank_name in meta_banks:
                entries = json.loads(zf.read(bank_name).decode("utf-8"))
                for row in entries:
                    if not isinstance(row, list) or len(row) < 3:
                        continue
                    term = str(row[0] or "").strip()
                    mode = str(row[1] or "").strip()
                    data = row[2]
                    reading = ""
                    if isinstance(data, dict):
                        reading = str(data.get("reading") or "")
                    if mode == "pitch" and isinstance(data, dict):
                        positions = [
                            str(p.get("position"))
                            for p in data.get("pitches", [])
                            if isinstance(p, dict) and p.get("position") is not None
                        ]
                        if positions:
                            meta_batch.append(
                                (dict_id, term, reading, "pitch", json.dumps({"pitches": positions}))
                            )
                            total_meta += 1
                    elif mode == "freq":
                        freq_val = None
                        if isinstance(data, int):
                            freq_val = data
                        elif isinstance(data, dict):
                            fv = data.get("frequency", data.get("value"))
                            if isinstance(fv, dict):
                                fv = fv.get("value")
                            if isinstance(fv, int):
                                freq_val = fv
                        if freq_val is not None:
                            meta_batch.append(
                                (dict_id, term, reading, "freq", json.dumps({"frequency": freq_val}))
                            )
                            total_meta += 1
                    if len(meta_batch) >= 5000:
                        cur.executemany(
                            "INSERT INTO term_meta (dict_id, term, reading, mode, data_json) VALUES (?, ?, ?, ?, ?)",
                            meta_batch,
                        )
                        meta_batch.clear()

                processed += 1
                if progress:
                    progress(f"匯入音調/詞頻 {title} ({processed}/{total_files})", int(processed * 100 / total_files))

            if meta_batch:
                cur.executemany(
                    "INSERT INTO term_meta (dict_id, term, reading, mode, data_json) VALUES (?, ?, ?, ?, ?)",
                    meta_batch,
                )

            final_count = total_terms if total_terms > 0 else total_meta
            cur.execute("UPDATE dictionaries SET entry_count = ? WHERE id = ?", (final_count, dict_id))
            self._conn.commit()

            return {"id": dict_id, "name": title, "kind": kind, "entry_count": final_count}

    def download_and_import_free_dictionary(
        self,
        catalog_id: str,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """從驗證過的官方直連位址一鍵下載並匯入開源 Yomitan 詞典。"""
        spec = next((c for c in FREE_DICTIONARY_CATALOG if c["id"] == catalog_id), None)
        if not spec:
            raise ValueError(f"未知的詞典編號：{catalog_id}")

        dl_url = str(spec.get("direct_url") or "")
        if not dl_url:
            raise RuntimeError(f"詞典 {catalog_id} 缺少下載位址。")
        asset_name = dl_url.rstrip("/").split("/")[-1]
        if progress:
            progress(f"開始下載 {asset_name}...", 5)

        with httpx.Client(timeout=120.0, follow_redirects=True) as client:
            with tempfile.TemporaryDirectory(prefix="galanki_dict_") as tmpdir:
                zip_path = Path(tmpdir) / asset_name
                downloaded = 0
                with client.stream("GET", dl_url, headers={"User-Agent": "GalAnki/0.1"}) as stream:
                    stream.raise_for_status()
                    total_size = int(stream.headers.get("Content-Length") or 0)
                    with zip_path.open("wb") as fh:
                        for chunk in stream.iter_bytes(chunk_size=131072):
                            fh.write(chunk)
                            downloaded += len(chunk)
                            if progress:
                                mb = downloaded / (1024 * 1024)
                                if total_size > 0:
                                    pct = 5 + int(downloaded * 50 / total_size)
                                    total_mb = total_size / (1024 * 1024)
                                    progress(f"下載 {asset_name} ({mb:.1f}/{total_mb:.1f} MB)...", min(55, pct))
                                else:
                                    progress(f"下載 {asset_name} ({mb:.1f} MB)...", 30)

                def import_cb(msg: str, pct: int) -> None:
                    if progress:
                        progress(msg, 55 + int(pct * 45 / 100))

                return self.import_yomitan_zip(
                    zip_path,
                    kind_override=spec["kind"],
                    progress=import_cb,
                    priority_override=spec.get("priority"),
                )

    def download_recommended_bundle(
        self,
        progress: ProgressCallback | None = None,
    ) -> list[dict[str, Any]]:
        """一鍵下載並安裝「最佳黃金搭配」全套 5 部詞典（日中 + 日日 + Jitendex + Kanjium音調 + JPDB詞頻）。"""
        bundle = [c for c in FREE_DICTIONARY_CATALOG if c.get("recommended_bundle")]
        results: list[dict[str, Any]] = []
        total = max(1, len(bundle))
        for idx, spec in enumerate(bundle):
            base_pct = int(idx * 100 / total)
            span = int(100 / total)

            def _sub_cb(msg: str, pct: int, b: int = base_pct, s: int = span, i: int = idx) -> None:
                if progress:
                    overall = min(99, b + int(pct * s / 100))
                    progress(f"[{i + 1}/{total}] {msg}", overall)

            res = self.download_and_import_free_dictionary(spec["id"], progress=_sub_cb)
            results.append(res)

        if progress:
            progress(f"✓ 最佳黃金搭配 {len(results)} 套詞典全部安裝完成！", 100)
        return results

    def _query_exact(self, word: str) -> list[DictEntry]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT t.term, t.reading, t.pos, t.score, t.glosses_json,
                       d.name AS dict_name, d.kind AS dict_kind, d.priority
                FROM terms t
                JOIN dictionaries d ON d.id = t.dict_id
                WHERE d.enabled = 1 AND (t.term = ? OR t.reading = ?)
                ORDER BY
                    (CASE WHEN t.term = ? THEN 0 ELSE 1 END),
                    (CASE
                        WHEN t.pos IN ('char', 'non-lemma')
                             OR LOWER(t.pos) LIKE '%dialect%'
                             OR t.reading = ''
                        THEN 1 ELSE 0
                    END),
                    d.priority ASC,
                    t.score DESC
                LIMIT 36
                """,
                (word, word, word),
            ).fetchall()

        entries: list[DictEntry] = []
        for r in rows:
            raw_json = r["glosses_json"]
            if not raw_json:
                continue
            raw_glosses = json.loads(raw_json)
            cleaned_glosses = [c for g in raw_glosses if (c := _clean_gloss_noise(str(g)))]
            if not cleaned_glosses:
                continue
            entries.append(
                DictEntry(
                    term=r["term"],
                    reading=r["reading"],
                    pos=r["pos"],
                    glosses=cleaned_glosses,
                    dict_name=r["dict_name"],
                    dict_kind=r["dict_kind"],
                    score=r["score"],
                )
            )

        # 將「漢字詞條卻沒有平假名讀音 (reading 含漢字)」或純存根條目往後排，確保 事 -> こと 而非 事
        def _rank_entry(e: DictEntry) -> tuple[int, int]:
            bad_reading = 1 if (not e.reading or (_HAS_KANJI_RE.search(e.term) and _HAS_KANJI_RE.search(e.reading))) else 0
            bad_pos = 1 if (e.pos.lower() in ("char", "non-lemma") or "dialect" in e.pos.lower()) else 0
            return (bad_pos, bad_reading)

        entries.sort(key=_rank_entry)
        return entries

    def _query_meta(self, term: str, reading: str) -> tuple[list[str], int | None]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT m.mode, m.reading, m.data_json
                FROM term_meta m
                JOIN dictionaries d ON d.id = m.dict_id
                WHERE d.enabled = 1 AND m.term = ?
                """,
                (term,),
            ).fetchall()
        pitches: list[str] = []
        best_freq: int | None = None
        for r in rows:
            if r["reading"] and reading and r["reading"] != reading:
                continue
            raw_data = r["data_json"]
            if not raw_data:
                continue
            data = json.loads(raw_data)
            if r["mode"] == "pitch":
                for p in data.get("pitches", []):
                    if p not in pitches:
                        pitches.append(p)
            elif r["mode"] == "freq":
                val = data.get("frequency")
                if isinstance(val, int) and (best_freq is None or val < best_freq):
                    best_freq = val
        return pitches, best_freq

    def batch_lookup_frequencies(self, terms: list[str] | set[str]) -> dict[str, int]:
        """批次查詢多個詞條的最高優先（數值最小）詞頻排名，供 1T 黃金句礦場高速排序。"""
        unique = [t for t in set(terms) if t]
        if not unique:
            return {}
        out: dict[str, int] = {}
        chunk_size = 400
        for i in range(0, len(unique), chunk_size):
            chunk = unique[i : i + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            with self._lock:
                rows = self._conn.execute(
                    f"""
                    SELECT m.term, m.data_json
                    FROM term_meta m
                    JOIN dictionaries d ON d.id = m.dict_id
                    WHERE d.enabled = 1 AND m.mode = 'freq' AND m.term IN ({placeholders})
                    """,
                    chunk,
                ).fetchall()
            for r in rows:
                try:
                    val = json.loads(r["data_json"]).get("frequency")
                except (ValueError, TypeError):
                    continue
                if isinstance(val, int):
                    term = str(r["term"])
                    if term not in out or val < out[term]:
                        out[term] = val
        return out

    def _pos_hint_matches(self, entry_pos: str, pos_hint: str) -> bool:
        if not pos_hint or not entry_pos:
            return False
        ep = entry_pos.lower()
        if pos_hint == "動詞":
            return any(k in ep for k in ("v1", "v5", "vs", "vk", "verb", "動詞", "一段", "五段", "サ変", "カ変")) or ep == "v"
        if pos_hint == "形容詞":
            return any(k in ep for k in ("adj-i", "形容詞", "i-adj"))
        if pos_hint == "名詞":
            return any(k in ep for k in ("n", "名", "pron"))
        if pos_hint == "副詞":
            return any(k in ep for k in ("adv", "副"))
        if pos_hint == "感動詞":
            return any(k in ep for k in ("intj", "感", "exp"))
        if pos_hint == "連体詞":
            return any(k in ep for k in ("adj-pn", "adj_noun", "連体"))
        return False

    def _freq_bonus(self, freq: int | None) -> int:
        if freq is None:
            return 0
        if freq <= 250:
            return 5200
        if freq <= 1000:
            return 3800
        if freq <= 3000:
            return 2500
        if freq <= 8000:
            return 1400
        if freq <= 20000:
            return 600
        return 100

    def lookup_exact_or_deinflect(
        self,
        surface: str,
        pos_hint: str = "",
        reading_hint: str = "",
        lemma_hint: str = "",
    ) -> LookupMatch | None:
        """多候選詞頻＋詞性＋讀音聯合消歧查詢（徹底解決「わかった誤判分かつ」等冷門詞覆蓋高頻詞問題！）。"""
        if not surface:
            return None

        candidates = list(deinflect(surface))
        # 若 Janome 形態素解析器已給出明確原型（如 わかっ+た -> わかる），優先加入候選清單
        if lemma_hint and not any(c.term == lemma_hint for c in candidates):
            from galgame_anki.core.deinflector import DeinflectCandidate

            candidates.insert(0, DeinflectCandidate(term=lemma_hint, rules=RULE_NONE, reasons=()))

        scored_matches: list[tuple[int, LookupMatch]] = []
        seen_keys: set[tuple[str, str]] = set()

        for cand in candidates:
            raw_entries = self._query_exact(cand.term)
            if not raw_entries:
                continue

            matched_entries = [
                e for e in raw_entries
                if cand.rules == RULE_NONE or pos_matches_rule(e.pos, cand.rules)
            ]
            if not matched_entries:
                continue

            # 若有讀音提示或詞性提示，在同一候選內優先把最吻合的條目排到前面（且保留口語核心詞庫最高優先）
            if reading_hint or pos_hint:
                matched_entries.sort(
                    key=lambda e: (
                        0 if "口語核心詞庫" in e.dict_name else 1,
                        0 if (reading_hint and e.reading == reading_hint) else 1,
                        0 if (pos_hint and self._pos_hint_matches(e.pos, pos_hint)) else 1,
                    )
                )

            # 按 (term, reading) 分組，讓同讀音不同漢字（如 わかる -> 分かる vs わかつ -> 分かつ）各自獨立競爭！
            grouped: dict[tuple[str, str], list[DictEntry]] = {}
            for e in matched_entries:
                rd = e.reading
                if _HAS_KANJI_RE.search(e.term) and (not rd or _HAS_KANJI_RE.search(rd)):
                    rd = reading_hint or rd
                key = (e.term, rd)
                grouped.setdefault(key, []).append(e)

            for (lemma, reading), grp_entries in list(grouped.items())[:4]:
                if (lemma, reading) in seen_keys:
                    continue
                seen_keys.add((lemma, reading))

                pitches, freq = self._query_meta(lemma, reading)
                if freq is None and reading and reading != lemma:
                    p2, f2 = self._query_meta(reading, reading)
                    if not pitches:
                        pitches = p2
                    freq = f2

                bi = [e for e in grp_entries if e.dict_kind == "bilingual"]
                mono = [e for e in grp_entries if e.dict_kind == "monolingual"]

                for e in grp_entries:
                    e.pitches = pitches
                    e.frequency = freq

                match_obj = LookupMatch(
                    surface=surface,
                    lemma=lemma,
                    reading=reading,
                    reasons=cand.reasons,
                    bilingual_entries=bi,
                    monolingual_entries=mono,
                    pitches=pitches,
                    frequency=freq,
                )

                # 計算多維度語言學消歧分數（徹底告別「撞到第一個冷門詞就回傳」！）
                score = self._freq_bonus(freq)
                is_stub = cand.rules == RULE_NONE and _is_inflection_stub_entries(grp_entries)
                if is_stub:
                    score -= 5500
                elif cand.rules == RULE_NONE and lemma == surface:
                    score += 1200

                if lemma_hint and (lemma == lemma_hint or reading == lemma_hint):
                    score += 8000
                if reading_hint and reading == reading_hint:
                    score += 3200
                if pos_hint and any(self._pos_hint_matches(e.pos, pos_hint) for e in grp_entries):
                    score += 2400
                if any("口語核心詞庫" in e.dict_name for e in grp_entries):
                    score += 1800
                if bi:
                    score += 400
                # 還原步數越少越自然（避免過度多層誤還原）
                score -= len(cand.reasons) * 180

                scored_matches.append((score, match_obj))

        if not scored_matches:
            return None

        scored_matches.sort(key=lambda pair: pair[0], reverse=True)
        best = scored_matches[0][1]
        # 保留最多 3 個不同原形的備選詞（如 あった -> ある / 会う / 合う），介面可 1 點切換！
        alts: list[LookupMatch] = []
        seen_alt_lemmas = {best.lemma}
        for _, cand_match in scored_matches[1:]:
            if cand_match.lemma not in seen_alt_lemmas and cand_match.all_entries:
                seen_alt_lemmas.add(cand_match.lemma)
                alts.append(cand_match)
                if len(alts) >= 3:
                    break
        best.alt_matches = alts
        return best

    def lookup_at_offset(self, text: str, offset: int, max_len: int = 16) -> list[LookupMatch]:
        """從文本指定字元位置向後做最長匹配掃描（類似 Yomitan 滑鼠懸停邏輯）。"""
        if not text or offset < 0 or offset >= len(text):
            return []

        matches: list[LookupMatch] = []
        seen_lemmas: set[str] = set()
        end_limit = min(len(text), offset + max_len)

        for length in range(end_limit - offset, 0, -1):
            sub = text[offset : offset + length]
            # 跳過開頭/結尾為標點符號的子串
            if re.search(r"[。、！？「」『』（）\(\)\s…・]", sub):
                continue
            found = self.lookup_exact_or_deinflect(sub)
            if found and found.lemma not in seen_lemmas:
                seen_lemmas.add(found.lemma)
                matches.append(found)
        return matches
