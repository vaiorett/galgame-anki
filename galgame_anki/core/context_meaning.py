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


def rank_senses_by_context(senses: list[str], sentence_translation: str) -> list[str]:
    """當已有整句中文參考理解時，依語境重疊度將最貼合本句的詞典義項自動排到第 1 位！"""
    if not senses or not sentence_translation.strip():
        return senses

    tr_chars = set(re.findall(r"[\u4e00-\u9fff]", sentence_translation))
    if not tr_chars:
        return senses

    def _score(idx_sense: tuple[int, str]) -> tuple[int, int]:
        idx, sense = idx_sense
        s_chars = set(re.findall(r"[\u4e00-\u9fff]", sense))
        overlap = len(tr_chars & s_chars)
        # 雙字連續詞命中加倍權重（如「明白」同時出現在譯文與義項中）
        bigram_hits = 0
        zh_only = "".join(re.findall(r"[\u4e00-\u9fff]", sense))
        for i in range(len(zh_only) - 1):
            if zh_only[i : i + 2] in sentence_translation:
                bigram_hits += 1
        return (-(bigram_hits * 4 + overlap), idx)

    ranked = sorted(enumerate(senses), key=_score)
    return [s for _, s in ranked]
