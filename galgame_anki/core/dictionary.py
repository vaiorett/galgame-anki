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
import uuid
import zipfile
from dataclasses import dataclass, field
from functools import lru_cache
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


@dataclass(frozen=True)
class SenseItem:
    """保留義項層級的語言、來源、詞性、語體與順序（原文與繁中顯示並存）。"""

    text: str
    raw_text: str
    lang: str               # "zh" | "ja" | "en"
    dict_name: str
    dict_kind: str          # "bilingual" | "monolingual"
    pos: str = ""
    register: str = ""      # e.g. "arch" | "obs" | "hist" | "col" | "sl" | "fam" | "suf" | "derog"
    order: int = 0
    score: int = 0


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

    def structured_senses(
        self,
        lang: str | None = None,
        max_senses: int = 6,
        convert_tw: bool = True,
    ) -> list[SenseItem]:
        """提取含語言、來源、詞性、語體標記的結構化義項清單。"""
        items = extract_structured_senses(self.all_entries, max_per_lang=max_senses, convert_tw=convert_tw)
        if lang is None:
            return items
        return [s for s in items if s.lang == lang][:max_senses]

    def senses_by_language(self, max_per_lang: int = 6, convert_tw: bool = True) -> dict[str, list[SenseItem]]:
        """依明確語言標記將義項分流為繁中（zh）、日日（ja）、日英（en）。"""
        grouped: dict[str, list[SenseItem]] = {"zh": [], "ja": [], "en": []}
        for item in extract_structured_senses(self.all_entries, max_per_lang=max_per_lang, convert_tw=convert_tw):
            if item.lang in grouped and len(grouped[item.lang]) < max_per_lang:
                grouped[item.lang].append(item)
        return grouped

    def best_sense_with_fallback(
        self,
        preference: str = "bilingual",
        strict: bool = False,
        convert_tw: bool = True,
    ) -> tuple[str, str, str]:
        """依偏好回傳 (主釋義, 實際語言, 備援標籤)；無可信中文時明確標記「日英備援」或「日日備援」。"""
        by_lang = self.senses_by_language(max_per_lang=4, convert_tw=convert_tw)
        if preference == "monolingual":
            if by_lang["ja"]:
                return by_lang["ja"][0].text, "ja", ""
            if not strict:
                if by_lang["zh"]:
                    return by_lang["zh"][0].text, "zh", "繁中備援"
                if by_lang["en"]:
                    return by_lang["en"][0].text, "en", "日英備援"
            return "", "", ""
        if by_lang["zh"]:
            return by_lang["zh"][0].text, "zh", ""
        if by_lang["en"]:
            return by_lang["en"][0].text, "en", "日英備援"
        if not strict and by_lang["ja"]:
            return by_lang["ja"][0].text, "ja", "日日備援"
        return "", "", ""

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
            # 僅當首義項極短（<= 3 字且不含多義分隔）且第二義項非地名/衍生長詞且完全不重疊時才補充 1 條近義
            if (
                len(first) <= 3
                and not re.search(r"[、，,；;。！？]", first)
                and len(senses) > 1
                and len(senses[1]) <= 10
                and not (len(first) >= 2 and senses[1].startswith(first))
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
    r"redirected from|alternative kanji|alternative spelling|alternative form of|obsolete spelling|"
    r"alt-of$|alternative$|transliteration\b|^⟶|「[^」]+」を参照)",
    re.IGNORECASE,
)

_SKIP_SENSE_LINE_RE = re.compile(
    r"^(?:例：|　例：|see:|see also|cf\.|compare|\d{4}[，,年]|Wiktionary|JMdict\b|Tatoeba|forms\b|Note$|"
    r"redirected from|.*redirected from|最早[见見][于於]|最早引文|可考[于於]|原[为為]\s*[\u3040-\u9fff]|"
    r".*alternative kanji|.*alternative spelling|.*alt-of$|.*alternative$|.*transliteration\b|"
    r"⟶.*|（[^）]+）「[^」]+」を参照|「[^」]+」を参照|同上[。.]?$|略$|解剖[学學]$|"
    r"平假名音[节節]|片假名音[节節]|音[节節][ぁ-んァ-ン]|"
    r"(?:男性|女性)?人名$|姓氏$|地名$|名字$|大阿[尔爾]克那|須弥山|ૐ|和文通話表|"
    r"〈[^〉]+〉|.*（[★🅁①-⑨]）$|as\s+[\u3040-\u9fff]|from\s+[\u3040-\u9fff]|abbr\.\s+of\s+|usu\.\s+as\s+|"
    r"(?:(?:inf|col|arch|obs|hist|sl|vulg|derog|pol|hum|hon|abbr|uk|fam|id|chn|exp|noun|verb|"
    r"adverb|adjective|pronoun|particle|interjection|kana|slang|colloquial|childish|"
    r"na-adj|no-adj|i-adj|5-dan|1-dan|ichidan|godan|suru|transitive|intransitive|go-on|kan-on)[\s,.]*)+$|"
    r"(?:非形式的|形式的|口語|文語|俗語|雅語|古語|廃語|方言|卑語|敬語|謙譲語|丁寧語|尊敬語|"
    r"自五|他五|自他五|自一|他一|自他一|自サ|他サ|自動|他動|自動詞|他動詞|自他動|"
    r"名詞|動詞|形容詞|形容動詞|副詞|感動詞|接続詞|連体詞|代名詞|接尾辞|接頭辞|"
    r"形|形動|名|副|感|連|接|代|接尾|接頭)$)",
    re.IGNORECASE,
)
_PURE_KANA_LINE_RE = re.compile(r"^[ぁ-んァ-ンー\s・]+$")

# Jitendex 舊版匯入時黏連在英文義項前的標籤（刻意排除單獨 exp 誤傷 expect/explain，exp 須後接其他標籤或 "to "）
_JITENDEX_TAG_TOKEN = (
    r"(?:expression|noun|verb|adverb|adjective|pronoun|particle|conjunction|interjection|"
    r"counter|auxiliary|prefix|suffix|abbr\.|colloquial|slang|childish|archaic|dated|obsolete|"
    r"vulgar|derogatory|honorific|humble|polite|familiar|formal|literary|poetic|rare|sensitive|"
    r"idiomatic|idiom|proverb|onomatopoeia|wasei|historical|jocular|euphemistic|"
    r"physics|biology|anatomy|chemistry|medicine|mathematics|law|finance|business|military|"
    r"linguistics|grammar|computing|internet|sports|baseball|music|food|mahjong|shogi|sumo|"
    r"Buddhism|Shinto|Christianity|no-adj|na-adj|i-adj|taru-adj|ku-adj|suru|transitive|"
    r"intransitive|kana|[15]-dan|ichidan|godan|kuru|irregular|n-pref|n-suf)"
)
_JITENDEX_GLUED_POS_RE = re.compile(
    rf"^(?:exp(?={_JITENDEX_TAG_TOKEN}|to\s)|{_JITENDEX_TAG_TOKEN})+",
)
_KATAKANA_LOANWORD_RE = re.compile(r"^[ァ-ヶー・ヴ]{2,}$")
_HIRAGANA_ONLY_RE = re.compile(r"^[ぁ-んー]+$")
_FOREIGN_LANG_NOTE_RE = re.compile(
    r"^(German|French|Dutch|Portuguese|Italian|Spanish|Russian|Latin|Greek):\s*(.+)$",
    re.IGNORECASE,
)
_LANG_ZH_MAP = {
    "german": "德",
    "french": "法",
    "dutch": "荷",
    "portuguese": "葡",
    "italian": "義",
    "spanish": "西",
    "russian": "俄",
    "latin": "拉",
    "greek": "希",
}
_REGISTER_TOKENS = ("arch", "obs", "hist", "derog", "vulg", "col", "sl", "fam", "hon", "hum", "pol", "suf", "chn")


