"""Galgame Hook 文本專業清洗引擎（高效能低延遲版：< 1ms 完成，絕不卡頓介面）。

針對 KiriKiriZ（如 ATRI）、BGI、Artemis 等引擎在 Textractor / LunaHook 下常見的：
1. 漸進前綴累積重繪：
   「『 『おい 『おいおい、 『おいおい、そ ... 『おいおい、そんな事したら……』」
   -> 「『おいおい、そんな事したら……』」
2. 逐字 2~4 倍描邊重複：
   「ああたたししはは」 -> 「あたしは」
3. 整句/長片語重複輸出：
   「そんな事したら……そんな事したら……」 -> 「そんな事したら……」
同時嚴格保護正常日語疊詞與語氣詞（如「おいおい」「どんどん」「えええっ」）。
"""

from __future__ import annotations

import re

# C 引擎正則：極速消除長度 4~120 的連續重複片語（毫秒級，取代原本 O(N^3) Python 迴圈）
_REPEATED_PHRASE_RE = re.compile(r"(.{4,120}?)\1+")
_MAX_CLEAN_LEN = 3500
_MAX_FINAL_SENT_LEN = 260


def collapse_progressive_prefixes(text: str) -> str:
    """偵測並折疊打字機逐字重繪造成的漸進前綴鏈 p1 + p2 + ... + F -> F。"""
    s = text.strip()
    n = len(s)
    if n < 4 or n > _MAX_CLEAN_LEN:
        return s

    # 優先從 offset=0 檢查；若開頭像短角色名（如「キャサリン 『『おい...」），再檢查前 16 字內引號/空白
    candidate_offsets = [0]
    max_lead = min(16, n // 3)
    for idx in range(1, max_lead):
        if s[idx] in "「『（(" or s[idx - 1] in " \t\n　":
            candidate_offsets.append(idx)
            if len(candidate_offsets) >= 4:
                break

    for offset in candidate_offsets:
        lead = s[:offset].rstrip()
        body = s[offset:].lstrip()
        collapsed = _collapse_body_prefixes(body)
        if collapsed != body:
            return f"{lead} {collapsed}".strip() if lead else collapsed
    return s


def _collapse_body_prefixes(body: str) -> str:
    n = len(body)
    if n < 4:
        return body

    first_char = body[0]
    # 單句 Galgame 台詞最多約 260 字，僅需在尾端 _MAX_FINAL_SENT_LEN 範圍內尋找最終句起點 j
    low_bound = max(0, n - _MAX_FINAL_SENT_LEN)
    checked = 0

    for j in range(n - 2, low_bound, -1):
        if body[j] != first_char:
            continue
        checked += 1
        if checked > 40:
            break

        final_sent = body[j:].strip()
        if len(final_sent) < 2:
            continue

        left = body[:j].strip()
        if not left:
            continue

        ok, seg_count, max_seg_len = _matches_prefix_chain(left, final_sent)
        if not ok:
            continue

        # 防誤殺規則：
        # 1) 至少有 2 段以上漸進前綴且最長前綴 >= 2 字（保護「えええっ！？」「あああ、もう！」）
        # 2) 單段但長度 >= 3 且為完整或近完整重複（保護「おいおい、…」）
        if (seg_count >= 2 and max_seg_len >= 2) or (
            seg_count == 1 and max_seg_len >= 3 and len(final_sent) >= 4
        ):
            return final_sent

    return body


def _matches_prefix_chain(left: str, final_sent: str) -> tuple[bool, int, int]:
    """線性掃描驗證 left 是否完全由 final_sent 的若干前綴依序組成。"""
    pos = 0
    total = len(left)
    f_len = len(final_sent)
    f0 = final_sent[0]
    seg_count = 0
    max_seg_len = 0

    while pos < total:
        while pos < total and left[pos] in " \t\r\n　":
            pos += 1
        if pos >= total:
            break

        if left[pos] != f0:
            return False, 0, 0

        k = 0
        limit = min(f_len, total - pos)
        while k < limit and left[pos + k] == final_sent[k]:
            k += 1

        if k == 0:
            return False, 0, 0

        if pos + k < total and left[pos + k] not in " \t\r\n　" and left[pos + k] != f0:
            backtracked = False
            for back in range(k - 1, 0, -1):
                if left[pos + back] == f0:
                    k = back
                    backtracked = True
                    break
            if not backtracked:
                return False, 0, 0

        pos += k
        seg_count += 1
        if k > max_seg_len:
            max_seg_len = k

    return seg_count > 0, seg_count, max_seg_len


def remove_repeated_characters(text: str) -> str:
    """移除引擎 2~4 倍逐字描邊重複（如「ああいいうう」->「あいう」）。"""
    n = len(text)
    if n < 4 or n > _MAX_CLEAN_LEN:
        return text
    for k in (2, 3, 4):
        if n >= k * 2 and n % k == 0:
            if all(len(set(text[i : i + k])) == 1 for i in range(0, n, k)):
                return text[::k]
    return text


def _phrase_dedup_replacer(match: re.Match[str]) -> str:
    chunk = match.group(1)
    # 跳過純空白或全同字元（例如 ………… / ーーーー）
    if not chunk.strip() or len(set(chunk)) == 1:
        return match.group(0)
    return chunk


def remove_repeated_phrases(text: str, min_phrase_len: int = 4) -> str:
    """以 C 正則極速消除連續重複片語（< 0.2ms），不傷正常日語疊詞與省略號。"""
    if len(text) < min_phrase_len * 2 or len(text) > _MAX_CLEAN_LEN:
        return text
    s = text
    for _ in range(3):
        updated = _REPEATED_PHRASE_RE.sub(_phrase_dedup_replacer, s)
        if updated == s:
            break
        s = updated
    return s


def clean_galgame_hook_text(raw: str, dedup: bool = True) -> str:
    """完整的 Galgame Hook 文本高速清洗管線。"""
    cleaned = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{2,}", "\n", cleaned)
    if not dedup or len(cleaned) < 4 or len(cleaned) > _MAX_CLEAN_LEN:
        return cleaned

    # 1. 先移除 2~4 倍描邊重複字元（如「あああたたたししし」->「あたし」）
    cleaned = remove_repeated_characters(cleaned)
    # 2. 折疊漸進式打字機前綴鏈（核心根治 ATRI 重複前綴問題）
    cleaned = collapse_progressive_prefixes(cleaned)
    # 3. 以 C 正則極速消除連續重複片語
    cleaned = remove_repeated_phrases(cleaned, min_phrase_len=4)
    # 4. 若移除後又暴露出前綴鏈，再收斂一次
    cleaned = collapse_progressive_prefixes(cleaned)
    return cleaned.strip()


def merge_hook_stream_chunks(buffered: str, incoming: str, dedup: bool = True) -> str:
    """將同線程連續到達的 Hook 片段乾淨合併，並限制緩衝上限防爆記憶體或卡頓。"""
    if len(incoming) > _MAX_CLEAN_LEN:
        return buffered
    inc = clean_galgame_hook_text(incoming, dedup=dedup)
    if not inc:
        return buffered
    if not buffered:
        return inc
    if inc == buffered:
        return buffered

    if inc.startswith(buffered):
        return inc
    if buffered.startswith(inc):
        return buffered

    # 頭尾重疊拼接
    max_overlap = min(len(buffered), len(inc), 24)
    for ov in range(max_overlap, 1, -1):
        if buffered.endswith(inc[:ov]):
            merged = buffered + inc[ov:]
            if len(merged) > _MAX_CLEAN_LEN:
                return inc
            return clean_galgame_hook_text(merged, dedup=dedup)

    # 前綴極高度一致時（如末尾「…」->「……』」微調），取較長完整版
    if len(buffered) >= 3 and len(inc) >= 3 and buffered[:3] == inc[:3]:
        common = 0
        for a, b in zip(buffered, inc):
            if a == b:
                common += 1
            else:
                break
        if common >= min(len(buffered), len(inc)) - 2:
            return inc if len(inc) >= len(buffered) else buffered

    combined = buffered + inc
    if len(combined) > _MAX_CLEAN_LEN:
        return inc
    return clean_galgame_hook_text(combined, dedup=dedup)


def is_progressive_extension(prev_sentence: str, new_sentence: str) -> bool:
    """判斷新送出的句子是否為上一句打字機停頓後的後半段延伸。"""
    if not prev_sentence or not new_sentence or prev_sentence == new_sentence:
        return False
    if new_sentence.startswith(prev_sentence):
        return True
    trimmed_prev = prev_sentence.rstrip("…・。.」』 ")
    if len(trimmed_prev) >= 3 and new_sentence.startswith(trimmed_prev) and len(new_sentence) > len(prev_sentence):
        return True
    return False
