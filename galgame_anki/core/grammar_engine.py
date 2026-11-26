"""離線日語語法拆解、助詞角色解說、詞性白話翻譯與 Web AI 提問詞產生器（零付費 API 依賴）。"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from galgame_anki.core.context_meaning import format_contextual_meaning
from galgame_anki.core.verb_lesson import VerbLesson, lesson_for_token
from galgame_anki.core.tokenizer import Token, TokenizedSentence


@dataclass(frozen=True)
class GrammarHit:
    pattern_id: str
    level: str
    category: str
    name: str
    formula: str
    meaning: str
    example: str
    matched_text: str


@dataclass(frozen=True)
class ParticleInfo:
    surface: str
    level: str
    role: str
    formula: str
    meaning: str


@dataclass(frozen=True)
class ClauseChunk:
    """依助詞與句型接續切出的日語文節，標明在整句中的邏輯角色。"""

    text: str
    role_key: str
    role_label: str
    gloss: str = ""
    particle: str = ""
    clause_index: int = 0


@dataclass(frozen=True)
class SyntaxRelation:
    """明確呈現「誰修飾誰／前件如何導向結論」的句法箭頭。"""

    source: str
    relation: str
    target: str
    explanation: str = ""


@dataclass
class SentenceGrammarAnalysis:
    sentence: str
    grammar_hits: list[GrammarHit] = field(default_factory=list)
    particle_roles: list[ParticleInfo] = field(default_factory=list)
    conjugation_notes: list[str] = field(default_factory=list)
    word_glosses: list[tuple[str, str, str]] = field(default_factory=list)  # (surface, tag, concise_meaning)
    chunks: list[ClauseChunk] = field(default_factory=list)
    skeleton_topic: str = ""
    skeleton_predicate: str = ""
    logic_relations: list[SyntaxRelation] = field(default_factory=list)
    reading_order_hint: str = ""
    verb_lessons: list[VerbLesson] = field(default_factory=list)

    def to_compact_html(self, accent_color: str = "#ec4899", muted_color: str = "#8e7a9c", focus_word: str = "") -> str:
        """輸出清晰的結構化邏輯 HTML：主幹 → 分塊結構 → 修飾箭頭 → 命中句型 → 活用拆解。"""
        blocks: list[str] = []

        if self.skeleton_predicate:
            topic_txt = html.escape(self.skeleton_topic or "（省略主語）")
            pred_txt = html.escape(self.skeleton_predicate)
            hint_html = (
                f'<div style="font-size:12px; color:{muted_color}; margin-top:4px; line-height:1.5;">'
                f"{html.escape(self.reading_order_hint)}</div>"
                if self.reading_order_hint
                else ""
            )
            blocks.append(
                f'<div style="margin-bottom:9px; padding:8px 11px; border-left:3px solid {accent_color}; '
                f'background:rgba(127,127,127,0.08); border-radius:6px;">'
                f'<div style="font-size:11px; font-weight:500; color:{accent_color}; margin-bottom:3px;">句子主幹</div>'
                f'<div style="font-size:14px; font-weight:500;">{topic_txt} '
                f'<span style="color:{accent_color};">──▶</span> {pred_txt}</div>'
                f"{hint_html}</div>"
            )

        for lesson in self.verb_lessons:
            if not focus_word or focus_word in (lesson.lemma, lesson.surface):
                blocks.append(lesson.html())

        if self.chunks:
            blocks.append(f'<div style="font-weight:500; color:{accent_color}; margin-bottom:4px;">結構分塊</div>')
            chunk_rows: list[str] = []
            for ch in self.chunks:
                gl = f" — {html.escape(ch.gloss)}" if ch.gloss else ""
                chunk_rows.append(
                    f'<div style="margin:2px 0; font-size:13px; line-height:1.55;">'
                    f'<span style="color:{muted_color}; font-size:11px;">[{html.escape(ch.role_label)}]</span> '
                    f"<b>{html.escape(ch.text)}</b>{gl}</div>"
                )
            blocks.append(f'<div style="margin-bottom:9px;">{"".join(chunk_rows)}</div>')

        if self.logic_relations:
            blocks.append(f'<div style="font-weight:500; color:{accent_color}; margin-bottom:4px;">修飾與邏輯鏈</div>')
            for rel in self.logic_relations:
                note = f' <span style="font-size:11.5px; color:{muted_color};">（{html.escape(rel.explanation)}）</span>' if rel.explanation else ""
                blocks.append(
                    f'<div style="margin:3px 0; font-size:13px; line-height:1.55;">'
                    f"<b>{html.escape(rel.source)}</b> "
                    f'<span style="color:{accent_color};">── {html.escape(rel.relation)} ──▶</span> '
                    f"<b>{html.escape(rel.target)}</b>{note}</div>"
                )

        if self.grammar_hits:
            blocks.append(f'<div style="font-weight:500; color:{accent_color}; margin:9px 0 6px 0;">命中句型</div>')
            for hit in self.grammar_hits:
                blocks.append(
                    f'<div style="margin-bottom:7px; padding:7px 10px; border-left:3px solid {accent_color}; '
                    f'background:rgba(127,127,127,0.07); border-radius:6px;">'
                    f'<div><b>[{html.escape(hit.level)}] {html.escape(hit.name)}</b> '
                    f'<span style="font-size:11px; color:{muted_color};">{html.escape(hit.category)} · 「{html.escape(hit.matched_text)}」</span></div>'
                    f'<div style="font-size:12px; color:{muted_color}; margin:2px 0;">接續：{html.escape(hit.formula)}</div>'
                    f'<div style="font-size:13px; line-height:1.55;">{html.escape(hit.meaning)}</div>'
                    f"</div>"
                )

        if self.conjugation_notes:
            blocks.append(f'<div style="font-weight:500; color:{accent_color}; margin:8px 0 4px 0;">活用拆解</div>')
            for note in self.conjugation_notes:
                blocks.append(f'<div style="font-size:13px; margin:2px 0;">• {html.escape(note)}</div>')

        if not blocks:
            return (
                f'<p style="color:{muted_color};">本句為基礎詞組，點上方單詞或助詞即可查看詞義與用法。</p>'
            )
        return "".join(blocks)


def translate_sentence_free(sentence: str, target_lang: str = "zh-TW") -> str:
    """按需呼叫免費免金鑰翻譯端點核對整句自然意思（僅於使用者主動點擊時觸發，零付費 API）。"""
    text = sentence.strip()
    if not text:
        return ""
    # 字幕行首的人名屬於旁註，分開保留可避免機翻將說話者當作受詞。
    speaker = re.match(r"^[（(]([一-龯ぁ-んァ-ヶー]{1,10})[）)]\s*(?=\S)", text)
    if speaker:
        text = text[speaker.end():]
    resp = httpx.get(
        "https://translate.googleapis.com/translate_a/single",
        params={"client": "gtx", "sl": "ja", "tl": target_lang, "dt": "t", "q": text},
        timeout=8.0,
    )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list) and data and isinstance(data[0], list):
        parts = [str(seg[0]) for seg in data[0] if isinstance(seg, list) and seg and seg[0]]
        translated = "".join(parts).strip()
        if translated and speaker:
            return f"（{speaker[1]}）{translated}"
        return translated
    return ""


def explain_pos_friendly(pos_raw: str) -> str:
    """把詞典裡艱澀的詞性縮寫（如 他下一 / v5r / adj-i）翻成白話中文標籤。"""
    if not pos_raw:
        return ""
    p = pos_raw.lower()
    tags: list[str] = []

    if "v1" in p or "一段" in p or "下一" in p or "上一" in p:
        tags.append("一段動詞")
    elif "v5" in p or "五段" in p:
        tags.append("五段動詞")
    elif "vs" in p or "サ変" in p:
        tags.append("サ變動詞")
    elif "vk" in p or "カ変" in p:
        tags.append("カ變動詞")
    elif p == "v" or "verb" in p or "動詞" in p:
        tags.append("動詞")

    if "vt" in p or "他" in pos_raw:
        tags.append("他動詞")
    elif "vi" in p or "自" in pos_raw:
        tags.append("自動詞")

    if "adj-i" in p or "形容詞" in pos_raw:
        tags.append("い形容詞")
    elif "adj-na" in p or "形容動詞" in pos_raw:
        tags.append("な形容詞")
    elif "adv" in p or "副" in pos_raw:
        tags.append("副詞")
    elif "conj" in p or "接" in pos_raw:
        tags.append("接續詞")
    elif "exp" in p or "慣用" in pos_raw:
        tags.append("慣用語")
    elif re.search(r"\bn\b", p) or "名" in pos_raw:
        tags.append("名詞")

    # 去重保持順序
    out: list[str] = []
    for t in tags:
        if t not in out:
            out.append(t)
    return " · ".join(out[:3])


def build_web_ai_prompt(
    sentence: str,
    target_word: str = "",
    target_reading: str = "",
    grammar_hits: list[GrammarHit] | None = None,
) -> str:
    """為使用者的 Google AI Pro (Gemini) / ChatGPT Plus 網頁版產出 1 鍵可貼上的高品質日語學習提問詞。"""
    lines = [
        "我正在透過 Galgame / 動畫啃生肉系統性學習日語，請幫我用清晰的邏輯拆解這句日語：",
        f"【日語原句】{sentence}",
    ]
    if target_word:
        rd = f"（{target_reading}）" if target_reading and target_reading != target_word else ""
        lines.append(f"【特別關注詞】{target_word}{rd}")
    if grammar_hits:
        names = "、".join(f"[{h.level}] {h.name}" for h in grammar_hits[:4])
        lines.append(f"【句中出現的句型】{names}")
    lines.extend(
        [
            "",
            "請用繁體中文精簡、有條理地回答：",
            "1. 自然中文翻譯（保留語氣）",
            "2. 句子骨架拆解：誰是主語/主題、對象是誰、每個助詞的作用",
            "3. 關鍵動詞/形容詞的「原型 → 變形步驟 → 句中意思」",
            "4. 關注詞與句型的實戰用法＋1 個簡單生活造句",
        ]
    )
    return "\n".join(lines)


_SENTENCE_SPLIT_CHARS = set("。！？!?")
_COMMA_CHARS = set("、,")
_FINAL_MODAL_SURFACES = frozenset(
    {"よ", "ね", "ねぇ", "ねえ", "わ", "ぞ", "ぜ", "さ", "かな", "か", "の", "のか", "だ", "です", "ます", "な", "なあ", "なぁ"}
)
_CASE_PARTICLE_ROLES: dict[str, tuple[str, str]] = {
    "は": ("topic", "主題"),
    "が": ("subject", "主語"),
    "を": ("object", "受詞"),
    "で": ("place", "場所/手段"),
    "に": ("target", "對象/方向"),
    "へ": ("target", "方向"),
    "と": ("quote", "對象/並列"),
    "から": ("time_place", "起點/原因"),
    "まで": ("time_place", "終點"),
    "より": ("modifier", "比較基準"),
    "も": ("topic", "並列主題"),
    "の": ("modifier", "修飾"),
}
_CLAUSE_CONJ_ROLES: dict[str, tuple[str, str]] = {
    "ても": ("concession", "讓步前件"),
    "でも": ("concession", "讓步前件"),
    "のに": ("concession", "逆接前件"),
    "けど": ("concession", "轉折前件"),
    "けれど": ("concession", "轉折前件"),
    "けれども": ("concession", "轉折前件"),
    "が": ("concession", "轉折前件"),
    "ので": ("reason", "原因前件"),
    "から": ("reason", "原因前件"),
    "ば": ("condition", "條件前件"),
    "たら": ("condition", "條件前件"),
    "なら": ("condition", "條件前件"),
    "と": ("condition", "條件前件"),
    "ながら": ("modifier", "伴隨前件"),
}
_COMMON_ADVERBS = frozenset(
    {
        "まるで", "やけに", "少し", "もっと", "とても", "ずっと", "たぶん", "きっと",
        "もう", "まだ", "ただ", "すぐ", "ちょっと", "本当に", "やはり", "やっぱり",
        "なぜか", "すでに", "すでに", "まったく", "全く", "かなり", "ずいぶん", "きっと",
    }
)
_DEMONSTRATIVES = frozenset({"この", "その", "あの", "どの", "こんな", "そんな", "あんな", "どんな"})
_PREFIX_SURFACES = frozenset({"再", "不", "無", "非", "未", "お", "ご", "大", "小", "新", "元"})
_TIME_WORDS = frozenset(
    {"明日", "今日", "昨日", "今", "毎日", "朝", "昼", "夜", "今年", "来年", "去年", "先ほど", "さっき", "以降", "以前", "以後"}
)
_SURU_LEMMAS = frozenset({"する", "為る", "できる", "出来る"})
_AUX_FOLLOW_LEMMAS = frozenset(
    {"ない", "無い", "なる", "成る", "いる", "居る", "しまう", "仕舞う", "くる", "来る", "いく", "行く", "みる", "見る", "おく", "置く"}
)


def _first_short_gloss(tok: Token, max_len: int = 12) -> str:
    """從 Token 擷取極精短的語塊核心中文義（去雜訊、不含長括號說明）。"""
    if tok.lookup is None:
        return ""
    base = tok.lookup.best_concise_meaning("bilingual", strict=True)
    if not base:
        return ""
    ctx = format_contextual_meaning(tok.surface, tok.lemma, base, tok.lookup.reasons)
    short = ctx.split("（原形")[0].strip() if "（原形" in ctx else ctx
    # 去掉 Wiktionary 詞頭重複（如「無い：没有，无」->「没有，无」）
    if "：" in short and len(short.split("：", 1)[0]) <= 5:
        short = short.split("：", 1)[1].strip()
    # 去掉括號補充後取前 1~2 個最核心詞
    short = re.sub(r"[（\(][^）\)]{1,14}[）\)]", "", short).strip(" 、，,;；。")
    parts = [s.strip() for s in re.split(r"[、，,;；/／]", short) if s.strip()]
    out = "、".join(parts[:2]) if parts else short
    if len(out) > max_len:
        out = out[: max_len - 1].rstrip(" 、，,") + "…"
    return out


def _tok_pos(tok: Token) -> str:
    if tok.lookup and tok.lookup.all_entries:
        return (tok.lookup.all_entries[0].pos or "").lower()
    return ""


def _is_verb_or_adj(tok: Token, particles: dict[str, ParticleInfo]) -> bool:
    if tok.is_punctuation:
        return False
    if tok.surface in particles and tok.surface not in ("ない", "なかった", "だ", "です"):
        return False
    if tok.verb_type:
        return True
    pos = _tok_pos(tok)
    if any(k in pos for k in ("v1", "v5", "vs", "vk", "godan", "ichidan", "verb", "adj-i", "adj-na", "adj", "動", "形")):
        if "adj-pn" not in pos:
            return True
    if tok.lookup and tok.lookup.reasons:
        return True
    if tok.lemma in ("ない", "無い", "ある", "有る", "する", "なる", "いる", "だ", "です"):
        return True
    return False


def _is_adverb(tok: Token) -> bool:
    if tok.surface in _COMMON_ADVERBS:
        return True
    pos = _tok_pos(tok)
    return "adv" in pos or "副" in pos


def _is_noun_like(tok: Token, particles: dict[str, ParticleInfo]) -> bool:
    if tok.is_punctuation or tok.surface in particles:
        return False
    if _is_adverb(tok) or _is_verb_or_adj(tok, particles):
        return False
    if tok.surface in _FINAL_MODAL_SURFACES or tok.surface in ("という", "っていう", "といった", "て", "で"):
        return False
    return True


def _split_token_sentences(tokens: list[Token]) -> list[list[Token]]:
    """依句末標點（。！？）切分子句，過濾開頭結尾引號，保留逗號供語塊邊界判斷。"""
    clauses: list[list[Token]] = []
    cur: list[Token] = []
    for tok in tokens:
        if tok.is_punctuation:
            if any(ch in _SENTENCE_SPLIT_CHARS for ch in tok.surface):
                if cur:
                    clauses.append(cur)
                    cur = []
            elif any(ch in _COMMA_CHARS for ch in tok.surface):
                if cur:
                    cur.append(tok)
            continue
        cur.append(tok)
    if cur:
        clauses.append(cur)
    return clauses


def _chunk_single_clause(
    tokens: list[Token],
    clause_idx: int,
    particles: dict[str, ParticleInfo],
) -> list[ClauseChunk]:
    """把單一子句的 Token 序列聚合成清晰的日語文節（ClauseChunk）。"""
    chunks: list[ClauseChunk] = []
    n = len(tokens)
    i = 0

    while i < n:
        tok = tokens[i]
        if tok.is_punctuation:
            i += 1
            continue

        # 1) 固定多詞修飾連接：か + の + よう + に/な/だ
        if (
            i + 3 < n
            and tokens[i].surface == "か"
            and tokens[i + 1].surface == "の"
            and tokens[i + 2].surface == "よう"
            and tokens[i + 3].surface in ("に", "な", "だ")
        ):
            txt = "".join(tokens[k].surface for k in range(i, i + 4))
            chunks.append(ClauseChunk(txt, "simile", "比喻接續", "彷彿…一樣", particle=txt, clause_index=clause_idx))
            i += 4
            continue

        # の + よう + に/な/だ
        if (
            i + 2 < n
            and tokens[i].surface == "の"
            and tokens[i + 1].surface == "よう"
            and tokens[i + 2].surface in ("に", "な", "だ")
        ):
            txt = "".join(tokens[k].surface for k in range(i, i + 3))
            chunks.append(ClauseChunk(txt, "simile", "比喻接續", "像…一樣", particle=txt, clause_index=clause_idx))
            i += 3
            continue

        # という / っていう / といった
        if tok.surface in ("という", "っていう", "といった"):
            chunks.append(ClauseChunk(tok.surface, "content_link", "內容修飾", "…這樣的", particle=tok.surface, clause_index=clause_idx))
            i += 1
            continue

        # 2) 副詞（如 まるで、やけに、少し）
        if _is_adverb(tok):
            role_lbl = "比喻副詞" if tok.surface == "まるで" else "程度修飾"
            chunks.append(
                ClauseChunk(tok.surface, "adverb", role_lbl, _first_short_gloss(tok), clause_index=clause_idx)
            )
            i += 1
            continue

        # 3) 述語（動詞／形容詞，或 前綴+名詞+する サ變動詞）
        is_suru_compound = False
        suru_end = i
        if _is_noun_like(tok, particles) or tok.surface in _PREFIX_SURFACES:
            j = i
            while j < n and not tokens[j].is_punctuation and (
                tokens[j].surface in _PREFIX_SURFACES or _is_noun_like(tokens[j], particles)
            ):
                j += 1
            if j > i and j < n and not tokens[j].is_punctuation and tokens[j].lemma in _SURU_LEMMAS:
                is_suru_compound = True
                suru_end = j  # tokens[j] is する/しない/した

        if is_suru_compound or _is_verb_or_adj(tok, particles):
            start = i
            if is_suru_compound:
                head_tokens = tokens[i : suru_end + 1]
                j = suru_end + 1
            else:
                head_tokens = [tok]
                j = i + 1

            # 合併連用副詞形（〜く）+ (は/も)? + 補助述語（ない/なる/なってしまう）
            last_t = head_tokens[-1]
            if last_t.lookup and any("副詞化" in r or "連用形" in r for r in last_t.lookup.reasons):
                k = j
                if k < n and tokens[k].surface in ("は", "も"):
                    k += 1
                if k < n and not tokens[k].is_punctuation and tokens[k].lemma in _AUX_FOLLOW_LEMMAS:
                    head_tokens.extend(tokens[j : k + 1])
                    j = k + 1

            # 合併 連用形動詞 + て/で（+ 補助動詞，或留給ても讓步判斷）
            trailing_conj = ""
            if j < n and tokens[j].surface in ("て", "で"):
                if j + 1 < n and tokens[j + 1].surface == "も":
                    head_tokens.extend(tokens[j : j + 2])
                    trailing_conj = "ても"
                    j += 2
                elif j + 1 < n and not tokens[j + 1].is_punctuation and tokens[j + 1].lemma in _AUX_FOLLOW_LEMMAS:
                    head_tokens.extend(tokens[j : j + 2])
                    j += 2
                else:
                    head_tokens.append(tokens[j])
                    j += 1

            # 合併 動詞 + 一方 + (だ/です)
            if j < n and tokens[j].surface == "一方":
                head_tokens.append(tokens[j])
                j += 1
                if j < n and tokens[j].surface in ("だ", "です"):
                    head_tokens.append(tokens[j])
                    j += 1

            # 檢查述語後是否接續子句連接詞（ても/のに/けど/ので/から/ば/たら/なら/と/ながら）
            if not trailing_conj and j < n and not tokens[j].is_punctuation:
                nxt = tokens[j].surface
                if nxt in _CLAUSE_CONJ_ROLES:
                    # 只有當後面還有其他內容（不是句末單純結尾）才視為前件連接詞
                    has_more = any(not t.is_punctuation for t in tokens[j + 1 :])
                    if has_more or nxt in ("ても", "でも", "のに", "ので", "ながら"):
                        head_tokens.append(tokens[j])
                        trailing_conj = nxt
                        j += 1

            # 吸收句尾語氣／斷定助詞（のか / わねぇ / だ / です / よ / ね）
            if not trailing_conj:
                while j < n and not tokens[j].is_punctuation and tokens[j].surface in _FINAL_MODAL_SURFACES:
                    # 保留後面接「よう」的「の」或「か」（留給 かのように）
                    if tokens[j].surface == "か" and j + 2 < n and tokens[j + 1].surface == "の" and tokens[j + 2].surface == "よう":
                        break
                    if tokens[j].surface == "の" and j + 1 < n and tokens[j + 1].surface == "よう":
                        break
                    # 如果後面還有實詞（不是逗號/結尾/另一個語氣詞），停止吸收「の」「か」
                    if tokens[j].surface in ("の", "か") and j + 1 < n and not tokens[j + 1].is_punctuation:
                        if tokens[j + 1].surface not in _FINAL_MODAL_SURFACES:
                            break
                    head_tokens.append(tokens[j])
                    j += 1

            text = "".join(t.surface for t in head_tokens)
            # 組合精煉中文意思（複合述語取最精簡首義，避免重複堆疊同義詞）
            content_glosses = [
                _first_short_gloss(t, max_len=10).split("、")[0]
                for t in head_tokens
                if t.surface not in _FINAL_MODAL_SURFACES
                and t.surface not in ("て", "で", "も", "は", "と", "一方")
                and t.lemma not in _SURU_LEMMAS
            ]
            content_glosses = [g for g in content_glosses if g]
            if not content_glosses:
                content_glosses = [_first_short_gloss(head_tokens[0], max_len=12).split("、")[0]]
            gloss = content_glosses[0] if content_glosses else ""
            if is_suru_compound and len(content_glosses) >= 2:
                stem_gl = "".join(content_glosses[:2]) if len(content_glosses[0]) <= 3 else content_glosses[1]
                if "再" in head_tokens[0].surface:
                    stem_gl = "重新" + (content_glosses[1] if len(content_glosses) > 1 else "")
                gloss = stem_gl
                if any(t.lookup and any("否定" in r for r in t.lookup.reasons) for t in head_tokens):
                    gloss = f"不{gloss}"
            elif any(t.lemma in ("ない", "無い") for t in head_tokens[1:]) and gloss and not gloss.startswith("不"):
                gloss = f"不{gloss}"
            if any(t.surface == "一方" for t in head_tokens) and gloss:
                gloss = f"越來越{gloss.lstrip('变得變得')}" if "越" not in gloss else gloss
            if any(t.lemma == "なる" and t.lookup and any("てしまう" in r for r in t.lookup.reasons) for t in head_tokens):
                if gloss:
                    gloss = f"變成{gloss}了"
            if any(t.surface in ("か", "のか") for t in head_tokens[-2:]) and gloss and not gloss.endswith("嗎"):
                gloss = f"{gloss}嗎"

            # 判斷角色：前件 / 修飾述語（後面緊接 という、かのように、或名詞） / 述語
            if trailing_conj:
                role_key, role_label = _CLAUSE_CONJ_ROLES[trailing_conj]
                if trailing_conj in ("ても", "でも") and gloss and not gloss.startswith("即使"):
                    gloss = f"即使{gloss}"
                elif trailing_conj == "と" and any(
                    t.lookup and any("否定" in r for r in t.lookup.reasons) for t in head_tokens
                ):
                    gloss = f"如果{gloss}"
                    trailing_conj = "ないと"
            else:
                # 檢查後面是否緊接 という / かのように / 名詞（表示此述語是修飾子句）
                nxt_real = next((tokens[k] for k in range(j, n) if not tokens[k].is_punctuation), None)
                is_followed_by_comma = j < n and tokens[j].is_punctuation
                if (
                    not is_followed_by_comma
                    and nxt_real is not None
                    and (
                        nxt_real.surface in ("という", "っていう", "といった")
                        or (
                            nxt_real.surface == "か"
                            and j + 2 < n
                            and tokens[j + 1].surface == "の"
                            and tokens[j + 2].surface == "よう"
                        )
                        or _is_noun_like(nxt_real, particles)
                    )
                ):
                    role_key, role_label = "clause_mod", "修飾述語"
                else:
                    role_key, role_label = "predicate", "核心結論"

            chunks.append(
                ClauseChunk(
                    text=text,
                    role_key=role_key,
                    role_label=role_label,
                    gloss=gloss[:18] + "…" if len(gloss) > 18 else gloss,
                    particle=trailing_conj,
                    clause_index=clause_idx,
                )
            )
            i = j
            continue

        # 4) 名詞語塊（含指示詞 / 複合名詞 / N1 の N2 + 格助詞）
        start = i
        j = i
        no_count = 0
        noun_tokens: list[Token] = []
        while j < n and not tokens[j].is_punctuation:
            cur_t = tokens[j]
            if cur_t.surface in _DEMONSTRATIVES or cur_t.surface in _PREFIX_SURFACES or _is_noun_like(cur_t, particles):
                noun_tokens.append(cur_t)
                j += 1
                continue
            # 允許 N1 の N2 合併成單一清晰名詞組（最多串接 2 個「の」）
            if (
                cur_t.surface == "の"
                and noun_tokens
                and no_count < 2
                and j + 1 < n
                and not tokens[j + 1].is_punctuation
                and tokens[j + 1].surface != "よう"
                and (tokens[j + 1].surface in _DEMONSTRATIVES or _is_noun_like(tokens[j + 1], particles))
            ):
                noun_tokens.append(cur_t)
                no_count += 1
                j += 1
                continue
            break

        if noun_tokens:
            particle_surf = ""
            # 吸收後隨格助詞／提示助詞
            if j < n and not tokens[j].is_punctuation and tokens[j].surface in _CASE_PARTICLE_ROLES:
                particle_surf = tokens[j].surface
                noun_tokens.append(tokens[j])
                j += 1
                # 若緊接句尾語氣詞（如 私自身もね）也一併收入
                while j < n and not tokens[j].is_punctuation and tokens[j].surface in ("ね", "ねぇ", "よ", "わ", "さ"):
                    noun_tokens.append(tokens[j])
                    j += 1
            elif j < n and not tokens[j].is_punctuation and tokens[j].surface in ("だ", "です"):
                # 名詞述語（如 ここは学校だ）
                noun_tokens.append(tokens[j])
                j += 1
                while j < n and not tokens[j].is_punctuation and tokens[j].surface in _FINAL_MODAL_SURFACES:
                    noun_tokens.append(tokens[j])
                    j += 1
                text = "".join(t.surface for t in noun_tokens)
                gl = _first_short_gloss(noun_tokens[0], max_len=14)
                chunks.append(ClauseChunk(text, "predicate", "核心結論", f"是{gl}" if gl else "", clause_index=clause_idx))
                i = j
                continue

            text = "".join(t.surface for t in noun_tokens)
            # 組合名詞組精簡釋義（如 外の世界 -> 外面的世界）
            content_toks = [
                t for t in noun_tokens
                if t.surface not in _CASE_PARTICLE_ROLES and t.surface not in _FINAL_MODAL_SURFACES
            ]
            if no_count >= 1 and len(content_toks) >= 2:
                g_left = _first_short_gloss(content_toks[0], max_len=6).split("、")[0]
                g_right = "".join(
                    _first_short_gloss(t, max_len=6).split("、")[0] for t in content_toks[1:3]
                )
                gloss = f"{g_left}的{g_right}" if (g_left and g_right) else (g_right or g_left)
            elif len(content_toks) >= 2:
                parts_g = [_first_short_gloss(t, max_len=6).split("、")[0] for t in content_toks[:2]]
                parts_g = [g for g in parts_g if g]
                gloss = "".join(parts_g) if len("".join(parts_g)) <= 12 else (parts_g[-1] if parts_g else "")
            else:
                gloss = _first_short_gloss(content_toks[0], max_len=14) if content_toks else ""

            if particle_surf:
                role_key, role_label = _CASE_PARTICLE_ROLES[particle_surf]
                if particle_surf == "に" and any(t.surface in _TIME_WORDS for t in content_toks):
                    role_key, role_label = "time_place", "時間"
            else:
                if any(t.surface in _TIME_WORDS for t in content_toks):
                    role_key, role_label = "time_place", "時間"
                elif j < n and tokens[j].is_punctuation and any(c in _COMMA_CHARS for c in tokens[j].surface):
                    role_key, role_label = "bare_topic", "主題/稱呼"
                else:
                    role_key, role_label = "noun", "名詞"

            chunks.append(
                ClauseChunk(
                    text=text,
                    role_key=role_key,
                    role_label=role_label,
                    gloss=gloss[:18] + "…" if len(gloss) > 18 else gloss,
                    particle=particle_surf,
                    clause_index=clause_idx,
                )
            )
            i = j
            continue

        # 5) 其餘孤立助詞／接續詞兜底
        p_info = particles.get(tok.surface)
        chunks.append(
            ClauseChunk(
                text=tok.surface,
                role_key="other",
                role_label=p_info.role if p_info else "語氣/成分",
                gloss=_first_short_gloss(tok, max_len=12),
                particle=tok.surface if p_info else "",
                clause_index=clause_idx,
            )
        )
        i += 1

    # 細化「主題/稱呼」：若同子句內已有明確「主題（は）」，逗號前單名詞屬稱呼；否則屬省略「は」的口語主題
    has_explicit_topic = any(c.role_key == "topic" for c in chunks)
    refined: list[ClauseChunk] = []
    for idx, ch in enumerate(chunks):
        if ch.role_key == "bare_topic":
            if has_explicit_topic:
                refined.append(ClauseChunk(ch.text, "vocative", "稱呼", ch.gloss, ch.particle, ch.clause_index))
            else:
                refined.append(ClauseChunk(ch.text, "topic", "主題（省は）", ch.gloss, ch.particle, ch.clause_index))
        else:
            refined.append(ch)

    # 若子句有多個 predicate，只把最後一個標為「核心結論」，其餘標為「分句述語」
    pred_indices = [idx for idx, c in enumerate(refined) if c.role_key == "predicate"]
    if len(pred_indices) > 1:
        last_p = pred_indices[-1]
        for idx in pred_indices[:-1]:
            c = refined[idx]
            refined[idx] = ClauseChunk(c.text, "clause_mod", "分句述語", c.gloss, c.particle, c.clause_index)
        c_last = refined[last_p]
        refined[last_p] = ClauseChunk(c_last.text, "predicate", "核心結論", c_last.gloss, c_last.particle, c_last.clause_index)

    return refined


def _analyze_sentence_logic(
    tokenized: TokenizedSentence,
    particles: dict[str, ParticleInfo],
) -> tuple[list[ClauseChunk], str, str, list[SyntaxRelation], str]:
    """離線解析整句主幹、分塊結構與「誰修飾誰」的邏輯箭頭鏈。"""
    token_clauses = _split_token_sentences(tokenized.tokens)
    if not token_clauses:
        return [], "", "", [], ""

    # 選出實詞最多的子句作為主句分析主幹；其餘極短子句標示為補充
    primary_idx = max(range(len(token_clauses)), key=lambda idx: len([t for t in token_clauses[idx] if not t.is_punctuation]))
    all_chunks: list[ClauseChunk] = []
    primary_chunks: list[ClauseChunk] = []

    for c_idx, c_toks in enumerate(token_clauses):
        c_chunks = _chunk_single_clause(c_toks, c_idx, particles)
        if c_idx == primary_idx:
            primary_chunks = c_chunks
            all_chunks.extend(c_chunks)
        else:
            # 短補充子句合併為 1 塊清楚標註
            txt = "".join(t.surface for t in c_toks if not t.is_punctuation)
            if txt:
                gl = " ".join(ch.gloss for ch in c_chunks if ch.gloss)[:16]
                all_chunks.append(ClauseChunk(txt, "supplement", "補充句", gl, clause_index=c_idx))

    if not primary_chunks:
        return all_chunks, "", "", [], ""

    # 尋找主句核心結論（最後一個 predicate；若無則取最後一塊）
    main_pred_idx = next(
        (idx for idx in range(len(primary_chunks) - 1, -1, -1) if primary_chunks[idx].role_key == "predicate"),
        len(primary_chunks) - 1,
    )
    main_pred = primary_chunks[main_pred_idx]

    # 識別修飾語與內嵌子句邊界，建構「誰修飾誰」邏輯箭頭（保留來源位置以便自然由左到右排序）
    indexed_relations: list[tuple[int, SyntaxRelation]] = []
    embedded_indices: set[int] = set()

    # A) 處理 `という`（內容修飾：左邊子句 ──內容修飾(という)──▶ 右邊名詞）
    for idx, ch in enumerate(primary_chunks):
        if ch.role_key == "content_link" and idx > 0 and idx + 1 < len(primary_chunks):
            left_start = idx - 1
            while left_start > 0 and primary_chunks[left_start - 1].role_key not in (
                "topic", "concession", "condition", "reason", "vocative"
            ):
                left_start -= 1
            mod_span = "".join(c.text for c in primary_chunks[left_start:idx])
            target_chunk = primary_chunks[idx + 1]
            inner_pred = next(
                (primary_chunks[k] for k in range(idx - 1, left_start - 1, -1) if primary_chunks[k].role_key in ("clause_mod", "predicate")),
                None,
            )
            if inner_pred is not None:
                for k in range(left_start, idx):
                    ck = primary_chunks[k]
                    if ck is not inner_pred and ck.role_key in ("object", "place", "target", "adverb"):
                        rel_name = {
                            "object": "動作受詞（を）",
                            "place": "場所/手段（で）",
                            "target": "動作對象（に）",
                            "adverb": "程度修飾",
                        }[ck.role_key]
                        indexed_relations.append((k, SyntaxRelation(ck.text, rel_name, inner_pred.text)))
            indexed_relations.append(
                (
                    idx,
                    SyntaxRelation(
                        mod_span,
                        f"內容修飾（{ch.text}）",
                        target_chunk.text,
                        f"具體說明「{target_chunk.text.rstrip('はがをにで')}」的內容",
                    ),
                )
            )
            embedded_indices.update(range(left_start, idx + 1))

    # B) 處理 `かのように` / `のように`（比喻修飾：左邊比喻子句 ──比喻方式──▶ 後續述語）
    for idx, ch in enumerate(primary_chunks):
        if ch.role_key == "simile" and idx > 0:
            left_start = idx - 1
            while left_start > 0 and primary_chunks[left_start - 1].role_key not in (
                "concession", "condition", "reason", "topic", "vocative"
            ):
                left_start -= 1
            simile_span = "".join(c.text for c in primary_chunks[left_start:idx])
            next_pred = next(
                (primary_chunks[k] for k in range(idx + 1, len(primary_chunks)) if primary_chunks[k].role_key in ("predicate", "clause_mod", "concession", "condition")),
                main_pred,
            )
            indexed_relations.append(
                (
                    idx,
                    SyntaxRelation(
                        simile_span,
                        f"比喻方式（{ch.text}）",
                        next_pred.text,
                        "形容後面動作的樣子／狀態",
                    ),
                )
            )
            embedded_indices.update(range(left_start, idx + 1))

    # C) 處理各個一般修飾語／受詞／地點／前件 -> 指向最近的下一個述語（或核心結論）
    for idx, ch in enumerate(primary_chunks):
        if idx in embedded_indices or idx == main_pred_idx:
            continue
        if ch.role_key in ("object", "place", "target", "adverb", "time_place"):
            nxt_verb = next(
                (
                    primary_chunks[k]
                    for k in range(idx + 1, len(primary_chunks))
                    if primary_chunks[k].role_key in ("predicate", "clause_mod", "concession", "condition", "reason")
                ),
                main_pred,
            )
            if nxt_verb is not ch:
                rel_map = {
                    "object": "動作受詞（を）",
                    "place": "場所/手段（で）",
                    "target": f"對象/方向（{ch.particle or 'に'}）",
                    "adverb": "程度修飾",
                    "time_place": "時間/範圍",
                }
                indexed_relations.append((idx, SyntaxRelation(ch.text, rel_map[ch.role_key], nxt_verb.text)))
        elif ch.role_key in ("concession", "condition", "reason"):
            rel_label_map = {
                "concession": f"讓步／轉折（〜{ch.particle}）" if ch.particle else "讓步／轉折",
                "condition": f"條件前件（〜{ch.particle}）" if ch.particle else "條件前件",
                "reason": f"原因前件（〜{ch.particle}）" if ch.particle else "原因前件",
            }
            cond_exp = (
                "不滿足此條件就會觸發後面結果"
                if ch.particle == "ないと"
                else "若滿足此條件，就會觸發後面結果"
            )
            exp_map = {
                "concession": "即使前件成立，後面結論依然如此",
                "condition": cond_exp,
                "reason": "前半為原因，導出後半結果",
            }
            indexed_relations.append(
                (idx, SyntaxRelation(ch.text, rel_label_map[ch.role_key], main_pred.text, exp_map[ch.role_key]))
            )

    # D) 尋找主句頂層「主題 / 主語 / 條件讓步前提」構成「句子主幹」
    top_topic = next(
        (c for idx, c in enumerate(primary_chunks) if idx not in embedded_indices and c.role_key == "topic"),
        None,
    )
    top_subject = next(
        (c for idx, c in enumerate(primary_chunks) if idx not in embedded_indices and c.role_key == "subject"),
        None,
    )
    top_premise = next(
        (c for idx, c in enumerate(primary_chunks) if idx not in embedded_indices and c.role_key in ("concession", "condition", "reason")),
        None,
    )

    if top_topic is not None and top_topic is not main_pred:
        skeleton_topic = f"{top_topic.text}（{top_topic.gloss}）" if top_topic.gloss else top_topic.text
        indexed_relations.append(
            (len(primary_chunks) + 1, SyntaxRelation(top_topic.text, f"全句主題（{top_topic.particle or '省は'}）", main_pred.text))
        )
    elif top_subject is not None and top_subject is not main_pred:
        skeleton_topic = f"{top_subject.text}（{top_subject.gloss}）" if top_subject.gloss else top_subject.text
        indexed_relations.append(
            (len(primary_chunks) + 1, SyntaxRelation(top_subject.text, "全句主語（が）", main_pred.text))
        )
    elif top_premise is not None and top_premise is not main_pred:
        skeleton_topic = f"{top_premise.text}（{top_premise.gloss}）" if top_premise.gloss else top_premise.text
    else:
        skeleton_topic = "（省略主語）"

    skeleton_predicate = (
        f"{main_pred.text}（{main_pred.gloss}）" if main_pred.gloss else main_pred.text
    )

    # 生成 1 句精準的閱讀語序提示
    if top_premise is not None and top_topic is None:
        if top_premise.role_key == "concession":
            hint = f"讓步結構：先看前半「{top_premise.text}」鋪設前提，核心結論在句尾「{main_pred.text}」。"
        elif top_premise.role_key == "condition":
            hint = f"條件結構：前半「{top_premise.text}」是條件，句尾「{main_pred.text}」是隨之發生的結果。"
        else:
            hint = f"因果結構：前半「{top_premise.text}」交代原因，句尾「{main_pred.text}」是結論。"
    elif top_topic is not None and embedded_indices:
        hint = f"日語修飾在左、結論在右：先抓主幹「{top_topic.text} → {main_pred.text}」，再往左補上修飾內容。"
    elif skeleton_topic == "（省略主語）":
        hint = f"日語口語常省略主語（你／我），直接抓句尾核心述語「{main_pred.text}」與前方修飾語即可。"
    elif len(primary_chunks) <= 2:
        t_head = top_topic.text if top_topic else (top_subject.text if top_subject else "")
        hint = f"簡潔主述句：「{t_head}」是談論對象，句尾「{main_pred.text}」直接給出結論。"
    else:
        t_head = top_topic.text if top_topic else (top_subject.text if top_subject else "")
        hint = f"日語結論在句尾：先抓主幹「{t_head} → {main_pred.text}」，中間都是動作的對象與修飾。"

    # 依語序由左至右排序，主幹結論置於最後，去重並保留最多 5 條
    indexed_relations.sort(key=lambda pair: pair[0])
    dedup_rels: list[SyntaxRelation] = []
    seen_rel_keys: set[tuple[str, str, str]] = set()
    for _, r in indexed_relations:
        k = (r.source, r.relation, r.target)
        if k not in seen_rel_keys and r.source != r.target:
            seen_rel_keys.add(k)
            dedup_rels.append(r)

    return all_chunks, skeleton_topic, skeleton_predicate, dedup_rels[:5], hint


class OfflineGrammarEngine:
    """離線 N5→N1 句型匹配與助詞解說引擎。"""

    def __init__(self) -> None:
        data_path = Path(__file__).resolve().parent.parent / "data" / "grammar_blueprint.json"
        raw: dict[str, Any] = json.loads(data_path.read_text(encoding="utf-8"))

        self.domains: list[str] = list(raw.get("domains", []))
        self.particles: dict[str, ParticleInfo] = {}
        for item in raw.get("particles", []):
            p = ParticleInfo(
                surface=item["surface"],
                level=item["level"],
                role=item["role"],
                formula=item["formula"],
                meaning=item["meaning"],
            )
            self.particles[p.surface] = p

        self.patterns: list[dict[str, Any]] = []
        self._compiled: list[tuple[re.Pattern[str], dict[str, Any]]] = []
        for pat in raw.get("patterns", []):
            self.patterns.append(pat)
            self._compiled.append((re.compile(pat["regex"]), pat))
        if not self.domains:
            self.domains = list(dict.fromkeys(p.get("category", "") for p in self.patterns if p.get("category")))

    def get_particle_info(self, surface: str) -> ParticleInfo | None:
        return self.particles.get(surface)

    def get_pattern_by_id(self, pattern_id: str) -> dict[str, Any] | None:
        for pat in self.patterns:
            if pat["id"] == pattern_id:
                return pat
        return None

    def analyze(self, tokenized: TokenizedSentence) -> SentenceGrammarAnalysis:
        sentence = tokenized.raw
        hits: list[GrammarHit] = []
        seen_ids: set[str] = set()

        token_lessons = [lesson_for_token(tok) for tok in tokenized.tokens]
        # 「本を読んだ／飲んだ／遊んだ」的「んだ」屬於動詞過去式；「読んだんだ」才在後面加了解釋語氣。
        past_endings: set[int] = set()
        for tok, lesson in zip(tokenized.tokens, token_lessons):
            if not tok.surface.endswith("んだ"):
                continue
            if lesson is not None and lesson.form in ("tenda", "tetanda", "terunda"):
                continue
            if tok.lookup and any("解釋" in r or "追問" in r or "のだ" in r for r in tok.lookup.reasons):
                continue
            is_ta_past = (
                (lesson is not None and lesson.form == "ta")
                or (
                    tok.lemma.endswith(("む", "ぶ", "ぬ"))
                    and tok.surface != tok.lemma
                    and not tok.surface.endswith("んだんだ")
                )
            )
            if is_ta_past:
                past_endings.add(tok.end - 2)

        def _token_at(pos: int) -> Token | None:
            for tk in tokenized.tokens:
                if tk.start <= pos < tk.end:
                    return tk
            return None

        for regex, pat in self._compiled:
            pid = pat["id"]
            if pid in seen_ids:
                continue
            matched_text: str | None = None

            if pid == "n4_potential":
                # 必須由動詞活用分析或可能形詞尾確認，絕不把一般一段動詞（食べる／見せる／忘れる）誤判成可能形
                for tok, lesson in zip(tokenized.tokens, token_lessons):
                    if lesson is not None and lesson.form == "potential":
                        matched_text = tok.surface
                        break
                    if tok.lookup and any("可能" in r for r in tok.lookup.reasons):
                        matched_text = tok.surface
                        break
                    if (
                        tok.surface.endswith(("られる", "られた", "られない", "られなかった", "られます"))
                        and tok.surface != tok.lemma
                        and (tok.verb_type or (tok.lookup and any(
                            any(k in e.pos.lower() for k in ("v1", "v5", "vk", "一段", "五段"))
                            for e in tok.lookup.all_entries
                        )))
                    ):
                        matched_text = tok.surface
                        break
            elif pid == "n4_ba":
                # 排除「言葉／やばい／すばらしい／たとえば」等普通詞彙，只匹配真實假定形 〜ば／〜ければ
                if "ければ" in sentence:
                    m_ba = regex.search(sentence)
                    if m_ba:
                        matched_text = m_ba.group(0)
                if matched_text is None:
                    for tok, lesson in zip(tokenized.tokens, token_lessons):
                        if lesson is not None and lesson.form == "ba":
                            matched_text = tok.surface
                            break
                        if tok.lookup and any("仮定" in r or "假定" in r or "〜ば" in r for r in tok.lookup.reasons):
                            matched_text = tok.surface
                            break
                        if tok.surface == "ば" and tok.is_function_word:
                            matched_text = "ば"
                            break
            elif pid == "n5_noda":
                m = next(
                    (hit for hit in regex.finditer(sentence) if hit.start() not in past_endings),
                    None,
                )
                if m:
                    matched_text = m.group(0)
            elif pid == "n4_nara":
                for hit in regex.finditer(sentence):
                    tk = _token_at(hit.start())
                    if tk is not None and tk.lemma in ("なる", "成る", "並ぶ", "必ず", "さよなら", "さようなら", "習う"):
                        continue
                    before_nara = sentence[: hit.start()]
                    if before_nara.endswith(("ければ", "ては", "では", "なきゃ")):
                        continue
                    matched_text = hit.group(0)
                    break
            elif pid == "n4_temo":
                for hit in regex.finditer(sentence):
                    tk = _token_at(hit.start())
                    if tk is not None and tk.surface in ("とても", "いつでも", "何でも", "なんでも", "誰でも", "だれでも", "どこでも"):
                        continue
                    if hit.group(0).startswith("でも"):
                        before_t = sentence[: hit.start()].rstrip()
                        if not before_t or before_t[-1] in "。、！？!?「『（(":
                            continue
                    matched_text = hit.group(0)
                    break
            elif pid == "n4_teoku":
                m = regex.search(sentence)
                if m:
                    seg = m.group(0)
                    if seg.startswith(("てお", "でお")):
                        matched_text = seg
                    else:
                        # 縮約形「とく／どく／といて」須與動詞活用吻合，避免誤判「届く／解く／説く」
                        for tok, lesson in zip(tokenized.tokens, token_lessons):
                            if lesson is not None and lesson.form in ("toku", "toite", "toita"):
                                matched_text = tok.surface
                                break
                            if tok.lookup and any("ておく" in r or "預先" in r for r in tok.lookup.reasons):
                                matched_text = tok.surface
                                break
            else:
                m = regex.search(sentence)
                if m:
                    matched_text = m.group(0)

            if matched_text:
                seen_ids.add(pid)
                hits.append(
                    GrammarHit(
                        pattern_id=pid,
                        level=pat["level"],
                        category=pat["category"],
                        name=pat["name"],
                        formula=pat["formula"],
                        meaning=pat["meaning"],
                        example=pat["example"],
                        matched_text=matched_text,
                    )
                )

        # 依難度由淺到深排列（N5 -> N1 -> 口語），若已命中 n5_teiru 則不重複顯示同片語 col_teru_teta
        if "n5_teiru" in seen_ids and "col_teru_teta" in seen_ids:
            hits = [h for h in hits if h.pattern_id != "col_teru_teta"]
        level_rank = {"N5": 1, "N4": 2, "N3": 3, "N2": 4, "N1": 5, "口語": 6}
        hits.sort(key=lambda h: level_rank.get(h.level, 9))

        # 擷取句中出現的核心助詞（不重複）
        particle_roles: list[ParticleInfo] = []
        seen_p: set[str] = set()
        for tok in tokenized.tokens:
            p = self.particles.get(tok.surface)
            if p and p.surface not in seen_p:
                seen_p.add(p.surface)
                particle_roles.append(p)

        # 擷取句中有實際活用變形的詞（讓學習者看懂怎麼從原型變過來的）
        conj_notes: list[str] = []
        seen_c: set[str] = set()
        lessons: list[VerbLesson] = []
        for tok, lesson in zip(tokenized.tokens, token_lessons):
            if lesson:
                if lesson not in lessons:
                    lessons.append(lesson)
                continue
            if tok.lookup and tok.lookup.reasons and tok.surface != tok.lookup.lemma:
                key = f"{tok.surface}->{tok.lookup.lemma}"
                if key in seen_c:
                    continue
                seen_c.add(key)
                steps = " → ".join(tok.lookup.reasons)
                pos_txt = ""
                if tok.lookup.all_entries:
                    pos_txt = explain_pos_friendly(tok.lookup.all_entries[0].pos)
                pos_part = f"〔{pos_txt}〕" if pos_txt else ""
                conj_notes.append(f"「{tok.surface}」← 原形「{tok.lookup.lemma}」{pos_part}（{steps}）")

        # 組裝逐詞日語語序直譯骨架（優先套用語境活用意涵，離線秒開！）
        word_glosses: list[tuple[str, str, str]] = []
        for tok in tokenized.tokens:
            if tok.is_punctuation:
                continue
            p_info = self.particles.get(tok.surface)
            if p_info is not None:
                word_glosses.append((tok.surface, p_info.role, ""))
            elif tok.lookup is not None:
                base_m = tok.lookup.best_concise_meaning("bilingual")
                ctx_m = format_contextual_meaning(tok.surface, tok.lemma, base_m, tok.lookup.reasons)
                # 語序骨架條保持精短（取括號前的語境核心意）
                short_m = ctx_m.split("（原形")[0].strip() if "（原形" in ctx_m else ctx_m
                if len(short_m) > 18:
                    short_m = short_m[:18] + "…"
                tag = "→".join(tok.lookup.reasons[:2]) if tok.lookup.reasons else ""
                word_glosses.append((tok.surface, tag, short_m))
            elif len(tok.surface) >= 2:
                word_glosses.append((tok.surface, "", ""))

        chunks, sk_topic, sk_pred, logic_rels, order_hint = _analyze_sentence_logic(
            tokenized, self.particles
        )

        return SentenceGrammarAnalysis(
            sentence=sentence,
            grammar_hits=hits,
            particle_roles=particle_roles,
            conjugation_notes=conj_notes,
            verb_lessons=lessons,
            word_glosses=word_glosses,
            chunks=chunks,
            skeleton_topic=sk_topic,
            skeleton_predicate=sk_pred,
            logic_relations=logic_rels,
            reading_order_hint=order_hint,
        )