def is_katakana_loanword(term: str) -> bool:
    """判斷詞頭是否為純片假名外來語（至少 2 字元，如 ロボット、システム、エネルギー）。"""
    return bool(_KATAKANA_LOANWORD_RE.match((term or "").strip()))


def _strip_jitendex_pos_prefix(text: str) -> str:
    """清理 Jitendex 結構化詞條黏連在英文義項前的詞性前綴（相容已匯入舊庫）。"""
    m = _JITENDEX_GLUED_POS_RE.match(text)
    if not m:
        return text
    rest = text[m.end():].strip()
    rest = re.sub(r"^\(spec\.\)\s*(?:intransitive|transitive)\s*", "", rest)
    if rest and re.match(r"^[A-Za-z(]", rest):
        return rest
    return text


def classify_entry_language(entry: DictEntry) -> str:
    """依來源詞典與實際文字明確區分義項語言：zh（繁中）/ ja（日日）/ en（日英），不單靠漢字誤判。"""
    if entry.dict_kind == "monolingual":
        return "ja"
    low = (entry.dict_name or "").lower()
    if any(k in low for k in ("ja-zh", "zh", "中", "雙語", "双语", "漢", "汉", "繁", "簡")):
        return "zh"
    if any(k in low for k in ("jmdict", "jitendex", "en-")) or re.search(
        r"(?:^|[^a-z])(?:en|eng|english)(?:$|[^a-z])", low
    ):
        return "en"
    sample = " ".join(entry.glosses[:2])
    return "zh" if _is_chinese_gloss(sample) else "en"


def _extract_register_tag(pos: str) -> str:
    low = f" {(pos or '').lower()} "
    found = [tok for tok in _REGISTER_TOKENS if re.search(rf"(?<![a-z]){tok}(?![a-z])", low)]
    return " ".join(found)


@lru_cache(maxsize=1)
def _get_opencc_s2twp() -> Any:
    try:
        import opencc  # type: ignore[import-not-found]

        return opencc.OpenCC("s2twp.json")
    except Exception:
        return None


_S2TW_PHRASES: tuple[tuple[str, str], ...] = (
    ("国家统治者", "國家統治者"),
    ("君主国", "君主國"),
    ("称谓后缀", "稱謂後綴"),
    ("后接于", "後接於"),
    ("总之", "總之"),
    ("暂且", "暫且"),
    ("机会", "機會"),
    ("或许", "或許"),
    ("确实", "確實"),
    ("认真", "認真"),
    ("实打实", "實打實"),
    ("办不到", "辦不到"),
    ("车站", "車站"),
    ("领主", "領主"),
    ("贵族", "貴族"),
    ("应招", "應招"),
    ("应征", "應徵"),
    ("意图", "意圖"),
    ("分别", "分別"),
    ("分离", "分離"),
    ("系统", "系統"),
    ("体系", "體系"),
    ("变得", "變得"),
    ("严重", "嚴重"),
    ("厉害", "厲害"),
    ("合并", "合併"),
    ("合计", "合計"),
    ("没有", "沒有"),
    ("无", "無"),
)


def normalize_zh_tw(text: str) -> str:
    """將已確認為中文的釋義轉為台灣正體顯示（優先使用 OpenCC s2twp，無套件時使用內建映射）。"""
    if not text or not _is_chinese_gloss(text):
        return text
    cc = _get_opencc_s2twp()
    if cc is not None:
        try:
            return str(cc.convert(text))
        except Exception:
            pass
    out = text
    for src, dst in _S2TW_PHRASES:
        if src in out:
            out = out.replace(src, dst)
    return out.translate(_S2T_NORM)


def extract_loanword_origins(
    entries: list[DictEntry],
    term: str,
    max_items: int = 3,
) -> list[str]:
    """針對片假名外來語，從雙語詞典（JMdict／Jitendex）精準提取對應英文／外文原詞。"""
    if not is_katakana_loanword(term) or not entries:
        return []
    origins: list[str] = []
    seen: set[str] = set()
    for entry in entries[:6]:
        if entry.dict_kind != "bilingual":
            continue
        entry_added = 0
        for g in entry.glosses[:4]:
            cleaned = _clean_gloss_noise(g)
            if not cleaned:
                continue
            for raw_ln in cleaned.splitlines():
                ln = raw_ln.strip()
                if not ln:
                    continue
                if _SKIP_SENSE_LINE_RE.match(ln) or _INLINE_FURIGANA_RE.search(ln):
                    continue
                if re.search(r"[\u3040-\u9fff]", ln):
                    continue
                ln = _strip_jitendex_pos_prefix(ln).strip(" ;,.")
                if not ln or _SKIP_SENSE_LINE_RE.match(ln):
                    continue
                fm = _FOREIGN_LANG_NOTE_RE.match(ln)
                if fm:
                    lang_zh = _LANG_ZH_MAP.get(fm.group(1).lower(), fm.group(1))
                    ln = f"{fm.group(2).strip()} ({lang_zh})"
                if ";" in ln or ln.startswith(('"', "'")) or len(ln.split()) > 3 or len(ln) > 28:
                    continue
                if not re.search(r"[A-Za-z]{2,}", ln):
                    continue
                key = ln.lower()
                if key in seen or any(key in s or s in key for s in seen if len(s) >= 4):
                    continue
                seen.add(key)
                origins.append(ln)
                entry_added += 1
                if len(origins) >= max_items:
                    return origins
                if entry_added >= 2:
                    break
            if entry_added >= 2:
                break
    return origins


_CITATION_LINE_RE = re.compile(
    r"(?:→ISBN|ISBN\b|虛構作品|虚构作品|頁：|ページ：|^\d{4}年\s*\d+月|收錄於|收录于|"
    r"^[（\(][^）\)]{1,32}『[^』]+』[^）\)]*[）\)]$)"
)
_INLINE_CIRCLED_SPLIT_RE = re.compile(r"\s*(?=[①-⑨])")
_ASCII_DOMAIN_TAG_RE = re.compile(r"^[A-Za-z][A-Za-z\s/-]{0,20}$")

