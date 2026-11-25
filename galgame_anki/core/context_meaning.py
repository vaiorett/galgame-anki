"""語境感知詞義轉換與義項排序引擎（看這句話的當下意思，不再硬搬孤立詞典原形！）。"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from galgame_anki.core.tokenizer import Token


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


def context_gloss_is_chinese(meaning: str) -> bool:
    # 英文義項也能附上中文活用標籤。判斷語言時應看釋義本身。
    return bool(re.search(r"[\u4e00-\u9fff]", meaning.partition("（句中活用：")[0]))


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
_BOW_CLUE_RE = re.compile(r"(?:深々|ふかぶか|丁寧|礼|お辞儀|挨拶|頭を下げ)")
_SPEECH_CLUE_RE = re.compile(r"(?:話|会話|発言|議論|説明|スピーチ)")
_PERSON_NI_RE = re.compile(r"([\u4e00-\u9fffァ-ヶー]{1,6})に$")

_S2T_CTX = str.maketrans(
    "这来为让给说听见现关开门间时书觉爱恋变强弱剧烈募集招募请指教照顾关照拜托明白懂理解知道国统领贵断弯",
    "這來為讓給說聽見現關開門間時書覺愛戀變強弱劇烈募集招募請指教照顧關照拜託明白懂理解知道國統領貴斷彎",
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
    has_bow_clue = bool(_BOW_CLUE_RE.search(raw_sentence or ""))
    has_speech_clue = bool(_SPEECH_CLUE_RE.search(raw_sentence or ""))
    is_kimi_dialogue = False
    if raw_sentence and target_surface:
        pos = raw_sentence.find(target_surface)
        if pos > 0:
            before = raw_sentence[max(0, pos - 14) : pos]
            has_wo_before = bool(_TRANSITIVE_CLUE_RE.search(before))
        if target_surface == "君" and pos >= 0:
            after = raw_sentence[pos + 1 : pos + 3]
            if after and after[0] in ("は", "が", "の", "を", "に", "も", "っ", "た", "、", "？", "！", "?"):
                is_kimi_dialogue = True

    def _score(idx_sense: tuple[int, str]) -> tuple[int, int]:
        idx, sense = idx_sense
        score = 0
        s_norm = sense.translate(_S2T_CTX)
        s_low = sense.lower()

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

        # 2. 具體上下文與慣用搭配線索（優先於來源順序）
        if is_kimi_dialogue:
            if s_norm in ("你", "妳") or "you" in s_low or "二人称" in s_norm:
                score += 12
            elif any(k in s_norm for k in ("統治者", "天皇", "君主", "領主", "卵黄", "卵黃", "妓女")):
                score -= 10
        if target_surface == "君主":
            if s_norm == "君主" or any(k in s_norm for k in ("統治者", "monarch", "ruler", "sovereign")):
                score += 10
            elif "君主國" in s_norm or "monarchy" in s_low:
                score -= 10
        if "さすがに" in (target_surface or "") or ("さすがに" in (raw_sentence or "") and "さすがの" not in (raw_sentence or "")):
            if any(k in s_norm for k in ("畢竟", "的確", "果然", "不愧", "還是")) or any(
                k in s_low for k in ("expect", "naturally", "indeed", "after all")
            ):
                score += 9
            elif "即使" in s_norm and "畢竟" not in s_norm:
                score -= 4
        if "腰を折" in (target_surface or "") or "腰を折" in (raw_sentence or ""):
            if has_bow_clue:
                if any(k in s_low for k in ("bend", "waist", "彎腰", "鞠躬")):
                    score += 12
                elif any(k in s_low for k in ("interrupt", "interfere", "打斷")):
                    score -= 8
            elif has_speech_clue:
                if any(k in s_low for k in ("interrupt", "interfere", "butt in", "打斷")):
                    score += 12
                elif any(k in s_low for k in ("bend", "waist", "彎腰")):
                    score -= 8
        if has_gradual and any(k in s_norm for k in ("強烈", "加劇", "越發", "變得", "越來越", "劇烈", "加深", "增強")):
            score += 7
        if has_wo_before and any(k in s_norm for k in ("募集", "招募", "徵集", "使", "讓", "把")):
            score += 5
        elif not has_wo_before and raw_sentence and any(k in s_norm for k in ("募集", "招募", "徵集")):
            score -= 3
        if has_greeting and any(k in s_norm for k in ("關照", "指教", "拜託", "照顧", "請多")):
            score += 8

        # 3. 保護 1 字核心詞（如「你」「狗」），只略微偏好 1~24 字精簡核心義項
        if 1 <= len(sense) <= 24:
            score += 1

        return (-score, idx)

    ranked = sorted(enumerate(senses), key=_score)
    return [s for _, s in ranked]


def is_otherwise_clause(sentence: str, start: int) -> bool:
    """只把有獨立分句線索的「じゃないと」解成轉折，保留否定句與引用的原本分析。"""
    phrase = "じゃないと"
    if not sentence.startswith(phrase, start):
        return False
    before = sentence[:start].rstrip()
    after = sentence[start + len(phrase):].lstrip()
    if before and not before.endswith(("てろ", "でろ", "しろ", "なさい", "。", "！", "!", "」")):
        return False
    return bool(re.match(r"[^。、！？!?」』\s]", after) and
                not re.match(r"(?:思|言|感じ|考え|いう|い[うっ]|いけな|ならな)", after))


def explain_lookup_in_sentence(token: "Token", sentence: str, translation: str) -> tuple[str, str]:
    """將所選詞或語塊的義項與可從原句判斷的用法分開，優先台灣正體並保留句尾省略的歧義。"""
    from galgame_anki.core.dictionary import normalize_zh_tw

    match = token.lookup
    by_lang = match.senses_by_language(max_per_lang=6, convert_tw=True) if match else {"zh": [], "ja": [], "en": []}
    zh_senses = [s.text for s in by_lang["zh"]]
    en_senses = [s.text for s in by_lang["en"]]
    senses = zh_senses if zh_senses else en_senses
    ranked = rank_senses_by_context(senses, translation, sentence, token.surface)
    meaning = ranked[0] if ranked else ""
    reasons = match.reasons if match else ()
    lemma = token.lemma
    before = sentence[:token.start].rstrip()
    after = sentence[token.end:].lstrip()

    if lemma == "壊れる":
        subject = re.search(
            r"(?<![一-龯])(地球|世界|惑星|機械|装置|テレビ|スマホ|パソコン|冷蔵庫|計画|予定|交渉|関係|コップ|グラス|皿|花瓶)\s*[がは]$",
            before,
        )
        if subject:
            noun = subject[1]
            if noun in ("地球", "世界", "惑星"):
                if token.surface.endswith("たら"):
                    return f"如果{('行星' if noun == '惑星' else noun)}毀了……", "「〜たら」先提出假設，後半句才會交代結果。"
                meaning = "毀壞、毀滅"
            elif noun in ("機械", "装置", "テレビ", "スマホ", "パソコン", "冷蔵庫"):
                meaning = "故障"
            elif noun in ("計画", "予定", "交渉", "関係"):
                meaning = "破裂" if noun in ("交渉", "関係") else "告吹"
            else:
                meaning = "破碎"
        else:
            meaning = "損壞"
    if token.surface == "さすがに" and re.match(r"(?:それは|これは|もう)?\s*(?:無理|むり|難し|厳し)", after):
        return "實在", "這裡是說情況已超過能應付的程度。例句可理解成「這實在辦不到」。"
    if token.surface == "じゃないと" and is_otherwise_clause(sentence, token.start):
        return "要不然……", "前面交代一件事 → じゃないと → 後面說明不照做的後果。"
    if token.surface == "とか" and token.is_function_word:
        left = re.search(r"([ァ-ヶー]+)$", before)
        if left:
            return f"像「{left[1]}」之類的", f"{left[1]} ＋ とか → 把「{left[1]}」當作其中一個例子。"
    if lemma in ("気を遣う", "気を使う"):
        pm = _PERSON_NI_RE.search(before)
        if pm:
            person = pm.group(1)
            base_idiom = f"顧慮{person}／留意{person}的感受"
            note = f"{person}に ＋ {lemma} → 在意「{person}」的想法、處處留意對方感受。"
        else:
            base_idiom = "顧慮、留意對方的感受、費心"
            note = f"慣用語「{lemma}」→ 把心思放在對方身上、顧慮對方感受。"
        if reasons and token.surface != lemma:
            return f"{base_idiom}（{' → '.join(reasons)}）", note
        return base_idiom, note
    if lemma in ("話の腰を折る", "腰を折る"):
        if lemma == "腰を折る" and _BOW_CLUE_RE.search(sentence):
            return (
                format_contextual_meaning(token.surface, lemma, "彎腰（鞠躬致意）", reasons),
                "搭配「深々／礼」等詞時指實際彎下腰鞠躬，而非打斷說話。",
            )
        if lemma == "話の腰を折る" or _SPEECH_CLUE_RE.search(sentence):
            return (
                format_contextual_meaning(token.surface, lemma, "打斷話頭、插話打斷", reasons),
                "慣用語「（話の）腰を折る」→ 中途打斷對方正在說的話或興致。",
            )
    if token.surface.endswith(("てろ", "でろ")) and token.surface != lemma and token.verb_type:
        return meaning, "「いろ」是「いる」的命令形，口語中省掉「い」。說話者強硬地要求對方繼續這個動作或保持這個狀態。"
    if lemma == "殺す":
        for obj, gloss in (("息", "屏住呼吸"), ("声", "壓低聲音"), ("足音", "放輕腳步")):
            if before.endswith(obj + "を"):
                meaning = f"{obj}を殺す（{gloss}）"
                break
    elif lemma in ("息を殺す", "声を殺す", "足音を殺す"):
        gloss_map = {"息を殺す": "屏住呼吸", "声を殺す": "壓低聲音", "足音を殺す": "放輕腳步"}
        meaning = f"{lemma}（{gloss_map[lemma]}）"

    if token.is_te_form or (token.surface.endswith(("て", "で")) and "て形" in reasons):
        if after.startswith(("ください", "下さい", "ちょうだい")) or re.match(r"くれ(?:[よね]?[。！？!?…」』]|$)", after):
            role = "後接請求表達，說話者請對方做這個動作。"
        elif after.startswith(("ほしい", "欲しい")):
            role = "接「ほしい」，表示希望別人做這個動作。"
        elif after.startswith("も"):
            role = "接「も」，表示即使做了這件事，後面的情況仍然成立。"
        elif after.startswith(("くれた", "くれる")):
            role = "接「くれる」，說話者把動作看成別人為自己或己方做的事。"
        elif after.startswith("から"):
            role = "接「から」，表示先完成這個動作，再進行後面的事。"
        elif after.startswith(("いる", "いた", "います")):
            role = "接「いる」，可表示動作持續或結果狀態。"
        elif not after.strip("。！？!?…」』 	") or re.fullmatch(r"[よね]+[。！？!?…」』]*", after):
            role = "停在句尾的て形，可能是在請求對方，也可能是話還沒說完。"
        else:
            role = "連到後面的敘述；可表示先後動作，或說明後一動作如何進行。"
        rule = "五段動詞，す → して" if lemma.endswith("す") and token.surface == lemma[:-1] + "して" else "て形"
        return meaning, f"{lemma} → {token.surface}（{rule}）\n{role}"
    meaning = format_contextual_meaning(token.surface, lemma, meaning, reasons)
    if meaning and zh_senses:
        meaning = normalize_zh_tw(meaning)
    return meaning, ""
