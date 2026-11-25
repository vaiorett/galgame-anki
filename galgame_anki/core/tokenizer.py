"""日語分詞、振假名產生、目標詞高亮與 1T (i+1) 未知詞分析模組。

支援雙後端：
1. 若使用者環境有安裝 SudachiPy，優先以 Sudachi 分詞提升長複合句切分精度。
2. 預設使用零外部編譯依賴的「詞典引導最長匹配 + 活用形還原分詞器」，在 Python 3.15 開箱即用。
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any

from galgame_anki.core.verb_lesson import FORMS, build_lesson, verb_group
from galgame_anki.core.context_meaning import is_otherwise_clause
from galgame_anki.core.dictionary import DictionaryEngine, LookupMatch
from galgame_anki.core.hook_cleaner import clean_galgame_hook_text

# 常見日語助詞、助動詞、接續詞與基礎功能字（不應被當成 1T 目標生詞）
COMMON_FUNCTION_WORDS: set[str] = {
    "の", "に", "は", "を", "た", "が", "で", "て", "と", "し", "れ", "さ",
    "ある", "いる", "も", "する", "から", "な", "こと", "として", "い", "や",
    "れる", "など", "なっ", "ない", "この", "ため", "その", "あっ", "よう",
    "また", "もの", "という", "あり", "まで", "られ", "なる", "へ", "か",
    "だ", "これ", "によって", "により", "おり", "より", "による", "ず", "なり",
    "られる", "において", "ば", "なかっ", "なく", "しかし", "について", "せ",
    "だっ", "その後", "できる", "それ", "う", "ので", "なお", "のみ", "でき",
    "き", "つ", "における", "および", "いう", "さらに", "でも", "ら", "たり",
    "その他", "に関する", "たち", "ます", "ん", "なら", "に対して", "特に",
    "せる", "及び", "これら", "とき", "では", "にて", "ほか", "ながら", "うち",
    "そして", "とともに", "ただし", "かつて", "それぞれ", "または", "お", "ほど",
    "ものの", "に対する", "ほとんど", "と共に", "といった", "です", "とも", "ところ",
    "ここ", "ね", "よ", "わ", "ぞ", "ぜ", "さ", "かな", "っけ", "んだ", "んだから",
    "のに", "けど", "けれど", "けれども", "だって", "って", "じゃ", "じゃない",
    "だろう", "でしょう", "らしい", "みたい", "かも", "だけ", "しか", "ばかり",
    "くらい", "ぐらい", "さえ", "こそ", "でも", "ても", "でも", "ちゃ", "じゃ",
}

_PUNCT_RE = re.compile(r"^[。、！？「」『』（）\(\)\[\]【】…・―—\s,.!?:;\"'~〜]+$")
_KANJI_RE = re.compile(r"[\u4e00-\u9fff々]")
_JP_CHAR_RE = re.compile(r"[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff]")


def is_japanese_text(text: str) -> bool:
    """判斷文本是否包含日文字符。"""
    return bool(_JP_CHAR_RE.search(text))


def contains_kanji(text: str) -> bool:
    return bool(_KANJI_RE.search(text))


def clean_hooked_text(text: str, dedup_repeated: bool = True) -> str:
    """清理 Galgame Textractor / LunaHook 常見的漸進前綴重繪、描邊重複字與重複片語。"""
    return clean_galgame_hook_text(text, dedup=dedup_repeated)


def build_ruby_word(surface: str, reading: str) -> str:
    """為含漢字單詞產生精準 <ruby> 標籤（自動剝離尾部共同假名送り仮名）。"""
    esc_surface = html.escape(surface)
    if not reading or not contains_kanji(surface) or surface == reading:
        return esc_surface

    # 剝離共同尾綴（例如 食べる / たべる -> 食(た)べる）
    s_end = len(surface)
    r_end = len(reading)
    while s_end > 0 and r_end > 0 and surface[s_end - 1] == reading[r_end - 1]:
        if contains_kanji(surface[s_end - 1]):
            break
        s_end -= 1
        r_end -= 1

    # 剝離共同前綴（例如 お願い / おねがい -> お願(ねが)い）
    s_start = 0
    r_start = 0
    while s_start < s_end and r_start < r_end and surface[s_start] == reading[r_start]:
        if contains_kanji(surface[s_start]):
            break
        s_start += 1
        r_start += 1

    prefix = html.escape(surface[:s_start])
    kanji_part = html.escape(surface[s_start:s_end])
    rt_part = html.escape(reading[r_start:r_end])
    suffix = html.escape(surface[s_end:])

    if not kanji_part or not rt_part:
        return esc_surface
    return f"{prefix}<ruby>{kanji_part}<rt>{rt_part}</rt></ruby>{suffix}"


def build_conjugated_ruby(surface: str, lemma: str, lemma_reading: str) -> str:
    """為活用變形後的詞（如「泳げない」<-「泳ぐ / およぐ」）精準只在漢字詞幹上標註假名！"""
    if not lemma_reading or not contains_kanji(surface) or surface == lemma_reading:
        return html.escape(surface)
    if surface == lemma or not lemma:
        return build_ruby_word(surface, lemma_reading)

    # 1. 找出 surface 與 lemma 的共同開頭（包含漢字的部分，如 泳げない vs 泳ぐ -> 共同開頭「泳」）
    common_len = 0
    for a, b in zip(surface, lemma):
        if a == b:
            common_len += 1
        else:
            break

    # 若開頭漢字完全對不上，退回不硬標錯音
    if common_len == 0 or not contains_kanji(surface[:common_len]):
        return html.escape(surface)

    # 2. 從 lemma 尾端剝離假名送り仮名（如 泳ぐ 尾端「ぐ」），同步從 lemma_reading 尾端剝離得到詞幹讀音「およ」
    lemma_tail = lemma[common_len:]
    stem_reading = lemma_reading
    if lemma_tail and not contains_kanji(lemma_tail) and stem_reading.endswith(lemma_tail):
        stem_reading = stem_reading[: -len(lemma_tail)]

    # 若剩餘詞幹本身有假名後綴（例如 踏ん張 vs ふんば），再用 build_ruby_word 精準對齊
    stem_surface = surface[:common_len]
    surface_tail = surface[common_len:]
    if not stem_reading:
        return html.escape(surface)
    return build_ruby_word(stem_surface, stem_reading) + html.escape(surface_tail)


def build_conjugated_inline_furigana(surface: str, lemma: str, lemma_reading: str) -> str:
    """為介面 Lv.1 行內假名提示產生精準標音（例如 急いでる -> 急(いそ)いでる）。"""
    if not lemma_reading or not contains_kanji(surface) or surface == lemma_reading:
        return surface
    if surface == lemma or not lemma:
        return f"{surface}({lemma_reading})"
    common_len = 0
    for a, b in zip(surface, lemma):
        if a == b:
            common_len += 1
        else:
            break
    if common_len == 0 or not contains_kanji(surface[:common_len]):
        return surface
    lemma_tail = lemma[common_len:]
    stem_reading = lemma_reading
    if lemma_tail and not contains_kanji(lemma_tail) and stem_reading.endswith(lemma_tail):
        stem_reading = stem_reading[: -len(lemma_tail)]
    if not stem_reading:
        return surface
    return f"{surface[:common_len]}({stem_reading}){surface[common_len:]}"


def highlight_target_in_sentence(sentence: str, start: int, end: int) -> str:
    """還原原攻略風格，將句中選中的目標詞以【...】包裹。"""
    if start < 0 or end <= start or end > len(sentence):
        return sentence
    return f"{sentence[:start]}【{sentence[start:end]}】{sentence[end:]}"


@dataclass
class Token:
    surface: str
    lemma: str
    reading: str
    start: int
    end: int
    is_punctuation: bool = False
    is_function_word: bool = False
    has_dict_entry: bool = False
    is_known: bool = False
    is_1t_candidate: bool = False
    lookup: LookupMatch | None = None
    is_te_form: bool = False
    verb_type: str = ""

    @property
    def is_content_word(self) -> bool:
        return (
            not self.is_punctuation
            and not self.is_function_word
            and (self.has_dict_entry or contains_kanji(self.surface))
        )


@dataclass
class TokenizedSentence:
    raw: str
    tokens: list[Token]
    unknown_count: int
    is_1t_sentence: bool
    furigana_html: str


# Janome 模組級單例快取（IPADIC 載入只需 1 次，後續分詞 <1ms）
_SHARED_JANOME: Any = None
_JANOME_CHECKED: bool = False

# Janome IPADIC 口語組塊合併表（解決 IPADIC 把口語詞「あんた」「えいっ」「でしょっ」切碎的問題）
_SPOKEN_PRE_MERGES: dict[tuple[str, str], tuple[str, str, str, bool]] = {
    ("あん", "た"): ("あんた", "あんた", "名詞", False),
    ("え", "いっ"): ("えいっ", "えいっ", "感動詞", False),
    ("で", "しょっ"): ("でしょっ", "でしょう", "助動詞", True),
    ("で", "しょ"): ("でしょ", "でしょう", "助動詞", True),
    ("だっ", "て"): ("だって", "だって", "接続詞", True),
    ("じゃ", "ない"): ("じゃない", "じゃない", "助動詞", True),
    ("じゃ", "なかっ"): ("じゃなかっ", "じゃない", "助動詞", True),
    ("ん", "だ"): ("んだ", "んだ", "助動詞", True),
    ("ん", "です"): ("んです", "んです", "助動詞", True),
}

_AUX_ATTACH_SURFACES: set[str] = {
    "た", "だ", "たら", "だら", "たり", "だり",
    "ない", "なかっ", "なく", "ず", "ぬ", "ん",
    "ます", "まし", "ませ", "ましょう",
    "たい", "たかっ", "たく",
    "れる", "れ", "られる", "られ", "せる", "せ", "させる", "させ",
    "う", "よう", "そう",
    "てる", "てた", "てない", "てろ", "でる", "でた", "でない", "でろ",
    "いる", "い", "いた", "いない", "います", "いません",
    "しまう", "しまっ", "しまえ", "ちゃう", "ちゃっ", "じゃう", "じゃっ",
    "おく", "おい", "とく", "とい", "どく", "どい",
    "みる", "み", "すぎる", "すぎ", "過ぎる", "過ぎ",
    "やすい", "にくい",
}


def _get_shared_janome() -> Any:
    global _SHARED_JANOME, _JANOME_CHECKED
    if not _JANOME_CHECKED:
        _JANOME_CHECKED = True
        try:
            from janome.tokenizer import Tokenizer as JanomeTokenizer

            _SHARED_JANOME = JanomeTokenizer()
        except Exception:
            _SHARED_JANOME = None
    return _SHARED_JANOME


class JapaneseTokenizer:
    """日語形態素分詞器（優先 SudachiPy → Janome MeCab IPADIC Viterbi → 詞典最長匹配）。"""

    def __init__(self, dictionary: DictionaryEngine) -> None:
        self.dictionary = dictionary
        self._sudachi = None
        self._sudachi_mode = None
        self._janome = None
        self._try_init_sudachi()
        if self._sudachi is None:
            self._janome = _get_shared_janome()

    def _try_init_sudachi(self) -> None:
        try:
            from sudachipy import dictionary as sudachi_dict, tokenizer as sudachi_tok

            self._sudachi = sudachi_dict.Dictionary().create()
            self._sudachi_mode = sudachi_tok.Tokenizer.SplitMode.C
        except Exception:
            self._sudachi = None

    def tokenize(
        self,
        sentence: str,
        known_words: set[str] | None = None,
    ) -> TokenizedSentence:
        """對整句進行形態素分詞、語境詞形還原、精準振假名與 1T (i+1) 判定。"""
        known = known_words or set()
        if self._sudachi is not None:
            tokens = self._tokenize_sudachi(sentence)
        elif self._janome is not None:
            tokens = self._tokenize_janome(sentence)
        else:
            tokens = self._tokenize_dict_greedy(sentence)

        if "じゃないと" in sentence:
            # 不同分詞器可能將此接續詞切成二至四個詞；只合併完整相鄰的範圍。
            grouped = []
            i = 0
            while i < len(tokens):
                token = tokens[i]
                if is_otherwise_clause(sentence, token.start):
                    end = token.start + len("じゃないと")
                    j = i
                    while j + 1 < len(tokens) and tokens[j].end < end and tokens[j].end == tokens[j + 1].start:
                        j += 1
                    if tokens[j].end == end:
                        lookup = self.dictionary.lookup_exact_or_deinflect("じゃないと", lemma_hint="じゃないと")
                        if lookup and lookup.lemma != "じゃないと":
                            lookup = None
                        token = Token("じゃないと", "じゃないと", "じゃないと", token.start, end,
                                      is_function_word=True, has_dict_entry=lookup is not None, lookup=lookup)
                        i = j
                grouped.append(token)
                i += 1
            tokens = grouped

        # 標記已知詞與未知實詞數量
        unknown_by_lemma: dict[str, list[Token]] = {}
        for tok in tokens:
            if tok.is_punctuation or tok.is_function_word:
                tok.is_known = True
                continue
            if tok.lemma in known or tok.surface in known:
                tok.is_known = True
            elif tok.is_content_word:
                unknown_by_lemma.setdefault(tok.lemma or tok.surface, []).append(tok)

        is_1t = len(unknown_by_lemma) == 1
        if is_1t:
            for occurrence in next(iter(unknown_by_lemma.values())):
                occurrence.is_1t_candidate = True

        # 生成整句振假名 HTML（活用變形詞精準只標漢字詞幹，避免出現「急いでる(いそぐ)」錯標！）
        ruby_parts = [build_conjugated_ruby(t.surface, t.lemma, t.reading) for t in tokens]
        furigana_html = "".join(ruby_parts)

        return TokenizedSentence(
            raw=sentence,
            tokens=tokens,
            unknown_count=len(unknown_by_lemma),
            is_1t_sentence=is_1t,
            furigana_html=furigana_html,
        )

    def _tokenize_sudachi(self, sentence: str) -> list[Token]:
        tokens: list[Token] = []
        assert self._sudachi is not None
        nodes = list(self._sudachi.tokenize(sentence, self._sudachi_mode))
        consumed = 0
        for i, m in enumerate(nodes):
            if i < consumed:
                continue
            surface = m.surface()
            start = m.begin()
            end = m.end()
            pos0 = m.part_of_speech()[0] if m.part_of_speech() else ""
            if pos0 == "動詞" and i + 1 < len(nodes):
                base = m.dictionary_form()
                conjugation = m.part_of_speech()[4]
                group = verb_group(base, conjugation)
                forms = {lesson.surface: form for form in FORMS
                         if (lesson := build_lesson(base, group, form))}
                # 只跨過完整而相連的詞素，不從「ていた」或「てください」中間截斷詞素。
                best = None
                for j in range(i + 1, len(nodes)):
                    nxt = nodes[j]
                    if nxt.begin() != nodes[j - 1].end():
                        break
                    candidate = sentence[start:nxt.end()]
                    if not any(word.startswith(candidate) for word in forms):
                        break
                    if candidate in forms:
                        best = (candidate, nxt.end(), j + 1)
                if best:
                    surface, end, consumed = best
                    lookup = self.dictionary.lookup_exact_or_deinflect(surface, lemma_hint=base)
                    if not lookup or lookup.lemma != base:
                        lookup = self.dictionary.lookup_exact_or_deinflect(base, lemma_hint=base)
                    reading = lookup.reading if lookup else _kata_to_hira(m.reading_form())
                    tokens.append(Token(surface, base, reading, start, end, lookup=lookup,
                                        has_dict_entry=lookup is not None, verb_type=conjugation,
                                        is_te_form=forms[surface] == "te"))
                    continue
            if (i and tokens and surface in ("て", "で")
                    and tuple(m.part_of_speech()[:2]) == ("助詞", "接続助詞")
                    and nodes[i-1].part_of_speech()[0] in ("動詞", "形容詞")
                    and tokens[-1].end == start):
                verb = tokens[-1]
                verb.surface += surface
                verb.end = end
                verb.is_te_form = True
                base = nodes[i-1].dictionary_form()
                verb.lookup = self.dictionary.lookup_exact_or_deinflect(verb.surface, lemma_hint=base)
                verb.has_dict_entry = verb.lookup is not None
                verb.lemma = verb.lookup.lemma if verb.lookup else base
                if verb.lookup:
                    verb.reading = verb.lookup.reading
                continue
            is_punct = bool(_PUNCT_RE.match(surface)) or pos0 in ("補助記号", "空白")
            is_func = (
                surface in COMMON_FUNCTION_WORDS
                or pos0 in ("助詞", "助動詞", "記号")
            )
            lemma = m.dictionary_form() or surface
            # Sudachi 讀音為片假名，轉平假名
            kata = m.reading_form() or surface
            reading = _kata_to_hira(kata)
            lookup = None if is_punct else (
                self.dictionary.lookup_exact_or_deinflect(surface)
                or self.dictionary.lookup_exact_or_deinflect(lemma)
            )
            if lookup:
                lemma = lookup.lemma
                if not contains_kanji(lookup.reading):
                    reading = lookup.reading
            tokens.append(
                Token(
                    surface=surface,
                    lemma=lemma,
                    reading=reading,
                    start=start,
                    end=end,
                    is_punctuation=is_punct,
                    is_function_word=is_func,
                    has_dict_entry=lookup is not None,
                    verb_type=m.part_of_speech()[4] if pos0 == "動詞" and lemma == m.dictionary_form() else "",
                    lookup=lookup,
                )
            )
        return tokens

    def _tokenize_janome(self, sentence: str) -> list[Token]:
        """Janome (MeCab IPADIC Viterbi) 統計形態素分詞 + 口語/活用複合組塊合併。"""
        assert self._janome is not None
        raw_nodes: list[dict[str, Any]] = []
        cursor = 0
        for m in self._janome.tokenize(sentence):
            surf = m.surface
            pos_parts = m.part_of_speech.split(",")
            pos0 = pos_parts[0] if pos_parts else ""
            pos1 = pos_parts[1] if len(pos_parts) > 1 else ""
            base = m.base_form if (m.base_form and m.base_form != "*") else surf
            rd_raw = m.reading if (m.reading and m.reading != "*") else surf
            rd_hira = _kata_to_hira(rd_raw)
            start = sentence.find(surf, cursor)
            if start < 0:
                start = cursor
            end = start + len(surf)
            cursor = end
            raw_nodes.append(
                {
                    "surface": surf,
                    "base": base,
                    "reading": rd_hira,
                    "verb_type": m.infl_type if pos0 == "動詞" else "",
                    "pos0": pos0,
                    "pos1": pos1,
                    "start": start,
                    "end": end,
                }
            )

        # Pass 1: 合併口語固定詞（あんた / えいっ / でしょっ）與連續標點
        pass1: list[dict[str, Any]] = []
        i = 0
        n = len(raw_nodes)
        while i < n:
            cur = raw_nodes[i]
            if i + 1 < n:
                pair_key = (cur["surface"], raw_nodes[i + 1]["surface"])
                if pair_key in _SPOKEN_PRE_MERGES:
                    msurf, mbase, mpos0, mfunc = _SPOKEN_PRE_MERGES[pair_key]
                    pass1.append(
                        {
                            "surface": msurf,
                            "base": mbase,
                            "reading": _kata_to_hira(msurf),
                            "pos0": mpos0,
                            "pos1": "一般",
                            "start": cur["start"],
                            "end": raw_nodes[i + 1]["end"],
                            "force_func": mfunc,
                        }
                    )
                    i += 2
                    continue
            # 連續標點與空白合併為單一標點節點
            if bool(_PUNCT_RE.match(cur["surface"])) or cur["pos0"] == "記号":
                j = i + 1
                surf_acc = cur["surface"]
                end_acc = cur["end"]
                while j < n and (bool(_PUNCT_RE.match(raw_nodes[j]["surface"])) or raw_nodes[j]["pos0"] == "記号"):
                    surf_acc += raw_nodes[j]["surface"]
                    end_acc = raw_nodes[j]["end"]
                    j += 1
                pass1.append(
                    {
                        "surface": surf_acc,
                        "base": surf_acc,
                        "reading": surf_acc,
                        "pos0": "記号",
                        "pos1": "一般",
                        "start": cur["start"],
                        "end": end_acc,
                        "is_punct": True,
                    }
                )
                i = j
                continue
            pass1.append(cur)
            i += 1

        # Pass 2: 合併動詞／形容詞的活用後綴（如 わかっ+た -> わかった, し+たら -> したら, 急い+でる -> 急いでる, 泳げ+ない -> 泳げない）
        merged: list[dict[str, Any]] = []
        i = 0
        m_len = len(pass1)
        while i < m_len:
            node = dict(pass1[i])
            if node["pos0"] in ("動詞", "形容詞") and node["pos1"] == "自立":
                j = i + 1
                while j < m_len:
                    nxt = pass1[j]
                    if nxt["start"] != node["end"]:
                        break
                    nsurf = nxt["surface"]
                    npos0 = nxt["pos0"]
                    npos1 = nxt["pos1"]
                    # 1) 緊跟助動詞或非自立/接尾動詞（た / たら / ない / たい / てる / でる / すぎる / ちゃう / しまう）
                    if (
                        (npos0 == "助動詞" and nsurf in _AUX_ATTACH_SURFACES)
                        or (npos0 == "動詞" and npos1 in ("非自立", "接尾") and nsurf in _AUX_ATTACH_SURFACES)
                        or (npos0 == "形容詞" and npos1 in ("非自立", "接尾") and nsurf in ("ない", "なかっ", "やすい", "にくい"))
                    ):
                        node["surface"] += nsurf
                        node["end"] = nxt["end"]
                        j += 1
                        continue
                    # IPADIC 有時將「読んでろ」的命令詞尾單獨標為名詞「ろ」。
                    if nsurf == "ろ" and node.get("te_end") == node["end"]:
                        node["surface"] += nsurf
                        node["end"] = nxt["end"]
                        j += 1
                        break
                    # 接續助詞與動詞共同構成て形；後續補助動詞由下一輪辨認。
                    if npos0 == "助詞" and npos1 == "接続助詞" and nsurf in ("て", "で"):
                        node["surface"] += nsurf
                        node["end"] = nxt["end"]
                        node["te_end"] = nxt["end"]
                        j += 1
                        continue
                    break
                merged.append(node)
                i = j
                continue

            # 若「一方 + だ」或「と + いう」等常見慣用句型未合併則原樣保留
            merged.append(node)
            i += 1

        # Pass 3: 透過多候選詞頻＋詞性＋讀音聯合消歧查詢字典
        tokens: list[Token] = []
        for nd in merged:
            surface = nd["surface"]
            start = nd["start"]
            end = nd["end"]
            is_punct = bool(nd.get("is_punct")) or bool(_PUNCT_RE.match(surface))
            pos0 = nd["pos0"]
            base_hint = nd["base"]
            rd_hint = nd["reading"]

            if is_punct:
                tokens.append(
                    Token(
                        surface=surface,
                        lemma=surface,
                        reading=surface,
                        start=start,
                        end=end,
                        is_punctuation=True,
                    )
                )
                continue

            is_func = bool(nd.get("force_func")) or (
                surface in COMMON_FUNCTION_WORDS
                or pos0 in ("助詞", "助動詞", "接続詞")
            )
            # 獨立實義動詞（如 したら / わかった / 知りたい）絕不因表層或助詞衝突被標成功能詞
            if pos0 in ("動詞", "形容詞", "名詞", "副詞", "感動詞", "連体詞") and nd["pos1"] != "非自立":
                if surface not in COMMON_FUNCTION_WORDS:
                    is_func = False

            # 若原句是未活用單詞，傳入 Janome 統計讀音（如 足 -> あし, 事 -> こと, 君 -> きみ/くん）
            pos_hint = nd["pos1"] if (pos0 == "名詞" and nd["pos1"] in ("代名詞", "接尾")) else pos0
            pass_rd = rd_hint if (surface == base_hint and rd_hint and not contains_kanji(rd_hint)) else ""
            # 若 Janome 識別「泳げない」的 stem base 是可能形一段「泳げる」，改不鎖死 lemma_hint 讓 deinflector 還原到五段「泳ぐ」
            pass_lemma = "" if (base_hint.endswith("える") or base_hint.endswith("げる") or base_hint.endswith("ける")) and surface != base_hint else base_hint

            lookup = self.dictionary.lookup_exact_or_deinflect(
                surface,
                pos_hint=pos_hint,
                reading_hint=pass_rd,
                lemma_hint=pass_lemma,
            )
            if lookup is None and base_hint and base_hint != surface:
                lookup = self.dictionary.lookup_exact_or_deinflect(
                    base_hint,
                    pos_hint=pos_hint,
                    reading_hint=pass_rd,
                )

            lemma = lookup.lemma if lookup else base_hint
            reading = lookup.reading if (lookup and lookup.reading and not contains_kanji(lookup.reading)) else rd_hint
            # 若表層本身未活用且 Janome 給了精準讀音（如 足 -> あし），確保不落回漢字
            if surface == lemma and pass_rd and (not reading or contains_kanji(reading)):
                reading = pass_rd

            tokens.append(
                Token(
                    surface=surface,
                    lemma=lemma,
                    reading=reading,
                    start=start,
                    end=end,
                    is_punctuation=False,
                    is_function_word=is_func,
                    has_dict_entry=lookup is not None,
                    lookup=lookup,
                    is_te_form=nd.get("te_end") == end,
                    verb_type=nd.get("verb_type", "") if lemma == base_hint else "",
                )
            )
        return tokens

    def _tokenize_dict_greedy(self, sentence: str) -> list[Token]:
        tokens: list[Token] = []
        n = len(sentence)
        pos = 0

        while pos < n:
            ch = sentence[pos]

            # 1. 標點或空白
            if _PUNCT_RE.match(ch):
                end = pos + 1
                while end < n and _PUNCT_RE.match(sentence[end]):
                    end += 1
                surf = sentence[pos:end]
                tokens.append(
                    Token(
                        surface=surf,
                        lemma=surf,
                        reading=surf,
                        start=pos,
                        end=end,
                        is_punctuation=True,
                    )
                )
                pos = end
                continue

            # 2. 詞典最長匹配（含活用形還原）
            matches = self.dictionary.lookup_at_offset(sentence, pos, max_len=14)
            best: LookupMatch | None = None
            if matches:
                # 優先選擇較長匹配；若長度為 1 且是常見助詞則檢查是否先當功能詞
                best = matches[0]

            if best is not None:
                surf = best.surface
                end = pos + len(surf)
                is_func = surf in COMMON_FUNCTION_WORDS or best.lemma in COMMON_FUNCTION_WORDS
                # 若表層詞是「〜すぎた」「〜なかった」等活用形，讓整詞讀音保留合理顯示
                display_reading = best.reading if surf == best.lemma else best.reading
                tokens.append(
                    Token(
                        surface=surf,
                        lemma=best.lemma,
                        reading=display_reading,
                        start=pos,
                        end=end,
                        is_function_word=is_func,
                        has_dict_entry=True,
                        lookup=best,
                    )
                )
                pos = end
                continue

            # 3. 詞典未命中時的字元群組備援（連續片假名 / 連續漢字 / 連續英數 / 單平假名）
            if re.match(r"[\u30a0-\u30ffー]", ch):
                end = pos + 1
                while end < n and re.match(r"[\u30a0-\u30ffー]", sentence[end]):
                    end += 1
                surf = sentence[pos:end]
                tokens.append(
                    Token(
                        surface=surf,
                        lemma=surf,
                        reading=_kata_to_hira(surf),
                        start=pos,
                        end=end,
                        is_function_word=surf in COMMON_FUNCTION_WORDS,
                    )
                )
                pos = end
            elif contains_kanji(ch):
                end = pos + 1
                while end < n and contains_kanji(sentence[end]):
                    # 若後面開頭能命中詞典就提前截斷，避免吞掉下一個已知詞
                    if self.dictionary.lookup_at_offset(sentence, end, max_len=10):
                        break
                    end += 1
                surf = sentence[pos:end]
                tokens.append(
                    Token(
                        surface=surf,
                        lemma=surf,
                        reading=surf,
                        start=pos,
                        end=end,
                    )
                )
                pos = end
            elif ch.isascii() and ch.isalnum():
                end = pos + 1
                while end < n and sentence[end].isascii() and sentence[end].isalnum():
                    end += 1
                surf = sentence[pos:end]
                tokens.append(
                    Token(
                        surface=surf,
                        lemma=surf,
                        reading=surf,
                        start=pos,
                        end=end,
                        is_function_word=True,
                    )
                )
                pos = end
            else:
                # 嘗試將連續常見功能詞（如 んだから / ている / という）合併
                matched_func = None
                for flen in (4, 3, 2):
                    cand = sentence[pos : pos + flen]
                    if cand in COMMON_FUNCTION_WORDS:
                        matched_func = cand
                        break
                if matched_func:
                    end = pos + len(matched_func)
                    tokens.append(
                        Token(
                            surface=matched_func,
                            lemma=matched_func,
                            reading=matched_func,
                            start=pos,
                            end=end,
                            is_function_word=True,
                        )
                    )
                    pos = end
                else:
                    tokens.append(
                        Token(
                            surface=ch,
                            lemma=ch,
                            reading=ch,
                            start=pos,
                            end=pos + 1,
                            is_function_word=ch in COMMON_FUNCTION_WORDS,
                        )
                    )
                    pos += 1

        return tokens


def _kata_to_hira(text: str) -> str:
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if 0x30A1 <= code <= 0x30F6:
            out.append(chr(code - 0x60))
        else:
            out.append(ch)
    return "".join(out)