_INLINE_FURIGANA_RE = re.compile(r"[\u4e00-\u9fff々]\([ぁ-んァ-ヶー]{1,6}\)")
_LEADING_POS_NOISE_RE = re.compile(
    r"^(?:(?:[1-9][\.\、\s]+|[①-⑨]\s*)|(?:(?:名|動|自|他|形|形動|副|サ|五|一|感|連|接)[・?？/\s]+)+)+"
)
_S2T_SRC = (
    "学语词义觉听说见现关开门间时书车东飞机气电话读写买卖进过还这个们实际边点头脸声响乐欢爱恋梦变动运经验结构组织认识记忆决定选择应该帮助给让离别对错难简单复杂强弱轻长远广满静温热凉干湿脏净坏伤惊担忧烦恼愿望兴够准备计划练习试战胜负护救杀醒飞游击碎断烧扫画唱弹照录播传送借换赔赚费贵贱穷约规则律权责任务业职馆院厂场港桥路镇城国陆岛湾雾雷风云阳阴树叶种麦鸟鸡鸭鹅猪猫马龙龟虫兽严厉征应体烫发内众"
    "统称谓缀暂认确办站领贵习图无总虽举几类叙毕"
)
_S2T_DST = (
    "學語詞義覺聽說見現關開門間時書車東飛機氣電話讀寫買賣進過還這個們實際邊點頭臉聲響樂歡愛戀夢變動運經驗結構組織認識記憶決定選擇應該幫助給讓離別對錯難簡單複雜強弱輕長遠廣滿靜溫熱涼乾濕髒淨壞傷驚擔憂煩惱願望興夠準備計劃練習試戰勝負護救殺醒飛游擊碎斷燒掃畫唱彈照錄播傳送借換賠賺費貴賤窮約規則律權責任務業職館院廠場港橋路鎮城國陸島灣霧雷風雲陽陰樹葉種麥鳥雞鴨鵝豬貓馬龍龜蟲獸嚴厲徵應體燙發內眾"
    "統稱謂綴暫認確辦站領貴習圖無總雖舉幾類敘畢"
)
_S2T_NORM = str.maketrans(_S2T_SRC, _S2T_DST)
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
    r"(?:词源|詞源|語源|语源|派生自|源自|原意|上古|中古|中世|室町|平安|江戸|江户|古旧|同源|"
    r"原始日語|原始日语|縮略|缩略|變換自|变换自|截斷形式|截断形式|和製英語|和制英语|"
    r"詞彙.*由以下|词汇.*由以下|字面意思|熟字訓|熟字训|沒有末尾|没有末尾|首次出現|首次出现|"
    r"最早出現|最早出现|最早引文|變自|变自|轉變自|转变自|可能是|讀法|读法|根據.*所說|根据.*所说|"
    r"フリー百科事典|ウィキペディア|\[Alt:|日本祖語|古典日本語|連濁|连浊|の転|の略|の縮約)"
)
_ETYMOLOGY_RUBY_COMP_RE = re.compile(r"^[\u4e00-\u9fffぁ-んァ-ヶ々]+(?:\([ぁ-んァ-ヶa-zA-Z]+\))?\s*\([a-zA-Zāīūēō]+[,，]")
_NUMBERED_ZH_SENSE_RE = re.compile(r"^\d{1,2}\s*[.．、]\s*([（(\u3400-\u9fff].*)$")
_ETYMOLOGY_BRIDGE_LINES = {"發展", "发展", "演變", "演变"}
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
        while idx < len(lines) and (
            _ETYMOLOGY_CONT_RE.search(lines[idx])
            or _ETYMOLOGY_RUBY_COMP_RE.match(lines[idx])
            or lines[idx] == "]"
            or (lines[idx].rstrip("：:") in _ETYMOLOGY_BRIDGE_LINES and idx + 1 < len(lines)
                and _ETYMOLOGY_CONT_RE.search(lines[idx + 1]))
        ):
            idx += 1
        if idx < len(lines):
            lines = lines[idx:]
        else:
            lines = []

    formatted: list[str] = []
    for ln in lines:
        # 保留例句數標記供 extract_concise_senses 精準略過後續 N 組例句＋譯文
        pretty = _WTY_EX_COUNT_RE.sub(r"\n　例[\1]：", ln)
        formatted.append(pretty)
    return "\n".join(formatted).strip()


_EX_COUNT_MARKER_RE = re.compile(r"^[　\s]*例\[(\d+)\]：")


def _iter_entry_clean_senses(entry: DictEntry, is_mono: bool) -> list[str]:
    """從單一 DictEntry 抽取出乾淨獨立義項文字（去例句、詞源、轉址、詞性黏連雜訊）。"""
    out: list[str] = []
    entry_lang = classify_entry_language(entry)
    head_term = (entry.term or "").strip()

    for g in entry.glosses[:5]:
        cleaned = _clean_gloss_noise(g)
        if not cleaned:
            continue
        lines = cleaned.splitlines()
        if entry_lang == "zh" and "wty" in entry.dict_name.lower():
            numbered = [m[1] for line in lines if (m := _NUMBERED_ZH_SENSE_RE.match(line))]
            if numbered:
                lines = numbered
        skip_remaining = 0
        for raw_ln in lines:
            ln = raw_ln.strip()
            if not ln:
                continue
            if _CITATION_LINE_RE.search(ln):
                continue
            em = _EX_COUNT_MARKER_RE.match(ln)
            if em:
                n_ex = int(em.group(1))
                skip_remaining = max(0, min(8, n_ex - 1)) if is_mono else max(1, min(12, n_ex * 2 - 1))
                continue
            if ln.startswith("例：") or ln.startswith("　例："):
                skip_remaining = max(skip_remaining, 0 if is_mono else 1)
                continue
            if skip_remaining > 0:
                skip_remaining -= 1
                continue
            if _SKIP_SENSE_LINE_RE.match(ln):
                continue
            if not is_mono and _PURE_KANA_LINE_RE.match(ln):
                continue
            if is_mono and _ASCII_DOMAIN_TAG_RE.match(ln):
                continue
            if _INFLECTION_STUB_RE.search(ln):
                continue
            if not is_mono and _INLINE_FURIGANA_RE.search(ln):
                skip_remaining = max(skip_remaining, 1)
                continue
            if not is_mono:
                if len(ln) >= 12 and len(re.findall(r"[！？!?]+", ln)) >= 2:
                    continue
                kana_cnt = len(re.findall(r"[ぁ-んァ-ン]", ln))
                if kana_cnt >= 3 and ln.endswith(("。", "！", "？", "‼", "!", "?")):
                    skip_remaining = max(skip_remaining, 1)
                    continue
                if kana_cnt >= 1 and len(ln) <= 6 and not re.search(r"[，,、。；;]", ln):
                    continue

            if re.match(r"^[\u3040-\u9fff々ー]+【[ぁ-んァ-ンー]+】$", ln):
                continue

            sub_parts = (
                [p.strip() for p in _INLINE_CIRCLED_SPLIT_RE.split(ln) if p.strip()]
                if ("①" in ln and "②" in ln)
                else [ln]
            )
            for part in sub_parts:
                cur = _strip_jitendex_pos_prefix(part).strip()
                if not is_mono:
                    cur = _LEADING_POS_NOISE_RE.sub("", cur).strip()
                else:
                    cur = re.sub(r"^[①-⑨]\s*", "", cur).strip()
                cur = cur.strip("。；;")
                if not cur or _SKIP_SENSE_LINE_RE.match(cur):
                    continue
                # 中日同形詞（如 君主 -> 君主）在中文雙語詞典中是有效核心釋義，不得誤刪導致次義「君主国」搶占首義；
                # 僅於單語詞典或非中文條目中過濾與詞頭完全相同的回顯。
                if cur == head_term and (is_mono or entry_lang != "zh" or not _is_chinese_gloss(cur) or len(cur) < 2):
                    continue
                compact = cur[:72] + ("…" if len(cur) > 72 else "")
                out.append(compact)
    return out


