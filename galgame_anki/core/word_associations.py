"""🌳 日語「詞彙聯想記憶樹」離線引擎（借鑑 CETPath MemoryTree 三枝串聯架構，專為日語深度設計）。

完全基於本機離線詞典 SQLite（wty-ja-zh / JPDBv2 詞頻 / Kanjium 音調）＋ 已解包遊戲劇本庫，零付費 API：
1. 🌿 同漢字詞族（Kanji Family）：以核心漢字為詞根，優先列出 Anki 已知熟詞「以熟帶生」，再列高頻同字詞。
2. 🔗 常用搭配與複合詞（Compounds & Recurrence）：撈出高頻複合詞／慣用片語，並附本作品劇本（如 ATRI）其他實戰出現處。
3. ⚡ 自他動詞配對 & 同音異義易混詞（Confusables）：內建高頻自他動詞對照表＋本機同讀音異字詞（附音調核區別）。
"""

from __future__ import annotations

import html
import json
import re
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from galgame_anki.core.dictionary import DictionaryEngine
from galgame_anki.ui.theme import get_palette

if TYPE_CHECKING:
    from galgame_anki.core.voice_pack import VoicePackManager

_KANJI_RE = re.compile(r"[\u4e00-\u9fff]")

# 精選高頻日語自他動詞成對表：(自動詞, 他動詞)
TRANSITIVITY_PAIRS: tuple[tuple[str, str], ...] = (
    ("分かる", "分ける"),
    ("開く", "開ける"),
    ("閉まる", "閉める"),
    ("決まる", "決める"),
    ("見つかる", "見つける"),
    ("始まる", "始める"),
    ("終わる", "終える"),
    ("変わる", "変える"),
    ("落ちる", "落とす"),
    ("消える", "消す"),
    ("残る", "残す"),
    ("増える", "増やす"),
    ("減る", "減らす"),
    ("集まる", "集める"),
    ("並ぶ", "並べる"),
    ("直る", "直す"),
    ("治る", "治す"),
    ("戻る", "戻す"),
    ("届く", "届ける"),
    ("進む", "進める"),
    ("続く", "続ける"),
    ("伝わる", "伝える"),
    ("助かる", "助ける"),
    ("育つ", "育てる"),
    ("慣れる", "慣らす"),
    ("生まれる", "生む"),
    ("出る", "出す"),
    ("入る", "入れる"),
    ("乗る", "乗せる"),
    ("降りる", "降ろす"),
    ("起きる", "起こす"),
    ("寝る", "寝かせる"),
    ("着く", "着ける"),
    ("付く", "付ける"),
    ("外れる", "外す"),
    ("壊れる", "壊す"),
    ("割れる", "割る"),
    ("折れる", "折る"),
    ("破れる", "破る"),
    ("汚れる", "汚す"),
    ("濡れる", "濡らす"),
    ("乾く", "乾かす"),
    ("冷める", "冷ます"),
    ("冷える", "冷やす"),
    ("温まる", "温める"),
    ("沸く", "沸かす"),
    ("焼ける", "焼く"),
    ("煮える", "煮る"),
    ("溶ける", "溶かす"),
    ("燃える", "燃やす"),
    ("見える", "見る"),
    ("聞こえる", "聞く"),
    ("止まる", "止める"),
    ("動く", "動かす"),
    ("回る", "回す"),
    ("曲がる", "曲げる"),
    ("倒れる", "倒す"),
    ("立つ", "立てる"),
    ("掛かる", "掛ける"),
    ("逃げる", "逃がす"),
    ("通る", "通す"),
    ("渡る", "渡す"),
    ("流れる", "流す"),
    ("浮かぶ", "浮かべる"),
    ("沈む", "沈める"),
    ("揺れる", "揺らす"),
    ("鳴る", "鳴らす"),
    ("散る", "散らす"),
    ("積もる", "積む"),
    ("隠れる", "隠す"),
    ("現れる", "現す"),
    ("表れる", "表す"),
    ("足りる", "足す"),
    ("満ちる", "満たす"),
    ("過ぎる", "過ごす"),
    ("向く", "向ける"),
    ("離れる", "離す"),
    ("近づく", "近づける"),
    ("混ざる", "混ぜる"),
    ("重なる", "重ねる"),
    ("繋がる", "繋げる"),
    ("ほどける", "ほどく"),
    ("緩む", "緩める"),
    ("締まる", "締める"),
    ("広がる", "広げる"),
    ("伸びる", "伸ばす"),
    ("縮む", "縮める"),
    ("深まる", "深める"),
    ("高まる", "高める"),
    ("強まる", "強める"),
    ("弱まる", "弱める"),
    ("静まる", "静める"),
    ("定まる", "定める"),
    ("整う", "整える"),
    ("揃う", "揃える"),
    ("備わる", "備える"),
    ("加わる", "加える"),
    ("遅れる", "遅らせる"),
    ("驚く", "驚かせる"),
    ("喜ぶ", "喜ばせる"),
    ("苦しむ", "苦しめる"),
    ("楽しむ", "楽しませる"),
    ("勝つ", "負ける"),
)

