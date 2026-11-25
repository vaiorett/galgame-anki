"""💎 1T 黃金句礦場（Sentence Bank）掃描引擎（借鑑 ajatt.top one-target-sentences & subs2srs）。

對已索引的遊戲劇本庫（如 ATRI 12,172 句／4,753 句 0% BGM 純人聲）進行兩階段高速掃描：
1. 第一階段（純 Janome 形態素快速過濾，~1–3 秒掃完數千句並緩存於記憶體）：
   扣除使用者 Anki 已知詞後，精準篩出恰好只缺 1 個生詞（1T）或 1–2 個生詞的候選句，並計算作品詞彙覆蓋率。
2. 第二階段（對依 JPDB 詞頻排序的優質候選句執行完整詞典與活用還原）：
   產出與主視窗完全一致的 `TokenizedSentence`、高低音調、詞頻排名與精煉釋義，供一鍵試聽與精煉製卡。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from galgame_anki.core.context_meaning import format_contextual_meaning
from galgame_anki.core.dictionary import DictionaryEngine
from galgame_anki.core.tokenizer import (
    JapaneseTokenizer,
    Token,
    TokenizedSentence,
    highlight_target_in_sentence,
)
from galgame_anki.core.voice_pack import VoicePackManager

_JP_CONTENT_CHAR_RE = re.compile(r"[ぁ-んァ-ヶ一-龯々]")
_PUNCT_POS = frozenset({"記号", "補助記号", "空白"})
_FUNC_POS = frozenset({"助詞", "助動詞", "接続詞", "フィラー", "感動詞"})


@dataclass
class SentenceBankItem:
    pack_id: str
    pack_name: str
    seq_index: int
    scene_file: str
    speaker: str
    char_key: str
    clean_text: str
    highlighted_text: str
    translation: str
    voice_ids: list[str]
    target_token: Token
    target_surface: str
    target_lemma: str
    target_reading: str
    target_frequency: int | None
    target_pitches: list[str]
    target_meaning: str
    unknown_count: int
    tokenized: TokenizedSentence


@dataclass
class SentenceBankScanResult:
    items: list[SentenceBankItem] = field(default_factory=list)
    total_lines_scanned: int = 0
    zero_t_count: int = 0
    one_t_count: int = 0
    total_content_tokens: int = 0
    known_content_tokens: int = 0

    @property
    def coverage_pct(self) -> float:
        if self.total_content_tokens <= 0:
            return 0.0
        return (self.known_content_tokens / self.total_content_tokens) * 100.0


class SentenceBankScanner:
    """帶形態素記憶體快取的高速 1T 句庫掃描器。"""

    def __init__(self, tokenizer: JapaneseTokenizer, dictionary: DictionaryEngine) -> None:
        self._tokenizer = tokenizer
        self._dictionary = dictionary
        # 快取 (pack_id, seq_index) -> list[(surface, lemma)] 的實詞列表，切換角色／門檻毫秒重掃！
        self._morph_cache: dict[tuple[str, int], list[tuple[str, str]]] = {}

    def _fast_content_lemmas(self, pack_id: str, seq_index: int, text: str) -> list[tuple[str, str]]:
        key = (pack_id, seq_index)
        cached = self._morph_cache.get(key)
        if cached is not None:
            return cached

        out: list[tuple[str, str]] = []
        janome = getattr(self._tokenizer, "_janome", None)
        if janome is not None:
            for m in janome.tokenize(text):
                surf = m.surface
                if not surf or not _JP_CONTENT_CHAR_RE.search(surf):
                    continue
                pos_parts = m.part_of_speech.split(",")
                pos0 = pos_parts[0] if pos_parts else ""
                pos1 = pos_parts[1] if len(pos_parts) > 1 else ""
                if pos0 in _PUNCT_POS or pos0 in _FUNC_POS:
                    continue
                if pos0 == "名詞" and pos1 in ("非自立", "代名詞", "数", "接尾"):
                    continue
                if pos0 == "動詞" and pos1 in ("非自立", "接尾"):
                    continue
                base = m.base_form if (m.base_form and m.base_form != "*") else surf
                if len(surf) == 1 and re.match(r"^[ぁ-んァ-ヶー]$", surf):
                    continue
                out.append((surf, base))
        else:
            # 無 Janome 時退回簡易分詞
            tok = self._tokenizer.tokenize(text, set())
            for t in tok.tokens:
                if t.is_content_word and not t.is_punctuation and not t.is_function_word:
                    out.append((t.surface, t.lemma or t.surface))

        self._morph_cache[key] = out
        return out

    def scan_candidates(
        self,
        voice_packs: VoicePackManager,
        pack_id: str,
        known_words: set[str],
        start_scene: str = "",
        end_scene: str = "",
        char_key: str = "",
        voiced_only: bool = True,
        max_unknown: int = 1,
        dedup_by_lemma: bool = True,
        keyword: str = "",
        dict_preference: str = "bilingual",
        limit: int = 180,
        progress_cb: Callable[[str], None] | None = None,
    ) -> SentenceBankScanResult:
        """從已索引遊戲劇本庫篩選高價值 1T（或 1T–2T）黃金句並按 JPDB 詞頻排序。"""
        packs = [p for p in voice_packs.list_packs() if p.pack_id == pack_id]
        pack_name = packs[0].game_name if packs else ""

        if progress_cb:
            progress_cb("正在讀取遊戲劇本索引...")

        raw_lines = voice_packs.iter_pack_lines(
            pack_id=pack_id,
            start_scene=start_scene,
            end_scene=end_scene,
            voiced_only=voiced_only,
            char_key=char_key,
        )

        kw = keyword.strip().lower()
        if kw:
            raw_lines = [
                ln
                for ln in raw_lines
                if kw in ln["clean_text"].lower()
                or kw in ln["speaker"].lower()
                or kw in ln["translation"].lower()
            ]

        result = SentenceBankScanResult(total_lines_scanned=len(raw_lines))
        if not raw_lines:
            return result

        # === Pass 1: 高速 Janome 過濾 + 計算詞彙覆蓋率 ===
        prelim: list[tuple[dict[str, Any], list[str], int]] = []
        all_unknown_lemmas: set[str] = set()

        for idx, ln in enumerate(raw_lines, start=1):
            if progress_cb and (idx == 1 or idx % 400 == 0 or idx == len(raw_lines)):
                progress_cb(f"正在掃描劇本詞彙 ({idx}/{len(raw_lines)})...")
            txt = ln["clean_text"]
            if len(txt) < 4 or len(txt) > 95:
                continue
            pairs = self._fast_content_lemmas(pack_id, ln["seq_index"], txt)
            if not pairs:
                continue

            unk_lemmas: list[str] = []
            seen_u: set[str] = set()
            for surf, lem in pairs:
                result.total_content_tokens += 1
                if lem in known_words or surf in known_words:
                    result.known_content_tokens += 1
                else:
                    if lem not in seen_u:
                        seen_u.add(lem)
                        unk_lemmas.append(lem)

            u_cnt = len(unk_lemmas)
            if u_cnt == 0:
                result.zero_t_count += 1
                continue
            if u_cnt == 1:
                result.one_t_count += 1
            if u_cnt <= max_unknown:
                prelim.append((ln, unk_lemmas, u_cnt))
                all_unknown_lemmas.update(unk_lemmas)

        if not prelim:
            return result

        # 單次 SQLite 批次查詢所有候選生詞的 JPDB 詞頻
        freq_map = self._dictionary.batch_lookup_frequencies(all_unknown_lemmas)

        def _prelim_score(entry: tuple[dict[str, Any], list[str], int]) -> tuple[int, int, int]:
            ln, u_lems, u_cnt = entry
            best_f = min((freq_map.get(u, 999999) for u in u_lems), default=999999)
            tlen = len(ln["clean_text"])
            # 偏好 8～36 字的精煉自然對白（不會太短缺語境、也不會太長難複習）
            len_pen = 0 if 8 <= tlen <= 36 else abs(tlen - 22)
            return (u_cnt, best_f, len_pen)

        prelim.sort(key=_prelim_score)

        # 去重預留：在 Pass 2 之前先過濾掉同一個目標生詞過多重複候選，大幅加速精煉查詞
        pass2_pool: list[dict[str, Any]] = []
        lemma_quota: dict[str, int] = {}
        max_pool = max(limit * 2, 260)
        for ln, u_lems, _u_cnt in prelim:
            primary_u = min(u_lems, key=lambda u: freq_map.get(u, 999999))
            if dedup_by_lemma and lemma_quota.get(primary_u, 0) >= 2:
                continue
            lemma_quota[primary_u] = lemma_quota.get(primary_u, 0) + 1
            pass2_pool.append(ln)
            if len(pass2_pool) >= max_pool:
                break

        # === Pass 2: 對勝出候選執行完整 GalAnki 詞典分詞與釋義精煉 ===
        final_items: list[SentenceBankItem] = []
        seen_final_lemmas: set[str] = set()

        for idx, ln in enumerate(pass2_pool, start=1):
            if progress_cb and (idx == 1 or idx % 40 == 0 or idx == len(pass2_pool)):
                progress_cb(f"正在精煉 1T 黃金句與詞頻 ({idx}/{len(pass2_pool)})...")

            tokenized = self._tokenizer.tokenize(ln["clean_text"], known_words)
            if tokenized.unknown_count == 0 or tokenized.unknown_count > max_unknown:
                continue

            unknown_tokens = [
                t
                for t in tokenized.tokens
                if t.is_content_word and not t.is_known and not t.is_punctuation and not t.is_function_word
            ]
            if not unknown_tokens:
                continue

            # 選取詞頻最靠前的生詞作為目標焦點詞
            def _tok_freq(t: Token) -> int:
                if t.lookup and t.lookup.frequency is not None:
                    return t.lookup.frequency
                return freq_map.get(t.lemma or t.surface, 999999)

            target = min(unknown_tokens, key=_tok_freq)
            match = target.lookup
            lemma = match.lemma if match else (target.lemma or target.surface)
            reading = match.reading if match else target.reading
            if dedup_by_lemma and lemma in seen_final_lemmas:
                continue
            seen_final_lemmas.add(lemma)

            freq = match.frequency if (match and match.frequency is not None) else freq_map.get(lemma)
            pitches = list(match.pitches[:3]) if (match and match.pitches) else []

            meaning = ""
            if match:
                base = match.best_concise_meaning(dict_preference, strict=True)
                if not base:
                    alt_kind = "bilingual" if dict_preference == "monolingual" else "monolingual"
                    base = match.best_concise_meaning(alt_kind, strict=True)
                if base:
                    meaning = (
                        format_contextual_meaning(target.surface, lemma, base, match.reasons)
                        if dict_preference == "bilingual"
                        else base
                    )

            highlighted = highlight_target_in_sentence(ln["clean_text"], target.start, target.end)
            final_items.append(
                SentenceBankItem(
                    pack_id=pack_id,
                    pack_name=pack_name,
                    seq_index=ln["seq_index"],
                    scene_file=ln["scene_file"],
                    speaker=ln["speaker"],
                    char_key=ln["char_key"],
                    clean_text=ln["clean_text"],
                    highlighted_text=highlighted,
                    translation=ln["translation"],
                    voice_ids=list(ln["voice_ids"]),
                    target_token=target,
                    target_surface=target.surface,
                    target_lemma=lemma,
                    target_reading=reading,
                    target_frequency=freq,
                    target_pitches=pitches,
                    target_meaning=meaning,
                    unknown_count=tokenized.unknown_count,
                    tokenized=tokenized,
                )
            )
            if len(final_items) >= limit:
                break

        final_items.sort(
            key=lambda it: (
                it.unknown_count,
                it.target_frequency if it.target_frequency is not None else 999999,
                abs(len(it.clean_text) - 20),
            )
        )
        result.items = final_items
        return result
