"""語境感知詞義轉換與義項排序引擎（看這句話的當下意思，不再硬搬孤立詞典原形！）。"""

from __future__ import annotations

import re


def _extract_primary_gloss_head(base_meaning: str) -> str:
    """從基礎中文釋義抽出最核心的 1 個簡短詞組（例如「明白、懂、理解」→「明白」）。"""
    text = base_meaning.strip()
    if not text:
        return ""
    # 去掉前綴標號與括號補充
    text = re.sub(r"^[①②③④⑤1-9][.、．\s]+", "", text)
    text = re.sub(r"[（\(][^）\)]*[）\)]", "", text).strip()
    parts = re.split(r"[、，,；;。/]", text)
    head = next((p.strip() for p in parts if p.strip()), text)
    return head[:16]


def format_contextual_meaning(
    surface: str,
    lemma: str,
    base_meaning: str,
    reasons: tuple[str, ...] | list[str] = (),
) -> str:
    """將「原形詞義 + 句中活用步驟」組裝成一眼就能看懂的【本句當下語境意思】。"""
    clean_base = base_meaning.strip()
    if not clean_base:
        return ""
    if not reasons or surface == lemma:
        return clean_base

    joined = " ".join(reasons)
    head = _extract_primary_gloss_head(clean_base)
    if not head:
        return clean_base

    # 若原詞義是純英文（無中文），不硬嵌進中文語法殼裡，改用清楚的括號標註活用
    if not re.search(r"[\u4e00-\u9fff]", head):
        return f"{clean_base}（句中活用：{' → '.join(reasons)}）"

    is_past = "過去" in joined
    is_neg = "否定" in joined
    is_pot = "可能" in joined
    is_pass = "被動" in joined and not is_pot
    is_caus = "使役" in joined
    is_want = "願望" in joined or "たい" in joined
    is_prog = "進行" in joined or "ている" in joined or "てる" in joined
    is_too = "過於" in joined or "すぎる" in joined or "過ぎる" in joined
    is_done = "完了" in joined or "てしまう" in joined or "ちゃう" in joined or "じゃう" in joined
    is_cond = "假定" in joined or "たら" in joined or "ば" in joined
    is_try = "嘗試" in joined or "てみる" in joined
    is_prep = "預先" in joined or "ておく" in joined or "とく" in joined
    is_te = "て形" in joined and not (is_prog or is_done or is_try or is_prep)

    # 依語氣與活用層次組裝自然中文語境表達
    phrase = head
    if is_caus:
        phrase = f"讓／使人{phrase}"
    elif is_pass:
        phrase = f"被{phrase}"

    if is_pot:
        if is_neg and is_past:
            phrase = f"沒能{phrase}／做不到{phrase}"
        elif is_neg:
            phrase = f"無法{phrase}／不會{phrase}"
        elif is_past:
            phrase = f"成功{phrase}了／能夠{phrase}了"
        else:
            phrase = f"能夠{phrase}／會{phrase}"
    elif is_want:
        if is_neg and is_past:
            phrase = f"當時不想{phrase}"
        elif is_neg:
            phrase = f"不想{phrase}"
        elif is_past:
            phrase = f"當時想{phrase}"
        else:
            phrase = f"想{phrase}"
    elif is_too:
        phrase = f"太過{phrase}{'了' if is_past else ''}"
    elif is_done:
        phrase = f"不小心{phrase}掉了／徹底{phrase}完"
    elif is_try:
        phrase = f"試著{phrase}{'看看' if not is_past else '了一下'}"
    elif is_prep:
        phrase = f"先預先{phrase}好"
    elif is_prog:
        if is_neg:
            phrase = f"沒有在{phrase}"
        elif is_past:
            phrase = f"當時正在{phrase}／{phrase}著"
        else:
            phrase = f"正在{phrase}／{phrase}著"
    elif is_cond:
        if is_neg:
            phrase = f"要是不{phrase}的話"
        else:
            phrase = f"要是{phrase}了的話"
    elif is_neg and is_past:
        phrase = f"沒有{phrase}／不{phrase}了"
    elif is_neg:
        phrase = f"不{phrase}／沒{phrase}"
    elif is_past:
        phrase = f"{phrase}了"
    elif is_te:
        phrase = f"{phrase}（並且／然後…）"
    else:
        return f"{clean_base}（句中活用：{' → '.join(reasons)}）"

    # 若原形詞義比單一 head 更豐富，附帶精簡原形對照（如「明白了（原形：明白、懂、理解）」）
    short_base = clean_base[:32] + ("…" if len(clean_base) > 32 else "")
    if phrase == head:
        return clean_base
    return f"{phrase}（原形「{lemma}」：{short_base}）"


