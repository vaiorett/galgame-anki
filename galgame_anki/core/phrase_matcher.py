"""離線語塊與慣用語匹配器（以分詞邊界為提示，用本機 SQLite 索引匹配相鄰詞素，不改動原句分詞與 1T 統計）。"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from galgame_anki.core.deinflector import RULE_NONE, deinflect, pos_matches_rule
from galgame_anki.core.dictionary import (
    DictionaryEngine,
    LookupMatch,
    _HAS_KANJI_RE,
    _is_inflection_stub_entries,
    is_katakana_loanword,
)
from galgame_anki.core.tokenizer import Token
from galgame_anki.core.verb_lesson import spoken_surfaces, verb_group

# 不作為多詞語塊開頭或結尾的純語氣／格助詞（保留結尾「に」供「さすがに」「本当に」等副詞語塊）
_BAD_EDGE_START = frozenset({"は", "が", "を", "の", "に", "で", "へ", "と", "も", "よ", "ね", "ぞ", "わ", "か", "だ", "です"})
_BAD_EDGE_END = frozenset({"は", "が", "を", "の", "で", "へ", "と", "も", "よ", "ね", "ぞ", "わ", "か", "だ", "です"})
_PUNCT_ANY_RE = re.compile(r"[。、！？「」『』（）\(\)\[\]【】…・―—\s,.!?:;\"'~〜]")


@dataclass(frozen=True)
class PhraseMatch:
    """命中原句區間 [start:end] 的完整語塊查詞結果。"""

    surface: str
    start: int
    end: int
    token_start: int
    token_end: int
    lookup: LookupMatch


def build_phrase_token(phrase: PhraseMatch) -> Token:
    """將命中的語塊封裝為具有精準原句範圍 [start:end] 的 Token，供查詞與 Anki 收錄共用。"""
    m = phrase.lookup
    return Token(
        surface=phrase.surface,
        lemma=m.lemma,
        reading=m.reading,
        start=phrase.start,
        end=phrase.end,
        is_punctuation=False,
        is_function_word=False,
        has_dict_entry=True,
        lookup=m,
    )


def _has_usable_sense(match: LookupMatch) -> bool:
    if not match.all_entries:
        return False
    if _is_inflection_stub_entries(match.all_entries):
        return False
    by_lang = match.senses_by_language(max_per_lang=1, convert_tw=False)
    return bool(by_lang["zh"] or by_lang["en"] or by_lang["ja"])


def _lookup_inflected_phrase(
    span: str,
    prefix: str,
    last_surface: str,
    dictionary: DictionaryEngine,
    hit_terms: set[str],
) -> LookupMatch | None:
    """先測完整語塊原形，再對結尾動詞／形容詞做活用還原（例如「気を遣った」→「気を遣う」）。"""
    # 1) 完整精確語塊
    if span in hit_terms:
        m = dictionary.lookup_exact_or_deinflect(span)
        if m is not None and _has_usable_sense(m):
            # 純片假名或混合寫法（ワンチャン vs ワンちゃん）不可跨寫法誤配
            if is_katakana_loanword(span) and m.lemma != span:
                pass
            elif _HAS_KANJI_RE.search(span) and m.lemma != span and not m.reasons:
                pass
            elif not _HAS_KANJI_RE.search(span) and m.lemma != span and m.reading != span:
                pass
            else:
                return m

    # 2) 結尾詞素活用還原（如 気を + 遣った -> 気を遣う，話の腰を + 折らない -> 話の腰を折る）
    if not prefix:
        return None
    for cand in deinflect(last_surface):
        if cand.rules == RULE_NONE or cand.term == last_surface:
            continue
        base_phrase = prefix + cand.term
        if base_phrase not in hit_terms:
            continue
        m = dictionary.lookup_exact_or_deinflect(base_phrase)
        if m is None or not _has_usable_sense(m):
            continue
        if not any(pos_matches_rule(e.pos, cand.rules) for e in m.all_entries):
            continue
        return LookupMatch(
            surface=span,
            lemma=m.lemma,
            reading=m.reading,
            reasons=cand.reasons,
            bilingual_entries=m.bilingual_entries,
            monolingual_entries=m.monolingual_entries,
            pitches=m.pitches,
            frequency=m.frequency,
            alt_matches=m.alt_matches,
        )
    return None


def find_phrase_for_token(
    sentence: str,
    tokens: list[Token],
    token: Token,
    dictionary: DictionaryEngine | None,
    *,
    max_tokens: int = 5,
    max_chars: int = 14,
) -> PhraseMatch | None:
    """在包含指定 token 的有限相鄰詞素區間內，以本機 SQLite 索引尋找最佳完整語塊／慣用語。"""
    if not sentence or not tokens or dictionary is None or token.is_punctuation:
        return None

    idx = next((i for i, t in enumerate(tokens) if t is token), -1)
    if idx < 0:
        idx = next((i for i, t in enumerate(tokens) if t.start == token.start and t.end == token.end), -1)
    if idx < 0:
        return None

    # 限制在同一個無標點、相鄰連續的詞素區段內
    left_bound = idx
    while left_bound > 0:
        prev_t = tokens[left_bound - 1]
        cur_t = tokens[left_bound]
        if prev_t.is_punctuation or prev_t.end != cur_t.start:
            break
        if idx - (left_bound - 1) >= max_tokens:
            break
        left_bound -= 1

    right_bound = idx
    while right_bound + 1 < len(tokens):
        cur_t = tokens[right_bound]
        nxt_t = tokens[right_bound + 1]
        if nxt_t.is_punctuation or cur_t.end != nxt_t.start:
            break
        if (right_bound + 1) - idx >= max_tokens:
            break
        right_bound += 1

    # 收集所有含 idx、至少 2 個詞素的候選範圍與預篩鍵
    cand_spans: list[tuple[int, int, str, str, str]] = []
    probe_keys: set[str] = set()

    spoken_lemmas = {}
    for j in range(left_bound + 1, right_bound + 1):
        tail = tokens[j]
        if tail.verb_type and tail.surface in spoken_surfaces(tail.lemma, verb_group(tail.lemma, tail.verb_type)):
            spoken_lemmas[j] = tail.lemma

    for i in range(left_bound, idx + 1):
        first_surf = tokens[i].surface
        if first_surf in _BAD_EDGE_START:
            continue
        for j in range(max(idx, i + 1), min(right_bound, i + max_tokens - 1) + 1):
            last_surf = tokens[j].surface
            if last_surf in _BAD_EDGE_END:
                continue
            start_pos = tokens[i].start
            end_pos = tokens[j].end
            if end_pos - start_pos > max_chars:
                break
            span = sentence[start_pos:end_pos]
            if not span or _PUNCT_ANY_RE.search(span):
                continue
            # 「に」結尾只允許純假名副詞語塊（如 さすがに），不把任意「駅に」當語塊
            if last_surf == "に" and _HAS_KANJI_RE.search(span):
                continue
            prefix = sentence[start_pos : tokens[j].start]
            cand_spans.append((i, j, span, prefix, last_surf))
            probe_keys.add(span)
            if j in spoken_lemmas:
                probe_keys.add(prefix + spoken_lemmas[j])
            for dc in deinflect(last_surf):
                if dc.rules != RULE_NONE and dc.term != last_surf:
                    probe_keys.add(prefix + dc.term)

    if not cand_spans:
        return None

    # 單次索引預篩：未命中的候選直接跳過，將懸停延遲壓在 1~2ms 內
    hit_terms = dictionary.batch_has_terms(probe_keys)
    if not hit_terms:
        return None

    # 由長到短嘗試（優先最完整慣用語，如 話の腰を折る > 腰を折る，君主国 > 君主）
    cand_spans.sort(key=lambda item: (len(item[2]), item[1] - item[0]), reverse=True)

    best_match: PhraseMatch | None = None
    best_rank: tuple[int, int, int] = (-1, -1, -1)

    for i, j, span, prefix, last_surf in cand_spans:
        match = _lookup_inflected_phrase(span, prefix, last_surf, dictionary, hit_terms)
        if match is None and j in spoken_lemmas:
            headword = prefix + spoken_lemmas[j]
            if headword in hit_terms:
                base = dictionary.lookup_exact_or_deinflect(headword)
                if base and base.lemma == headword and _has_usable_sense(base):
                    match = replace(base, surface=span)
        if match is None:
            continue
        # 避免把純「副詞/連體詞+名詞」誤收；優先有明確詞性與讀音的完整詞條
        has_good_rd = 1 if (match.reading and not _HAS_KANJI_RE.search(match.reading)) else 0
        has_zh = 1 if match.senses_by_language(max_per_lang=1, convert_tw=False)["zh"] else 0
        rank = (len(span), has_good_rd, has_zh)
        if rank > best_rank:
            best_rank = rank
            best_match = PhraseMatch(
                surface=span,
                start=tokens[i].start,
                end=tokens[j].end,
                token_start=i,
                token_end=j,
                lookup=match,
            )

    return best_match