def extract_structured_senses(
    entries: list[DictEntry],
    max_per_lang: int = 6,
    *,
    convert_tw: bool = True,
) -> list[SenseItem]:
    """保留義項層級的語言、來源、詞性、語體與順序，按語言分別去重（繁中／日日／日英互不汙染）。"""
    results: list[SenseItem] = []
    seen_norm_by_lang: dict[str, set[str]] = {"zh": set(), "ja": set(), "en": set()}
    seen_atoms_by_lang: dict[str, set[str]] = {"zh": set(), "ja": set(), "en": set()}
    texts_by_lang: dict[str, list[str]] = {"zh": [], "ja": [], "en": []}

    order_counter = 0
    for entry in entries[:14]:
        is_mono = entry.dict_kind == "monolingual"
        entry_lang = classify_entry_language(entry)
        reg = _extract_register_tag(entry.pos)
        for raw_sense in _iter_entry_clean_senses(entry, is_mono=is_mono):
            lang = entry_lang
            if lang == "zh" and not _is_chinese_gloss(raw_sense):
                # 雙語中文詞典偶含純英文備註，不誤歸為中文
                if re.search(r"[A-Za-z]{2,}", raw_sense) and not _HAS_JP_KANA_RE.search(raw_sense):
                    lang = "en"
                else:
                    continue
            if len(texts_by_lang[lang]) >= max_per_lang:
                continue
            norm = re.sub(r"[\s，,、。；;・]+", "", raw_sense.translate(_S2T_NORM).lower())
            if not norm or norm in seen_norm_by_lang[lang]:
                continue
            atoms = _sense_atoms(raw_sense)
            if atoms:
                if atoms <= seen_atoms_by_lang[lang]:
                    continue
                if any(_senses_semantically_overlap(raw_sense, prev) for prev in texts_by_lang[lang]):
                    continue
            seen_norm_by_lang[lang].add(norm)
            seen_atoms_by_lang[lang].update(atoms)
            texts_by_lang[lang].append(raw_sense)
            display_txt = normalize_zh_tw(raw_sense) if (lang == "zh" and convert_tw) else raw_sense
            results.append(
                SenseItem(
                    text=display_txt,
                    raw_text=raw_sense,
                    lang=lang,
                    dict_name=entry.dict_name,
                    dict_kind=entry.dict_kind,
                    pos=entry.pos,
                    register=reg,
                    order=order_counter,
                    score=entry.score,
                )
            )
            order_counter += 1
    return results


def extract_concise_senses(entries: list[DictEntry], max_senses: int = 6, *, prefer_chinese: bool = True) -> list[str]:
    """從多本詞典中提煉出乾淨、不重複、去例句雜訊且聚焦重點的獨立核心義項。"""
    senses: list[str] = []
    seen_norm: set[str] = set()
    seen_atoms: set[str] = set()
    is_mono_only = bool(entries) and all(e.dict_kind == "monolingual" for e in entries)

    for entry in entries[:8]:
        for compact in _iter_entry_clean_senses(entry, is_mono=is_mono_only):
            norm = re.sub(r"[\s，,、。；;・]+", "", compact.translate(_S2T_NORM).lower())
            if not norm or norm in seen_norm:
                continue

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

    # 雙語模式：只要已有真正中文核心釋義，就直接隱藏多餘英文詞典重複義項，讓 Anki 卡片與膠囊聚焦中文重點！
    if prefer_chinese and not is_mono_only:
        zh_senses = [s for s in senses if _is_chinese_gloss(s)]
        if zh_senses:
            return zh_senses[:max_senses]
    return senses[:max_senses]


def _is_inflection_stub_entries(entries: list[DictEntry]) -> bool:
    """判斷查到的詞條是否全都是屈折存根、重定向或方言干擾（保留真實現代口語 col / 縮略 abbr 詞條如 ガチ、とりま）。"""
    if not entries:
        return False
    if all(
        any(tag in e.pos.lower() for tag in ("dialect", "non-lemma", "char", "forms"))
        for e in entries[:2]
    ):
        return True
    for entry in entries:
        is_mono = entry.dict_kind == "monolingual"
        if _iter_entry_clean_senses(entry, is_mono=is_mono):
            return False
    return True


_JITENDEX_BLOCK_DATA_TYPES = frozenset(
    {
        "part-of-speech-info",
        "misc-info",
        "field-info",
        "dialect-info",
        "info-gloss",
        "glossary",
        "sense-group",
        "example-sentence",
        "example-sentence-a",
        "example-sentence-b",
        "xref",
        "antonym",
        "notes",
        "forms",
        "attribution",
    }
)


def _extract_yomitan_text(node: Any) -> str:
    """遞迴解析 Yomitan structured-content（尊重 Jitendex 節點邊界避免標籤黏連）與純字串釋義。"""
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
        data = node.get("data")
        data_content = str(data.get("content", "")) if isinstance(data, dict) else ""
        content = _extract_yomitan_text(node.get("content"))
        if tag == "rt":
            return f"({content})" if content else ""
        if tag in ("li", "div", "p", "br", "tr", "dt", "dd") or data_content in _JITENDEX_BLOCK_DATA_TYPES:
            return f"{content}\n" if content else "\n"
        return content
    return str(node)


_DICT_EN_WORD_RE = re.compile(r"(?:^|[^a-z])(?:en|eng|english|ja-en|jp-en)(?:$|[^a-z])")
_MAX_ZIP_JSON_BYTES = 100 * 1024 * 1024  # 單一 Yomitan bank JSON 解壓上限 100 MB（防 zip bomb）


def _read_zip_json_safe(zf: zipfile.ZipFile, name: str, max_bytes: int = _MAX_ZIP_JSON_BYTES) -> Any:
    info = zf.getinfo(name)
    if info.file_size > max_bytes:
        raise ValueError(f"詞典檔案 {name} 解壓後大小超過安全上限（{max_bytes // (1024 * 1024)} MB）。")
    chunks: list[bytes] = []
    total = 0
    with zf.open(info, "r") as fh:
        while True:
            buf = fh.read(1024 * 1024)
            if not buf:
                break
            total += len(buf)
            if total > max_bytes:
                raise ValueError(f"詞典檔案 {name} 實際解壓大小超過安全上限（{max_bytes // (1024 * 1024)} MB）。")
            chunks.append(buf)
    return json.loads(b"".join(chunks).decode("utf-8"))