_TRANSITIVE_CLUE_RE = re.compile(r"を[^。、！？]{0,12}$")
_GRADUAL_CLUE_RE = re.compile(r"(?:一方だ|ばかりだ|てくる|てきた|ていく|ていった|ますます|だんだん|次第に)")
_GREETING_CLUE_RE = re.compile(r"(?:お願い|よろしく|宜しく|はじめまして|初めまして)")

_S2T_CTX = str.maketrans(
    "这来为让给说听见现关开门间时书觉爱恋变强弱剧烈募集招募请指教照顾关照拜托明白懂理解知道",
    "這來為讓給說聽見現關開門間時書覺愛戀變強弱劇烈募集招募請指教照顧關照拜託明白懂理解知道",
)


def rank_senses_by_context(
    senses: list[str],
    sentence_translation: str = "",
    raw_sentence: str = "",
    target_surface: str = "",
) -> list[str]:
    """結合「整句譯文重疊度 ＋ 日語句法／自他動詞／搭配線索」，將最貼合本句語境的重點釋義排到第 1 位！"""
    if len(senses) <= 1:
        return list(senses)

    tr_norm = (sentence_translation or "").translate(_S2T_CTX).strip()
    tr_chars = set(re.findall(r"[\u4e00-\u9fff]", tr_norm))

    # 分析日語原句中目標詞前後的語境線索（即使完全沒開翻譯也能準確選義項！）
    has_wo_before = False
    has_gradual = bool(_GRADUAL_CLUE_RE.search(raw_sentence or ""))
    has_greeting = bool(_GREETING_CLUE_RE.search(raw_sentence or ""))
    if raw_sentence and target_surface:
        pos = raw_sentence.find(target_surface)
        if pos > 0:
            before = raw_sentence[max(0, pos - 14) : pos]
            has_wo_before = bool(_TRANSITIVE_CLUE_RE.search(before))

    def _score(idx_sense: tuple[int, str]) -> tuple[int, int]:
        idx, sense = idx_sense
        score = 0
        s_norm = sense.translate(_S2T_CTX)

        # 1. 與整句中文理解的字詞／雙字詞重疊（權重最高）
        if tr_chars:
            s_chars = set(re.findall(r"[\u4e00-\u9fff]", s_norm))
            overlap = len(tr_chars & s_chars)
            bigram_hits = 0
            zh_only = "".join(re.findall(r"[\u4e00-\u9fff]", s_norm))
            for i in range(len(zh_only) - 1):
                if zh_only[i : i + 2] in tr_norm:
                    bigram_hits += 1
            score += bigram_hits * 8 + overlap * 2

        # 2. 日語句法／搭配相關性線索
        if has_gradual and any(k in s_norm for k in ("強烈", "加劇", "越發", "變得", "越來越", "劇烈", "加深", "增強")):
            score += 7
        if has_wo_before and any(k in s_norm for k in ("募集", "招募", "徵集", "使", "讓", "把")):
            score += 5
        elif not has_wo_before and raw_sentence and any(k in s_norm for k in ("募集", "招募", "徵集")):
            score -= 3
        if has_greeting and any(k in s_norm for k in ("關照", "指教", "拜託", "照顧", "請多")):
            score += 8

        # 3. 適度偏好精煉簡短的核心義項（避免過長冷門條目搶占首位）
        if 2 <= len(sense) <= 24:
            score += 1

        return (-score, idx)

    ranked = sorted(enumerate(senses), key=_score)
    return [s for _, s in ranked]
