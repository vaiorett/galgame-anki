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


@dataclass
class SentenceGrammarAnalysis:
    sentence: str
    grammar_hits: list[GrammarHit] = field(default_factory=list)
    particle_roles: list[ParticleInfo] = field(default_factory=list)
    conjugation_notes: list[str] = field(default_factory=list)
    word_glosses: list[tuple[str, str, str]] = field(default_factory=list)  # (surface, tag, concise_meaning)

    def to_compact_html(self, accent_color: str = "#ec4899", muted_color: str = "#8e7a9c") -> str:
        """輸出清晰的結構化 HTML，幫助學習者先理解整句邏輯骨架，絕非生硬詞典堆疊。"""
        blocks: list[str] = []

        if self.word_glosses:
            chips: list[str] = []
            for surf, tag, meaning in self.word_glosses[:14]:
                m_part = f"：{html.escape(meaning)}" if meaning else ""
                tag_part = f'<span style="font-size:11px; color:{muted_color};">[{html.escape(tag)}]</span>' if tag else ""
                chips.append(
                    f'<span style="display:inline-block; margin:2px 4px 2px 0; padding:2px 7px; '
                    f'background:rgba(127,127,127,0.09); border-radius:6px; font-size:12.5px;">'
                    f'<b>{html.escape(surf)}</b>{tag_part}{m_part}</span>'
                )
            blocks.append(f'<div style="font-weight:700; color:{accent_color}; margin-bottom:4px;">語序骨架</div>')
            blocks.append(f'<div style="margin-bottom:9px; line-height:1.9;">{"".join(chips)}</div>')

        if self.grammar_hits:
            blocks.append(f'<div style="font-weight:700; color:{accent_color}; margin-bottom:6px;">句型拆解</div>')
            for hit in self.grammar_hits:
                blocks.append(
                    f'<div style="margin-bottom:8px; padding:7px 10px; border-left:3px solid {accent_color}; '
                    f'background:rgba(127,127,127,0.07); border-radius:6px;">'
                    f'<div><b>[{html.escape(hit.level)}] {html.escape(hit.name)}</b> '
                    f'<span style="font-size:11px; color:{muted_color};">{html.escape(hit.category)} · 「{html.escape(hit.matched_text)}」</span></div>'
                    f'<div style="font-size:12px; color:{muted_color}; margin:2px 0;">接續：{html.escape(hit.formula)}</div>'
                    f'<div style="font-size:13px; line-height:1.55;">{html.escape(hit.meaning)}</div>'
                    f'</div>'
                )

        if self.conjugation_notes:
            blocks.append(f'<div style="font-weight:700; color:{accent_color}; margin:8px 0 4px 0;">活用還原</div>')
            for note in self.conjugation_notes:
                blocks.append(f'<div style="font-size:13px; margin:2px 0;">• {html.escape(note)}</div>')

        if self.particle_roles:
            blocks.append(f'<div style="font-weight:700; color:{accent_color}; margin:8px 0 4px 0;">助詞功能</div>')
            for p in self.particle_roles[:6]:
                blocks.append(
                    f'<div style="font-size:13px; margin:3px 0;">'
                    f'<b>「{html.escape(p.surface)}」</b>'
                    f'<span style="color:{muted_color};">（{html.escape(p.role)}）</span>：{html.escape(p.meaning)}'
                    f'</div>'
                )

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
    resp = httpx.get(
        "https://translate.googleapis.com/translate_a/single",
        params={"client": "gtx", "sl": "ja", "tl": target_lang, "dt": "t", "q": text},
        timeout=8.0,
    )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list) and data and isinstance(data[0], list):
        parts = [str(seg[0]) for seg in data[0] if isinstance(seg, list) and seg and seg[0]]
        return "".join(parts).strip()
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

        for regex, pat in self._compiled:
            m = regex.search(sentence)
            if m and pat["id"] not in seen_ids:
                seen_ids.add(pat["id"])
                hits.append(
                    GrammarHit(
                        pattern_id=pat["id"],
                        level=pat["level"],
                        category=pat["category"],
                        name=pat["name"],
                        formula=pat["formula"],
                        meaning=pat["meaning"],
                        example=pat["example"],
                        matched_text=m.group(0),
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
        for tok in tokenized.tokens:
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

        return SentenceGrammarAnalysis(
            sentence=sentence,
            grammar_hits=hits,
            particle_roles=particle_roles,
            conjugation_notes=conj_notes,
            word_glosses=word_glosses,
        )