def _guess_dict_kind(title: str, sample_gloss: str = "") -> str:
    """自動推斷匯入詞典是雙語還是日日單語。"""
    lower = title.lower()
    if any(
        k in lower
        for k in (
            "ja-ja", "wty-ja-ja", "大辞林", "大辞泉", "明鏡", "新明解", "広辞苑",
            "三省堂", "国語", "旺文社", "岩波", "monolingual",
            "daijisen", "daijirin", "kojien", "koujien", "meikyo", "shinmeikai",
            "sanseido", "kokugo", "obunsha", "iwanami", "pixiv",
        )
    ):
        return "monolingual"
    if any(k in lower for k in ("ja-zh", "zh", "中", "jmdict", "jitendex", "雙語", "双语", "漢", "汉")) or _DICT_EN_WORD_RE.search(lower):
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
            PRAGMA busy_timeout=5000;

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
            CREATE INDEX IF NOT EXISTS idx_terms_dict ON terms(dict_id);
            CREATE INDEX IF NOT EXISTS idx_meta_term ON term_meta(term);
            CREATE INDEX IF NOT EXISTS idx_meta_dict ON term_meta(dict_id);
            CREATE INDEX IF NOT EXISTS idx_meta_mode_term ON term_meta(mode, term);
            """
        )
        # 安全遷移既有資料庫：補上可選欄位（舊庫完全相容，不遺失任何已安裝詞典）
        cols = {row["name"] for row in cur.execute("PRAGMA table_info(dictionaries)").fetchall()}
        if "enabled" not in cols:
            cur.execute("ALTER TABLE dictionaries ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1")
            cols.add("enabled")
        if "source_id" not in cols:
            cur.execute("ALTER TABLE dictionaries ADD COLUMN source_id TEXT NOT NULL DEFAULT ''")
            cols.add("source_id")
        if "license" not in cols:
            cur.execute("ALTER TABLE dictionaries ADD COLUMN license TEXT NOT NULL DEFAULT ''")
            cols.add("license")
        self._has_source_col = "source_id" in cols
        self._has_lic_col = "license" in cols
        self._conn.commit()

    def list_dictionaries(self) -> list[dict[str, Any]]:
        extra_sel = ""
        if getattr(self, "_has_source_col", True):
            extra_sel += ", source_id"
        if getattr(self, "_has_lic_col", True):
            extra_sel += ", license"
        with self._lock:
            rows = self._conn.execute(
                f"SELECT id, name, kind, revision, entry_count, priority, enabled{extra_sel} "
                "FROM dictionaries ORDER BY priority ASC, id ASC"
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["enabled"] = bool(d.get("enabled", 1))
            d.setdefault("source_id", "")
            d.setdefault("license", "")
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

    def get_en_fallback_source(self) -> str:
        """回傳目前啟用的日英備援模式：jmdict / jitendex / both / none。"""
        dicts = self.list_dictionaries()
        has_jm = any("jmdict" in d["name"].lower() and d["enabled"] for d in dicts)
        has_jt = any("jitendex" in d["name"].lower() and d["enabled"] for d in dicts)
        if has_jm and has_jt:
            return "both"
        if has_jm:
            return "jmdict"
        if has_jt:
            return "jitendex"
        return "none"

    def set_en_fallback_source(self, mode: str) -> None:
        """獨立設定日英備援來源（jmdict / jitendex / both / none），不影響中文、日日與音調詞頻庫。"""
        mode_low = (mode or "jmdict").lower()
        with self._lock:
            for d in self.list_dictionaries():
                low = d["name"].lower()
                is_jm = "jmdict" in low
                is_jt = "jitendex" in low
                if not (is_jm or is_jt):
                    continue
                if mode_low == "jmdict":
                    en = is_jm
                    prio = 10 if is_jm else 25
                elif mode_low == "jitendex":
                    en = is_jt
                    prio = 10 if is_jt else 25
                elif mode_low == "both":
                    en = True
                    prio = 10 if is_jm else 25
                else:
                    en = False
                    prio = d["priority"]
                self._conn.execute(
                    "UPDATE dictionaries SET enabled = ?, priority = ? WHERE id = ?",
                    (1 if en else 0, prio, d["id"]),
                )
            self._conn.commit()

    def apply_dictionary_preset(self, preset: str) -> None:
        """將「可被查詢」與「優先顯示順序」分離：預設保留日日與單一日英備援供語塊查詞，不再一刀切斷慣用語覆蓋。"""
        with self._lock:
            dicts = self.list_dictionaries()
            has_jmdict = any("jmdict" in d["name"].lower() for d in dicts)
            for d in dicts:
                name_low = d["name"].lower()
                kind = d["kind"]
                is_jm = "jmdict" in name_low
                is_jt = "jitendex" in name_low
                is_builtin_spoken = "口語核心詞庫" in d["name"]
                is_starter = "內建精選" in d["name"]
                is_zh_dict = (kind == "bilingual") and not (is_jm or is_jt)

                if preset == "all":
                    enabled = True
                    prio = d["priority"]
                elif preset == "zh_focus":
                    # 繁中優先：保留中文主力 + 日日 + 單一日英備援（優先較乾淨的 JMdict）+ 音調詞頻供語塊與查詞使用
                    enabled = not (is_jt and has_jmdict)
                    if is_builtin_spoken:
                        prio = 1 if kind == "bilingual" else 2
                    elif is_zh_dict and not is_starter:
                        prio = 5
                    elif is_jm:
                        prio = 10
                    elif kind == "monolingual" and not is_starter:
                        prio = 15
                    elif is_jt:
                        prio = 25
                    elif kind == "meta":
                        prio = d["priority"] if d["priority"] >= 40 else 40
                    else:
                        prio = d["priority"]
                elif preset == "mono_focus":
                    # Lv.3 日日優先：保留日日主力 + 繁中安全網 + 單一日英備援 + 音調詞頻
                    enabled = not (is_jt and has_jmdict)
                    if is_builtin_spoken:
                        prio = 1 if kind == "monolingual" else 2
                    elif kind == "monolingual" and not is_starter:
                        prio = 5
                    elif is_zh_dict and not is_starter:
                        prio = 10
                    elif is_jm:
                        prio = 15
                    elif is_jt:
                        prio = 25
                    elif kind == "meta":
                        prio = d["priority"] if d["priority"] >= 40 else 40
                    else:
                        prio = d["priority"]
                else:
                    enabled = True
                    prio = d["priority"]

                self._conn.execute(
                    "UPDATE dictionaries SET enabled = ?, priority = ? WHERE id = ?",
                    (1 if enabled else 0, prio, d["id"]),
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
        source_id: str | None = None,
        license_text: str | None = None,
    ) -> dict[str, Any]:
        """原子化匯入 Yomitan .zip 詞典：鎖外解壓與解析 JSON（防鎖死查詞執行緒），鎖內批量寫入並原子切換。"""
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            if "index.json" not in names:
                raise ValueError("此壓縮檔不含 index.json，非標準 Yomitan 詞典格式。")

            index_data = _read_zip_json_safe(zf, "index.json", max_bytes=4 * 1024 * 1024)
            if not isinstance(index_data, dict):
                raise ValueError("index.json 格式錯誤。")
            title = str(index_data.get("title") or zip_path.stem)
            revision = str(index_data.get("revision") or "")
            final_license = str(
                license_text
                or index_data.get("attribution")
                or index_data.get("license")
                or ""
            )[:400]
            stable_source = (source_id or "").strip()

            term_banks = sorted(n for n in names if re.match(r"^(?:.*/)?term_bank_\d+\.json$", n))
            meta_banks = sorted(n for n in names if re.match(r"^(?:.*/)?term_meta_bank_\d+\.json$", n))
            if not term_banks and not meta_banks:
                raise ValueError("此詞典壓縮檔不含 term_bank 或 term_meta_bank 資料。")

            total_files = max(1, len(term_banks) + len(meta_banks))
            processed = 0
            sample_gloss = ""
            parsed_terms: list[tuple[str, str, str, int, str]] = []
            parsed_meta: list[tuple[str, str, str, str]] = []

            # 鎖外完成全部 ZIP 解壓與 JSON 解析，避免匯入大詞典時卡住懸停查詞
            for bank_idx, bank_name in enumerate(term_banks):
                entries = _read_zip_json_safe(zf, bank_name)
                if not isinstance(entries, list):
                    raise ValueError(f"{bank_name} 格式錯誤（非陣列）。")
                if bank_idx == 0:
                    for row in entries[:10]:
                        if isinstance(row, list) and len(row) >= 6:
                            txt = _extract_yomitan_text(row[5]).strip()
                            if txt:
                                sample_gloss += txt + " "
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
                    if reading == term and gloss_list:
                        rm = re.search(r"【([ぁ-んァ-ンー]+)】", gloss_list[0].splitlines()[0])
                        if rm:
                            reading = rm.group(1)
                    parsed_terms.append(
                        (term, reading, pos, score, json.dumps(gloss_list, ensure_ascii=False))
                    )

                processed += 1
                if progress:
                    progress(f"解析詞條 {title} ({processed}/{total_files})", int(processed * 75 / total_files))

            for bank_name in meta_banks:
                entries = _read_zip_json_safe(zf, bank_name)
                if not isinstance(entries, list):
                    raise ValueError(f"{bank_name} 格式錯誤（非陣列）。")
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
                            parsed_meta.append(
                                (term, reading, "pitch", json.dumps({"pitches": positions}))
                            )
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
                            parsed_meta.append(
                                (term, reading, "freq", json.dumps({"frequency": freq_val}))
                            )

                processed += 1
                if progress:
                    progress(f"解析音調/詞頻 {title} ({processed}/{total_files})", int(processed * 75 / total_files))

        if kind_override:
            kind = kind_override
        elif not term_banks and meta_banks:
            kind = "meta"
        else:
            kind = _guess_dict_kind(title, sample_gloss)

        final_count = len(parsed_terms) if parsed_terms else len(parsed_meta)
        if final_count == 0:
            raise ValueError("此詞典壓縮檔解析後沒有任何可用詞條，已取消切換並保留原版本。")

        with self._lock:
            has_source_col = getattr(self, "_has_source_col", True)
            has_lic_col = getattr(self, "_has_lic_col", True)

            existing = None
            if stable_source and has_source_col:
                existing = self._conn.execute(
                    "SELECT id, enabled, priority FROM dictionaries WHERE source_id = ?",
                    (stable_source,),
                ).fetchone()
            if existing is None:
                existing = self._conn.execute(
                    "SELECT id, enabled, priority FROM dictionaries WHERE name = ?",
                    (title,),
                ).fetchone()

            if existing is not None:
                old_id = int(existing["id"])
                keep_enabled = int(existing["enabled"])
                priority = int(existing["priority"])
            else:
                old_id = None
                keep_enabled = 1
                if priority_override is not None:
                    priority = priority_override
                elif kind == "bilingual":
                    priority = 5 if any(k in title.lower() for k in ("zh", "中", "漢", "汉")) else 20
                elif kind == "monolingual":
                    priority = 15
                else:
                    priority = 40

            cur = self._conn.cursor()
            staging_name = f"{title}::__staging_{uuid.uuid4().hex[:8]}"
            cur.execute("SAVEPOINT galanki_dict_import")
            try:
                if has_source_col and has_lic_col:
                    cur.execute(
                        "INSERT INTO dictionaries (name, kind, revision, entry_count, priority, enabled, source_id, license) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (staging_name, kind, revision, 0, priority, keep_enabled, stable_source, final_license),
                    )
                else:
                    cur.execute(
                        "INSERT INTO dictionaries (name, kind, revision, entry_count, priority, enabled) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (staging_name, kind, revision, 0, priority, keep_enabled),
                    )
                dict_id = cur.lastrowid

                for idx in range(0, len(parsed_terms), 5000):
                    chunk = [(dict_id, *row) for row in parsed_terms[idx : idx + 5000]]
                    cur.executemany(
                        "INSERT INTO terms (dict_id, term, reading, pos, score, glosses_json) VALUES (?, ?, ?, ?, ?, ?)",
                        chunk,
                    )

                for idx in range(0, len(parsed_meta), 5000):
                    chunk = [(dict_id, *row) for row in parsed_meta[idx : idx + 5000]]
                    cur.executemany(
                        "INSERT INTO term_meta (dict_id, term, reading, mode, data_json) VALUES (?, ?, ?, ?, ?)",
                        chunk,
                    )

                if progress:
                    progress(f"寫入資料庫 {title}...", 92)

                # 新版本匯入驗證成功後，才移除舊版本並原子切換為正式名稱
                if old_id is not None:
                    cur.execute("DELETE FROM terms WHERE dict_id = ?", (old_id,))
                    cur.execute("DELETE FROM term_meta WHERE dict_id = ?", (old_id,))
                    cur.execute("DELETE FROM dictionaries WHERE id = ?", (old_id,))
                dup = cur.execute(
                    "SELECT id FROM dictionaries WHERE name = ? AND id != ?",
                    (title, dict_id),
                ).fetchone()
                if dup is not None:
                    cur.execute("DELETE FROM terms WHERE dict_id = ?", (dup["id"],))
                    cur.execute("DELETE FROM term_meta WHERE dict_id = ?", (dup["id"],))
                    cur.execute("DELETE FROM dictionaries WHERE id = ?", (dup["id"],))

                cur.execute(
                    "UPDATE dictionaries SET name = ?, entry_count = ? WHERE id = ?",
                    (title, final_count, dict_id),
                )
                cur.execute("RELEASE SAVEPOINT galanki_dict_import")
                self._conn.commit()
            except Exception:
                try:
                    cur.execute("ROLLBACK TO SAVEPOINT galanki_dict_import")
                    cur.execute("RELEASE SAVEPOINT galanki_dict_import")
                    self._conn.commit()
                except sqlite3.Error:
                    try:
                        self._conn.rollback()
                    except sqlite3.Error:
                        pass
                raise

            if progress:
                progress(f"完成匯入 {title}", 100)

            return {
                "id": dict_id,
                "name": title,
                "kind": kind,
                "revision": revision,
                "source_id": stable_source,
                "entry_count": final_count,
            }

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
                    source_id=spec["id"],
                    license_text=spec.get("license"),
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
        _base_sql = """
                SELECT t.term, t.reading, t.pos, t.score, t.glosses_json,
                       d.name AS dict_name, d.kind AS dict_kind, d.priority
                FROM terms t
                JOIN dictionaries d ON d.id = t.dict_id
                WHERE d.enabled = 1 AND {kind_cond} AND (t.term = ? OR t.reading = ?)
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
                LIMIT {limit}
        """
        with self._lock:
            bi_rows = self._conn.execute(
                _base_sql.format(kind_cond="d.kind != 'monolingual'", limit=32),
                (word, word, word),
            ).fetchall()
            mono_rows = self._conn.execute(
                _base_sql.format(kind_cond="d.kind = 'monolingual'", limit=18),
                (word, word, word),
            ).fetchall()
        # 依原本詞典優先序合併雙語與單語結果，確保單語條目永不被大量雙語條目擠出 LIMIT
        rows = sorted(
            list(bi_rows) + list(mono_rows),
            key=lambda r: (0 if r["term"] == word else 1, r["priority"], -r["score"]),
        )

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

    def get_top_freq_terms(self, max_freq: int = 35000, limit: int = 35000) -> list[tuple[str, int]]:
        """執行緒安全地回傳高頻詞清單 (term, freq)；若無詞頻表則回退至雙語詞典詞頭。"""
        try:
            with self._lock:
                rows = self._conn.execute(
                    """
                    SELECT m.term, MIN(CAST(json_extract(m.data_json, '$.frequency') AS INT)) AS freq
                    FROM term_meta m
                    JOIN dictionaries d ON d.id = m.dict_id
                    WHERE d.enabled = 1 AND m.mode = 'freq'
                    GROUP BY m.term
                    HAVING freq IS NOT NULL AND freq <= ?
                    ORDER BY freq ASC
                    LIMIT ?
                    """,
                    (max_freq, limit),
                ).fetchall()
            if rows:
                return [(str(r["term"] or "").strip(), int(r["freq"])) for r in rows if r["term"]]
            with self._lock:
                t_rows = self._conn.execute(
                    """
                    SELECT DISTINCT t.term FROM terms t
                    JOIN dictionaries d ON d.id = t.dict_id
                    WHERE d.enabled = 1 AND d.kind = 'bilingual'
                    LIMIT 12000
                    """
                ).fetchall()
            return [(str(r["term"] or "").strip(), idx) for idx, r in enumerate(t_rows, start=1) if r["term"]]
        except Exception:
            return []

    def query_homophones(self, reading: str, exclude_term: str = "", limit: int = 28) -> list[str]:
        """執行緒安全地查詢同讀音、不同寫法的候選詞頭（供聯想記憶樹使用）。"""
        if not reading:
            return []
        try:
            with self._lock:
                rows = self._conn.execute(
                    """
                    SELECT DISTINCT t.term FROM terms t
                    JOIN dictionaries d ON d.id = t.dict_id
                    WHERE d.enabled = 1 AND d.kind = 'bilingual'
                      AND t.reading = ? AND t.term != ?
                    LIMIT ?
                    """,
                    (reading, exclude_term, limit),
                ).fetchall()
            return [str(r["term"]) for r in rows if r["term"]]
        except Exception:
            return []

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

        exact_pitches: list[str] = []
        fallback_pitches: list[str] = []
        exact_freq: int | None = None
        fallback_freq: int | None = None
        has_other_reading_freq = False
        has_other_reading_pitch = False

        for r in rows:
            m_rd = str(r["reading"] or "")
            raw_data = r["data_json"]
            if not raw_data:
                continue
            data = json.loads(raw_data)
            if r["mode"] == "pitch":
                if reading and m_rd:
                    if m_rd != reading:
                        has_other_reading_pitch = True
                        continue
                    for p in data.get("pitches", []):
                        if p not in exact_pitches:
                            exact_pitches.append(p)
                else:
                    for p in data.get("pitches", []):
                        if p not in fallback_pitches:
                            fallback_pitches.append(p)
            elif r["mode"] == "freq":
                val = data.get("frequency")
                if not isinstance(val, int):
                    continue
                if reading and m_rd:
                    if m_rd != reading:
                        has_other_reading_freq = True
                        continue
                    if exact_freq is None or val < exact_freq:
                        exact_freq = val
                else:
                    if fallback_freq is None or val < fallback_freq:
                        fallback_freq = val

        pitches = exact_pitches if exact_pitches else ([] if has_other_reading_pitch else fallback_pitches)
        best_freq = exact_freq if exact_freq is not None else (None if has_other_reading_freq else fallback_freq)
        return pitches, best_freq

    def batch_has_terms(self, candidates: list[str] | set[str]) -> set[str]:
        """單次 SQLite 查詢確認一批候選語塊是否存在於啟用詞典中（供語塊匹配高速預篩）。"""
        unique = [c for c in set(candidates) if c]
        if not unique:
            return set()
        found: set[str] = set()
        chunk_size = 200
        for i in range(0, len(unique), chunk_size):
            chunk = unique[i : i + chunk_size]
            ph = ",".join("?" for _ in chunk)
            with self._lock:
                rows = self._conn.execute(
                    f"""
                    SELECT DISTINCT t.term AS hit
                    FROM terms t
                    JOIN dictionaries d ON d.id = t.dict_id
                    WHERE d.enabled = 1 AND t.term IN ({ph})
                    UNION
                    SELECT DISTINCT t.reading AS hit
                    FROM terms t
                    JOIN dictionaries d ON d.id = t.dict_id
                    WHERE d.enabled = 1 AND t.reading IN ({ph})
                    """,
                    chunk + chunk,
                ).fetchall()
            for r in rows:
                if r["hit"]:
                    found.add(str(r["hit"]))
        return found

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
        if pos_hint == "代名詞":
            return any(k in ep for k in ("pron", "pn", "代"))
        if pos_hint == "接尾":
            return any(k in ep for k in ("suf", "接尾"))
        if pos_hint == "動詞":
            return any(k in ep for k in ("v1", "v5", "vs", "vk", "verb", "動詞", "一段", "五段", "サ変", "カ変")) or ep == "v"
        if pos_hint == "形容詞":
            return any(k in ep for k in ("adj-i", "形容詞", "i-adj"))
        if pos_hint == "名詞":
            return any(k in ep for k in ("n", "名", "pron", "pn"))
        if pos_hint == "副詞":
            return any(k in ep for k in ("adv", "副"))
        if pos_hint == "感動詞":
            return any(k in ep for k in ("intj", "感", "exp"))
        if pos_hint == "連体詞":
            return any(k in ep for k in ("adj-pn", "adj_noun", "連体"))
        return False

    def _entry_pos_family(self, entry_pos: str) -> str:
        ep = (entry_pos or "").lower()
        if re.search(r"(?:^|[\s,])(?:pron|pn)(?:$|[\s,])", ep) or "代" in ep:
            return "pron"
        if re.search(r"(?:^|[\s,])(?:suf|n-suf)(?:$|[\s,])", ep) or "接尾" in ep:
            return "suf"
        if re.search(r"(?:^|[\s,])(?:adv)(?:$|[\s,])", ep) or "副" in ep:
            return "adv"
        if any(k in ep for k in ("v1", "v5", "vs", "vk", "verb", "動")):
            return "verb"
        if any(k in ep for k in ("adj", "形")):
            return "adj"
        if re.search(r"(?:^|[\s,])n(?:$|[\s,])", ep) or "名" in ep:
            return "noun"
        return ""

    def _order_group_entries(self, grp_entries: list[DictEntry], pos_hint: str = "") -> list[DictEntry]:
        """在同一 (原形, 讀音) 內保留現代常用詞性與非古語義項在前，參考 JMdict 義項主序調校 Wiktionary 同分條目。"""
        if len(grp_entries) <= 1:
            return grp_entries

        # 偵測同組 JMdict 最高分首義的詞性家族（例如 君(きみ) 首義是 1 pn fam 代名詞，而非 2 n 君王）
        jmdict_top = sorted(
            (e for e in grp_entries if "jmdict" in e.dict_name.lower() and "forms" not in e.pos.lower()),
            key=lambda e: e.score,
            reverse=True,
        )
        consensus_family = self._entry_pos_family(jmdict_top[0].pos) if jmdict_top else ""

        def _sort_key(idx_e: tuple[int, DictEntry]) -> tuple[int, int, int, int, int, int]:
            idx, e = idx_e
            is_spoken = 0 if "口語核心詞庫" in e.dict_name else 1
            is_stub = 1 if _is_inflection_stub_entries([e]) else 0
            reg = _extract_register_tag(e.pos)
            is_archaic = 1 if any(t in reg for t in ("arch", "obs", "hist")) else 0
            pos_match = 0 if (pos_hint and self._pos_hint_matches(e.pos, pos_hint)) else 1
            # 僅當未明確指定特定子詞性、或 JMdict 主詞性與之相容時，以 JMdict 首義詞性為現代語序先驗
            fam = self._entry_pos_family(e.pos)
            fam_match = 0 if (consensus_family and fam == consensus_family) else 1
            return (is_spoken, is_stub, is_archaic, pos_match, fam_match, idx)

        return [e for _, e in sorted(enumerate(grp_entries), key=_sort_key)]

    def _extract_alt_redirect_targets(self, entries: list[DictEntry]) -> list[str]:
        """從 Wiktionary non-lemma 轉址條目（如 さすがalt-of / 流石alternative）抽出目標詞頭。"""
        targets: list[str] = []
        for e in entries:
            if "non-lemma" not in e.pos.lower():
                continue
            for g in e.glosses[:2]:
                for ln in g.splitlines():
                    m = re.match(r"^([\u3040-\u9fff々ー]{2,10})\s*(?:alt-of|alternative)\b", ln.strip(), re.IGNORECASE)
                    if m and m.group(1) not in targets:
                        targets.append(m.group(1))
        return targets

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
        """多候選詞頻＋詞性＋讀音聯合消歧查詢（含假名/通常假名表記融合、純片假名保護與現代詞性排序）。"""
        if not surface:
            return None

        candidates = list(deinflect(surface))
        if lemma_hint and not any(c.term == lemma_hint for c in candidates):
            from galgame_anki.core.deinflector import DeinflectCandidate

            candidates.insert(0, DeinflectCandidate(term=lemma_hint, rules=RULE_NONE, reasons=()))

        is_pure_kata = is_katakana_loanword(surface)
        is_pure_hira = bool(_HIRAGANA_ONLY_RE.match(surface))
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

            if reading_hint or pos_hint:
                matched_entries.sort(
                    key=lambda e: (
                        0 if "口語核心詞庫" in e.dict_name else 1,
                        0 if (reading_hint and e.reading == reading_hint) else 1,
                        0 if (pos_hint and self._pos_hint_matches(e.pos, pos_hint)) else 1,
                    )
                )

            # 先為同詞頭但缺假名讀音的條目（如 wty-ja-ja 気を遣う reading=気を遣う）補對齊標準假名讀音
            canon_kana_rd: dict[str, str] = {}
            for e in matched_entries:
                if e.reading and not _HAS_KANJI_RE.search(e.reading) and e.term not in canon_kana_rd:
                    canon_kana_rd[e.term] = e.reading

            grouped: dict[tuple[str, str], list[DictEntry]] = {}
            for e in matched_entries:
                rd = e.reading
                if _HAS_KANJI_RE.search(e.term) and (not rd or _HAS_KANJI_RE.search(rd)):
                    rd = reading_hint or canon_kana_rd.get(e.term) or rd
                key = (e.term, rd)
                grouped.setdefault(key, []).append(e)

            for (lemma, reading), grp_entries in list(grouped.items())[:6]:
                if (lemma, reading) in seen_keys:
                    continue
                seen_keys.add((lemma, reading))

                ordered_entries = self._order_group_entries(grp_entries, pos_hint=pos_hint)
                pitches, freq = self._query_meta(lemma, reading)
                if freq is None and reading and reading != lemma:
                    p2, f2 = self._query_meta(reading, reading)
                    if not pitches:
                        pitches = p2
                    freq = f2

                bi = [e for e in ordered_entries if e.dict_kind == "bilingual"]
                mono = [e for e in ordered_entries if e.dict_kind == "monolingual"]

                for e in ordered_entries:
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

                score = self._freq_bonus(freq)
                is_stub = cand.rules == RULE_NONE and _is_inflection_stub_entries(ordered_entries)
                if is_stub:
                    score -= 5500
                elif cand.rules == RULE_NONE and lemma == surface:
                    score += 1200

                # 純片假名詞（如 ガチ / ワンチャン）優先原詞本身，避免被罕見片假名音讀漢字（如 月=ガチ）搶占
                if is_pure_kata and cand.rules == RULE_NONE:
                    if lemma == surface and not is_stub:
                        score += 3600
                    elif _HAS_KANJI_RE.search(lemma):
                        score -= 4800

                if lemma_hint and (lemma == lemma_hint or reading == lemma_hint):
                    score += 8000
                if reading_hint and reading == reading_hint:
                    score += 3200
                if pos_hint and any(self._pos_hint_matches(e.pos, pos_hint) for e in ordered_entries):
                    score += 2400
                if any("口語核心詞庫" in e.dict_name for e in ordered_entries):
                    score += 1800
                has_real_bi = bool(bi) and not _is_inflection_stub_entries(bi)
                if has_real_bi:
                    score += 700
                    if any(classify_entry_language(e) == "zh" and not _is_inflection_stub_entries([e]) for e in bi):
                        score += 500
                score -= len(cand.reasons) * 180

                scored_matches.append((score, match_obj))

        if not scored_matches:
            return None

        scored_matches.sort(key=lambda pair: pair[0], reverse=True)
        best = scored_matches[0][1]

        # 假名／通常假名表記補強（如 さすが -> 融合 流石(さすが)，さすがに -> 融合 流石に(さすがに) 與 alt-of 目標）
        if is_pure_hira:
            best_by_lang = best.senses_by_language(max_per_lang=2, convert_tw=False)
            if not best_by_lang["zh"] or not best_by_lang["en"]:
                existing_bi_keys = {(e.dict_name, e.term, e.reading, e.pos) for e in best.bilingual_entries}
                existing_mn_keys = {(e.dict_name, e.term, e.reading, e.pos) for e in best.monolingual_entries}
                for _, cand_m in scored_matches[1:]:
                    if cand_m.reading != best.reading:
                        continue
                    for e in cand_m.bilingual_entries:
                        if _is_inflection_stub_entries([e]):
                            continue
                        ek = (e.dict_name, e.term, e.reading, e.pos)
                        if ek not in existing_bi_keys:
                            best.bilingual_entries.append(e)
                            existing_bi_keys.add(ek)
                    if not best.monolingual_entries:
                        for e in cand_m.monolingual_entries:
                            if _is_inflection_stub_entries([e]):
                                continue
                            ek = (e.dict_name, e.term, e.reading, e.pos)
                            if ek not in existing_mn_keys:
                                best.monolingual_entries.append(e)
                                existing_mn_keys.add(ek)
                best.bilingual_entries = self._order_group_entries(best.bilingual_entries, pos_hint=pos_hint)

            # 若仍無中文義項，檢查 non-lemma 轉址目標（如 さすがに -> 流石 / さすが）
            if not best.senses_by_language(max_per_lang=1, convert_tw=False)["zh"]:
                targets = self._extract_alt_redirect_targets(best.bilingual_entries)
                for tgt in targets[:2]:
                    for re_entry in self._query_exact(tgt):
                        if re_entry.dict_kind != "bilingual" or classify_entry_language(re_entry) != "zh":
                            continue
                        if _is_inflection_stub_entries([re_entry]):
                            continue
                        if re_entry.reading and not (
                            re_entry.reading == best.reading
                            or best.reading.startswith(re_entry.reading)
                        ):
                            continue
                        best.bilingual_entries.append(re_entry)
                best.bilingual_entries = self._order_group_entries(best.bilingual_entries, pos_hint=pos_hint)

        # 保留最多 3 個不同原形且非空存根的備選詞
        alts: list[LookupMatch] = []
        seen_alt_lemmas = {best.lemma}
        for _, cand_match in scored_matches[1:]:
            if cand_match.lemma in seen_alt_lemmas or not cand_match.all_entries:
                continue
            if _is_inflection_stub_entries(cand_match.all_entries):
                continue
            # 若剛把同讀音 uk 漢字（如 流石）併入假名主結果，就不必再當成無關備選重複列出
            if is_pure_hira and cand_match.reading == best.reading and cand_match.bilingual_entries:
                if any(e in best.bilingual_entries for e in cand_match.bilingual_entries):
                    continue
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