_TRANSITIVITY_LOOKUP: dict[str, list[tuple[str, str]]] = defaultdict(list)
for _vi, _vt in TRANSITIVITY_PAIRS:
    if _vi == "勝つ" and _vt == "負ける":
        _TRANSITIVITY_LOOKUP[_vi].append((_vt, "反義配對"))
        _TRANSITIVITY_LOOKUP[_vt].append((_vi, "反義配對"))
    else:
        _TRANSITIVITY_LOOKUP[_vi].append((_vt, "對應他動詞"))
        _TRANSITIVITY_LOOKUP[_vt].append((_vi, "對應自動詞"))


@dataclass
class RelatedWordNode:
    term: str
    reading: str
    meaning: str
    pitch: str = ""
    frequency: int | None = None
    relation: str = ""   # kanji_family | compound | transitivity | homophone
    tag: str = ""
    is_known: bool = False


@dataclass
class GameRecurrenceLine:
    scene_file: str
    speaker: str
    text: str
    translation: str = ""


@dataclass
class WordAssociationTree:
    lemma: str
    reading: str
    kanji_roots: list[str] = field(default_factory=list)
    kanji_family: list[RelatedWordNode] = field(default_factory=list)
    compounds: list[RelatedWordNode] = field(default_factory=list)
    confusables: list[RelatedWordNode] = field(default_factory=list)
    game_recurrences: list[GameRecurrenceLine] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (
            self.kanji_family
            or self.compounds
            or self.confusables
            or self.game_recurrences
        )

    def total_nodes(self) -> int:
        return len(self.kanji_family) + len(self.compounds) + len(self.confusables)

    def to_compact_preview_text(self) -> str:
        """供精煉製卡視窗 1～2 行快速預覽此詞的聯想網絡。"""
        def _short(txt: str, limit: int = 12) -> str:
            t = (txt or "").strip()
            if len(t) <= limit:
                return t
            return t[: limit - 1].rstrip(" 、，,;") + "…"

        segs: list[str] = []
        if self.confusables:
            c_txt = "、".join(
                f"{n.term}({n.tag}:{_short(n.meaning, 12)})" if n.meaning else f"{n.term}({n.tag})"
                for n in self.confusables[:2]
            )
            segs.append(f"辨析：{c_txt}")
        if self.kanji_family:
            k_txt = "、".join(
                f"{'✓' if n.is_known else ''}{n.term}({_short(n.meaning, 12)})" if n.meaning else n.term
                for n in self.kanji_family[:3]
            )
            segs.append(f"同字：{k_txt}")
        if self.compounds:
            p_txt = "、".join(n.term for n in self.compounds[:3])
            segs.append(f"複合：{p_txt}")
        return " ｜ ".join(segs)

    def to_panel_html(self, theme_name: str = "sakura") -> str:
        """輸出主視窗「關聯詞」分頁的乾淨結構化 HTML。"""
        p = get_palette(theme_name)
        if self.is_empty():
            return (
                f'<p style="color:{p.text_muted}; margin:6px 0;">'
                "此詞條暫無關聯詞資料。</p>"
            )

        blocks: list[str] = []
        if self.kanji_roots:
            roots_str = " · ".join(f"<b>{html.escape(k)}</b>" for k in self.kanji_roots)
            blocks.append(
                f'<div style="font-size:12px; color:{p.text_muted}; margin-bottom:6px;">'
                f"漢字：{roots_str}</div>"
            )

        def _render_branch(title: str, nodes: list[RelatedWordNode]) -> None:
            if not nodes:
                return
            rows: list[str] = []
            for n in nodes:
                known_badge = (
                    f'<span style="color:#059669; font-weight:700; font-size:11px;">✓ </span>'
                    if n.is_known
                    else ""
                )
                rd = f"「{html.escape(n.reading)}」" if n.reading and n.reading != n.term else ""
                pt = f" [{html.escape(n.pitch)}]" if n.pitch else ""
                tag_html = (
                    f' <span style="color:{p.accent_lilac}; font-size:11px;">{html.escape(n.tag)}</span>'
                    if n.tag
                    else ""
                )
                freq_html = (
                    f' <span style="color:{p.text_muted}; font-size:11px;">#{n.frequency}</span>'
                    if n.frequency
                    else ""
                )
                gloss = f" — {html.escape(n.meaning)}" if n.meaning else ""
                rows.append(
                    f'<div style="margin:3px 0; line-height:1.55;">'
                    f"{known_badge}<b>{html.escape(n.term)}</b>{rd}{pt}{tag_html}{freq_html}"
                    f'<span style="color:{p.text_primary};">{gloss}</span></div>'
                )
            blocks.append(
                f'<div style="margin-bottom:8px; padding:8px 10px; background:{p.bg_input}; '
                f'border:1px solid {p.border}; border-radius:8px;">'
                f'<div style="font-size:12px; font-weight:700; color:{p.accent_pink}; margin-bottom:4px;">{title}</div>'
                f"{''.join(rows)}</div>"
            )

        _render_branch("易混辨析", self.confusables)
        _render_branch("同字詞族", self.kanji_family)
        _render_branch("複合詞", self.compounds)

        if self.game_recurrences:
            rec_rows = []
            for gr in self.game_recurrences:
                spk = f"[{html.escape(gr.speaker)}] " if gr.speaker else ""
                tr = f'<div style="font-size:12px; color:{p.text_muted};">{html.escape(gr.translation)}</div>' if gr.translation else ""
                rec_rows.append(
                    f'<div style="margin:4px 0; line-height:1.5;">'
                    f'<span style="color:{p.accent_lilac}; font-size:11px;">{html.escape(gr.scene_file)}</span> '
                    f"{spk}{html.escape(gr.text)}{tr}</div>"
                )
            blocks.append(
                f'<div style="margin-bottom:4px; padding:8px 10px; background:{p.bg_input}; '
                f'border:1px solid {p.border}; border-radius:8px;">'
                f'<div style="font-size:12px; font-weight:700; color:{p.accent_pink}; margin-bottom:4px;">'
                f"劇本例句</div>"
                f"{''.join(rec_rows)}</div>"
            )

        return "".join(blocks)

    def to_anki_html(self) -> str:
        """輸出嚴格相容 Anki 原生日間／夜間主題的精簡關聯詞 HTML。"""
        if self.is_empty():
            return ""
        sections: list[str] = []

        def _sec(title: str, nodes: list[RelatedWordNode], limit: int) -> None:
            if not nodes:
                return
            lis: list[str] = []
            for n in nodes[:limit]:
                rd = f"（{html.escape(n.reading)}）" if n.reading and n.reading != n.term else ""
                pt = f" [{html.escape(n.pitch)}]" if n.pitch else ""
                tg = f" · {html.escape(n.tag)}" if n.tag else ""
                mg = f"：{html.escape(n.meaning)}" if n.meaning else ""
                kn = "✓ " if n.is_known else ""
                lis.append(f"<li>{kn}<b>{html.escape(n.term)}</b>{rd}{pt}{tg}{mg}</li>")
            sections.append(f'<div class="rel-group"><div class="rel-title">{title}</div><ul>{"".join(lis)}</ul></div>')

        _sec("易混辨析", self.confusables, 4)
        _sec("同字詞族", self.kanji_family, 5)
        _sec("複合詞", self.compounds, 4)
        return "".join(sections)


class WordAssociationEngine:
    """離線高速日語詞彙聯想記憶樹引擎（懶載入高頻漢字倒排表，<15ms 回傳）。"""

    def __init__(
        self,
        dictionary: DictionaryEngine,
        voice_packs: "VoicePackManager | None" = None,
    ) -> None:
        self._dict = dictionary
        self._voice_packs = voice_packs
        self._lock = threading.RLock()
        self._index_ready = False
        self._kanji_index: dict[str, list[tuple[str, int]]] = defaultdict(list)
        self._top_terms: list[tuple[str, int]] = []

    def _ensure_kanji_index(self) -> None:
        with self._lock:
            if self._index_ready:
                return
            self._index_ready = True
            try:
                with self._dict._lock:
                    rows = self._dict._conn.execute(
                        """
                        SELECT m.term, MIN(CAST(json_extract(m.data_json, '$.frequency') AS INT)) AS freq
                        FROM term_meta m
                        JOIN dictionaries d ON d.id = m.dict_id
                        WHERE d.enabled = 1 AND m.mode = 'freq'
                        GROUP BY m.term
                        HAVING freq IS NOT NULL AND freq <= 35000
                        ORDER BY freq ASC
                        LIMIT 35000
                        """
                    ).fetchall()
            except Exception:
                rows = []

            if rows:
                for r in rows:
                    term = str(r["term"] or "").strip()
                    freq = int(r["freq"])
                    if len(term) < 2 or len(term) > 8:
                        continue
                    self._top_terms.append((term, freq))
                    for ch in set(_KANJI_RE.findall(term)):
                        if len(self._kanji_index[ch]) < 80:
                            self._kanji_index[ch].append((term, freq))
                return

            # 無 JPDB 詞頻表時（例如僅載入內建詞庫）從 terms 建輕量索引
            try:
                with self._dict._lock:
                    t_rows = self._dict._conn.execute(
                        """
                        SELECT DISTINCT t.term FROM terms t
                        JOIN dictionaries d ON d.id = t.dict_id
                        WHERE d.enabled = 1 AND d.kind = 'bilingual'
                        LIMIT 12000
                        """
                    ).fetchall()
            except Exception:
                t_rows = []
            for idx, r in enumerate(t_rows, start=1):
                term = str(r["term"] or "").strip()
                if len(term) < 2 or len(term) > 8:
                    continue
                self._top_terms.append((term, idx))
                for ch in set(_KANJI_RE.findall(term)):
                    if len(self._kanji_index[ch]) < 40:
                        self._kanji_index[ch].append((term, idx))

    def _summarize_term(
        self,
        term: str,
        known_words: set[str],
        relation: str,
        tag: str,
        freq_hint: int | None = None,
    ) -> RelatedWordNode | None:
        m = self._dict.lookup_exact_or_deinflect(term)
        if m is None:
            return None
        meaning = m.best_concise_meaning("bilingual", strict=True) or m.best_concise_meaning("monolingual", strict=True)
        if not meaning or meaning.strip() == term or meaning.strip() == m.lemma:
            return None
        if len(meaning) > 26:
            meaning = meaning[:26] + "…"
        pitch = "/".join(m.pitches[:2]) if m.pitches else ""
        freq = m.frequency if m.frequency is not None else freq_hint
        return RelatedWordNode(
            term=m.lemma or term,
            reading=m.reading,
            meaning=meaning,
            pitch=pitch,
            frequency=freq,
            relation=relation,
            tag=tag,
            is_known=(term in known_words or m.lemma in known_words),
        )

    def build_tree(
        self,
        lemma: str,
        reading: str = "",
        known_words: set[str] | None = None,
        current_sentence: str = "",
        active_pack_id: str = "",
    ) -> WordAssociationTree:
        """建構單詞的三枝聯想記憶樹（同漢字詞族・複合搭配・自他/同音易混詞＋作品重現）。"""
        known = known_words or set()
        clean_lemma = (lemma or "").strip()
        clean_reading = (reading or "").strip()
        kanji_roots = list(dict.fromkeys(_KANJI_RE.findall(clean_lemma)))
        tree = WordAssociationTree(
            lemma=clean_lemma,
            reading=clean_reading,
            kanji_roots=kanji_roots,
        )
        if not clean_lemma:
            return tree

        self._ensure_kanji_index()
        seen_terms: set[str] = {clean_lemma}

        # === 1. 自他動詞配對 + 同音異義易混詞 ===
        for partner, tag in _TRANSITIVITY_LOOKUP.get(clean_lemma, []):
            if partner in seen_terms:
                continue
            node = self._summarize_term(partner, known, relation="transitivity", tag=tag)
            if node is not None:
                tree.confusables.append(node)
                seen_terms.add(node.term)
                seen_terms.add(partner)

        # 同音異義詞（相同讀音、不同漢字寫法、依詞頻取最常見前 3 個）
        if clean_reading and len(clean_reading) >= 2:
            try:
                with self._dict._lock:
                    h_rows = self._dict._conn.execute(
                        """
                        SELECT DISTINCT t.term FROM terms t
                        JOIN dictionaries d ON d.id = t.dict_id
                        WHERE d.enabled = 1 AND d.kind = 'bilingual'
                          AND t.reading = ? AND t.term != ?
                        LIMIT 28
                        """,
                        (clean_reading, clean_lemma),
                    ).fetchall()
            except Exception:
                h_rows = []
            h_terms = [
                str(r["term"])
                for r in h_rows
                if _KANJI_RE.search(str(r["term"])) and str(r["term"]) not in seen_terms
            ]
            if h_terms:
                h_freqs = self._dict.batch_lookup_frequencies(h_terms)
                h_terms.sort(
                    key=lambda w: (
                        0 if w in known else 1,
                        h_freqs.get(w, 999999),
                        len(w),
                    )
                )
                for ht in h_terms[:6]:
                    if len(tree.confusables) >= 4:
                        break
                    if ht in seen_terms:
                        continue
                    node = self._summarize_term(
                        ht,
                        known,
                        relation="homophone",
                        tag="同音異字",
                        freq_hint=h_freqs.get(ht),
                    )
                    if node is not None:
                        tree.confusables.append(node)
                        seen_terms.add(node.term)
                        seen_terms.add(ht)

        # === 2. 常用複合詞／衍生片語（完整包含目前詞幹的高頻詞） ===
        stem = clean_lemma[:-1] if (len(clean_lemma) >= 2 and re.match(r"^[ぁ-ん]$", clean_lemma[-1])) else clean_lemma
        comp_cands: list[tuple[str, int]] = []
        if len(stem) >= 2 or kanji_roots:
            search_key = clean_lemma if len(clean_lemma) >= 2 else (kanji_roots[0] if kanji_roots else "")
            if search_key:
                pool = self._kanji_index.get(kanji_roots[0], []) if kanji_roots else self._top_terms
                for term, freq in pool:
                    if term in seen_terms or len(term) <= len(clean_lemma):
                        continue
                    if clean_lemma in term:
                        comp_cands.append((term, freq))
        comp_cands.sort(key=lambda x: (0 if x[0] in known else 1, x[1], len(x[0])))
        for term, freq in comp_cands[:10]:
            if len(tree.compounds) >= 5:
                break
            if term in seen_terms:
                continue
            node = self._summarize_term(term, known, relation="compound", tag="常用複合", freq_hint=freq)
            if node is not None:
                tree.compounds.append(node)
                seen_terms.add(node.term)
                seen_terms.add(term)

        # === 3. 同漢字詞族（以熟帶生：Anki 已知熟詞優先，其次高頻 JPDB 詞！） ===
        fam_cands: list[tuple[str, int, str]] = []
        for k in kanji_roots[:2]:
            for term, freq in self._kanji_index.get(k, []):
                if term in seen_terms:
                    continue
                fam_cands.append((term, freq, k))
        # 去重並優先排列已知熟詞與高頻詞
        best_fam: dict[str, tuple[int, str]] = {}
        for term, freq, k in fam_cands:
            if term not in best_fam or freq < best_fam[term][0]:
                best_fam[term] = (freq, k)

        ordered_fam = sorted(
            best_fam.items(),
            key=lambda kv: (0 if kv[0] in known else 1, kv[1][0], len(kv[0])),
        )
        for term, (freq, k) in ordered_fam[:14]:
            if len(tree.kanji_family) >= 6:
                break
            if term in seen_terms:
                continue
            node = self._summarize_term(
                term,
                known,
                relation="kanji_family",
                tag=f"同字「{k}」",
                freq_hint=freq,
            )
            if node is not None:
                tree.kanji_family.append(node)
                seen_terms.add(node.term)
                seen_terms.add(term)

        # === 4. 同作品劇本重現句（防劇透：僅搜尋目前遊玩進度以內或最早期章節！） ===
        if self._voice_packs is not None and active_pack_id and len(clean_lemma) >= 2:
            try:
                with self._voice_packs._lock:
                    cur_seq = int(self._voice_packs._cursors.get(active_pack_id, 0) or 0)
                    if cur_seq > 0:
                        rows = self._voice_packs._conn.execute(
                            """
                            SELECT scene_file, speaker, clean_text, translation
                            FROM dialogue_lines
                            WHERE pack_id = ? AND seq_index <= ? AND clean_text LIKE ?
                              AND length(clean_text) BETWEEN 6 AND 55
                            ORDER BY seq_index DESC
                            LIMIT 8
                            """,
                            (active_pack_id, cur_seq, f"%{clean_lemma}%"),
                        ).fetchall()
                    else:
                        rows = self._voice_packs._conn.execute(
                            """
                            SELECT scene_file, speaker, clean_text, translation
                            FROM dialogue_lines
                            WHERE pack_id = ? AND clean_text LIKE ?
                              AND length(clean_text) BETWEEN 6 AND 55
                            ORDER BY seq_index ASC
                            LIMIT 8
                            """,
                            (active_pack_id, f"%{clean_lemma}%"),
                        ).fetchall()
            except Exception:
                rows = []
            for r in rows:
                txt = str(r["clean_text"] or "").strip()
                if not txt or (current_sentence and txt in current_sentence):
                    continue
                tree.game_recurrences.append(
                    GameRecurrenceLine(
                        scene_file=str(r["scene_file"] or ""),
                        speaker=str(r["speaker"] or ""),
                        text=txt,
                        translation=str(r["translation"] or ""),
                    )
                )
                if len(tree.game_recurrences) >= 2:
                    break

        return tree
