"""每一步以上一步的輸出為起點：包含 4 步主動邏輯推導、3 秒輕互動測驗、單變因實驗室與口語回應訓練。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from galgame_anki.core.verb_lesson import FORMS, SPOKEN_ENDINGS, build_lesson, lesson_for_token, verb_group
from galgame_anki.core.word_associations import TRANSITIVITY_PAIRS

if TYPE_CHECKING:
    from galgame_anki.core.grammar_engine import SentenceGrammarAnalysis
    from galgame_anki.core.study_map import Focus
    from galgame_anki.core.tokenizer import Token


@dataclass(frozen=True)
class Walk:
    start: str
    # (這一步要做什麼、為什麼、得到的形式或判斷)
    steps: tuple[tuple[str, str, str], ...]

    def before(self, index: int) -> str:
        return self.start if index == 0 else self.steps[index - 1][2]


@dataclass(frozen=True)
class QuizOption:
    """3 秒輕互動選擇項（先預測，再核對，強化主動語法推導）。"""

    label: str
    is_correct: bool
    feedback: str


@dataclass(frozen=True)
class LogicStep:
    """4 步主動邏輯推導中的單一步驟。"""

    step_no: int
    title: str
    input_text: str
    question: str
    options: tuple[QuizOption, ...]
    conclusion: str
    explanation: str


@dataclass(frozen=True)
class MutationVariant:
    """單變因實驗室：只改動 1 個語法／詞尾／自他變因，直觀比較句意位移。"""

    key: str
    label: str
    before_form: str
    after_form: str
    before_sentence: str
    after_sentence: str
    diff_note: str


@dataclass(frozen=True)
class SpokenDrill:
    """聽懂到會說的口語過渡提示（跟讀重點＋角色情境回應句）。"""

    shadow_tip: str
    context_prompt: str
    sample_replies: tuple[tuple[str, str], ...]  # (日語自然回應, 中文語意與語氣)


@dataclass(frozen=True)
class LogicPipeline:
    """單句完整的 4 步邏輯拆解＋單變因實驗＋口語回應練習資料。"""

    sentence: str
    focus_surface: str
    focus_lemma: str
    steps: tuple[LogicStep, ...]
    mutations: tuple[MutationVariant, ...]
    spoken_drill: SpokenDrill


_PAIR_MAP: dict[str, tuple[str, str]] = {}
for _vi, _vt in (("開く", "開ける"), ("閉まる", "閉める"), ("壊れる", "壊す"), ("落ちる", "落とす"), *TRANSITIVITY_PAIRS):
    _PAIR_MAP.setdefault(_vi, (_vt, "が"))
    _PAIR_MAP.setdefault(_vt, (_vi, "を"))


def conjugation_walk(lesson):
    lemma, surface = lesson.lemma, lesson.surface
    if lesson.form == "tara":
        ta = build_lesson(lemma, lesson.group, "ta")
        return Walk(lemma, (
            ("先造出能接「ら」的形態", f"「たら」需要た形。在這個詞中，把「{ta.old}」換成「{ta.new}」。", ta.surface),
            ("把事件設成條件", "在剛得到的た形後加「ら」。整組表示『如果……／等到……』，解讀要看後半句。", surface),
        ))
    if lesson.expanded:
        base = build_lesson(lemma, lesson.group, "nai" if lesson.form in ("nakya", "nakucha") else "te")
        steps = [("準備接續形", base.rule, base.surface)]
        expanded = lesson.expanded
        # 把「持續 → 過去 → 說明」拆開，使每一步只引入一個意思。
        if lesson.form in ("teta", "tetanda", "tenda", "terunda", "tenai", "tenakatta"):
            steps.append(("建立持續狀態", "接「いる」，表示正在進行或狀態持續。", base.surface + "いる"))
        if lesson.form in ("tetanda", "tenakatta"):
            mid_form = "いた" if lesson.form == "tetanda" else "いなかった"
            mid_desc = "いる 換成 いた，所說的是先前的狀態。" if lesson.form == "tetanda" else "いない 換成 いなかった，表示當時沒有持續。"
            steps.append(("把時間移到之前", mid_desc, base.surface + mid_form))
        purpose = FORMS[lesson.form][2]
        steps.append(("給動作加上這層意思", purpose, expanded))
        steps.append(("換成對白裡的說法", lesson.rule, surface))
        return Walk(lemma, tuple(steps))
    return Walk(lemma, (("依接法換詞尾", lesson.rule, surface),
                        ("讀出這個形式的作用", FORMS[lesson.form][2], FORMS[lesson.form][0])))


EXAMPLES = {
    "roles": Walk("猫が魚を食べる。", (
        ("找句尾", "先問『發生什麼動作』，找到食べる。", "食べる（吃）"),
        ("找誰吃什麼", "回到同一句，が標出貓，を標出魚。", "猫が ＋ 魚を ＋ 食べる → 貓吃魚"))),
    "form": Walk("帰る", (("換成已發生的形態", "帰る 是五段動詞，這裡的「る」換成「った」。", "帰った"),
                       ("放入時間", "和昨日一起讀，動作就落在昨天。", "昨日、帰った。"))),
    "negative": Walk("言う", (("先否定", "五段「う」接ない時換成「わ」。", "言わない"),
                           ("再放到過去", "將否定詞尾「ない」換成「なかった」。", "言わなかった（當時沒說）"))),
    "ability": Walk("言う", (("先表示能不能", "把「う」換成「える」，得到能說。", "言える"),
                          ("否定這份能力", "去掉可能動詞末尾的る，接ない。", "言えない"),
                          ("回顧當時", "ない換成なかった，否定的是當時說出口的能力或條件。", "言えなかった"))),
    "connect": Walk("待つ", (("先接成て形", "五段動詞「つ」在這裡換成「って」。", "待って"),
                         ("決定如何接下去", "接ください，把等待這個動作交給對方做。", "待ってください"))),
    "state": Walk("開く（あく）", (("準備接補助動詞", "在這裡把く換成いて。", "開いて"),
                              ("描述狀態", "接いる。配上窓が，表示打開後的狀態仍在。", "窓が開いている"))),
    "completion": Walk("読む", (("連接下一部分", "む在て形中變成んで。", "読んで"),
                            ("看完成結果", "在で後接しまった，把讀完這件事當作已發生的結果。", "読んでしまった"),
                            ("聽懂縮約", "でしまった在口語中縮成じゃった。", "読んじゃった"))),
    "prepare": Walk("読む", (("接成て形", "む的て形詞尾是んで。", "読んで"),
                         ("說明提前準備", "接おく，表示先讀好以備後用。", "読んでおく"),
                         ("換成口語", "把でおく縮成どく。", "読んどく"))),
    "condition": Walk("開く", (("先造た形", "這裡把く換成いた，得到能接ら的形態。", "開いた"),
                            ("提出條件", "在た形後加ら。", "開いたら"),
                            ("接後續動作", "前半句交代什麼時候才能做後面的事。", "ドアが開いたら、入って。"))),
    "stance": Walk("待っていた", (("說明事情的背景", "接のだ，讓先前一直在等成為說明。", "待っていたのだ"),
                             ("換成對白", "ていた縮成てた，のだ縮成んだ，再用よ告訴對方。", "待ってたんだよ"))),
    "word": Walk("書く（寫）", (("找寫的內容", "選「手紙」，用を把它接到書く。", "手紙を書く"),
                            ("測試搭配", "保留を和書く，替換被寫的內容。", "日記を書く"))),
    "pair": Walk("窓が開く", (("找變化的主體", "這裡只描述窗戶本身的變化，窓用が標出。", "窓が ＋ 開く"),
                            ("改從動作者看", "同一扇窗成了受作用的對象，が換成を，動詞換成開ける。", "窓を開ける"))),
    "compound": Walk("入学式", (("找右側核心", "末尾的式決定這是一種典禮。", "式（典禮）"),
                            ("再加限制", "用卒業限定典禮的場合，重新組成一個詞。", "卒業式（畢業典禮）"))),
    "register": Walk("遅れてごめん。", (("看雙方關係", "ごめん適合親近的對象。假設這次是向不熟的人致歉。", "需要較客氣的道歉"),
                                ("換詞，保留原因", "保留遅れて，將道歉表達換成すみません。", "遅れてすみません。"))),
    "feelings": Walk("想說出平常藏著的想法", (("先選名詞", "本音突出未說出口的真實想法。", "本音"),
                                  ("再接動作", "真心話是說出的內容，用を接到言う。", "本音を言う"))),
    "trend": Walk("不安が募る", (("找變化方向", "募る在這裡表示不安逐漸加深，使用原形。", "募る"),
                             ("加上持續的方向", "原形接一方だ，指出變化一直沿這個方向發展。", "不安は募る一方だ。"))),
    "idiom": Walk("息を殺す", (("先整組取義", "息を殺す合起來是屏住呼吸。", "息を殺す（屏住呼吸）"),
                           ("把語塊接到下一個動作", "只變結尾的殺す，換成殺して，便可接待つ。", "息を殺して待つ"))),
}

# 每條邊的文字解釋為什麼下一個概念要用到上一個，反查同一份資料即可得到後續用途。
REQUIRES = {
    "roles": (("word", "要先分出名詞和動作，才看得出助詞連接了哪兩部分。"),),
    "form": (("word", "先認出詞典原形，才能比較台詞裡換掉了哪個詞尾。"),),
    "negative": (("form", "ない須接在動詞相應的變形後。"),),
    "ability": (("form", "要先把『做』變成『能做』。"), ("negative", "還要能否定『能做』，才能讀出『沒能做』。")),
    "connect": (("form", "要選對て／で的接法，才接得上後面的動作。"),),
    "state": (("connect", "いる要接在前一個動作的て形後面。"),),
    "completion": (("connect", "還原ちゃった／じゃった時，會用到て／で和しまった的連接。"),),
    "prepare": (("connect", "要先接出て／で，才能理解後面的おく。"),),
    "condition": (("form", "先能造出た形，才有可接ら的起點。"), ("roles", "還要分清前後兩件事，才能判斷哪件是條件。")),
    "stance": (("roles", "先讀出發生什麼，再聽句尾多傳達了哪種態度。"),),
    "pair": (("word", "先知道兩個動詞描述同一類事件。"), ("roles", "再看名詞標的是變化主體還是動作對象。")),
    "compound": (("word", "先辨認構詞成分，才能從右側核心推回整個詞。"),),
    "register": (("word", "先找到意思相近的說法，再用雙方關係挑詞。"),),
    "feelings": (("word", "先比較名詞和動詞的搭配，再判斷要表達哪種感受。"),),
    "trend": (("form", "一方だ用原形承接表示變化的動詞。"),),
    "idiom": (("word", "先看哪些字固定一起用，才能決定要整組查哪個意思。"),),
}


def _replace_once(sentence: str, old: str, new: str) -> str:
    if not sentence or not old or old == new or old not in sentence:
        return new
    return sentence.replace(old, new, 1)


def _infer_verb_group(lemma: str, token: Token | None = None) -> str:
    if token is not None:
        g = verb_group(lemma, token.verb_type)
        if g:
            return g
        if token.lookup:
            for e in token.lookup.all_entries:
                g = verb_group(lemma, e.pos)
                if g:
                    return g
    g = verb_group(lemma, "")
    if g:
        return g
    if lemma in ("食べる", "開ける", "閉める", "壊れる", "見る", "決める", "見つける", "始める", "終える", "落ちる", "忘れる", "教える", "覚える"):
        return "v1"
    if lemma and lemma[-1] in "うつるむぶぬくぐす":
        return "v5"
    return ""


def build_mutations(
    sentence: str,
    lemma: str,
    surface: str,
    group: str = "",
) -> tuple[MutationVariant, ...]:
    """只改動單一變因（肯定/否定、現在/過去、可能形、自他動詞、口語/書面、條件/讓步），產生精確對照實驗。"""
    muts: list[MutationVariant] = []
    if not lemma:
        return ()
    grp = group or _infer_verb_group(lemma)
    base_surf = surface or lemma
    base_sent = sentence or base_surf

    def _add(key: str, label: str, after_form: str, diff_note: str, after_sent: str | None = None) -> None:
        if not after_form or after_form == base_surf:
            return
        if any(m.key == key for m in muts):
            return
        s_after = after_sent if after_sent is not None else _replace_once(base_sent, base_surf, after_form)
        muts.append(
            MutationVariant(
                key=key,
                label=label,
                before_form=base_surf,
                after_form=after_form,
                before_sentence=base_sent,
                after_sentence=s_after,
                diff_note=diff_note,
            )
        )

    if grp:
        is_neg = any(k in base_surf for k in ("ない", "なかっ", "ません", "じゃだめ", "ちゃだめ"))
        is_past = any(base_surf.endswith(k) for k in ("た", "だ", "ました", "かった", "たんだ", "だんだ"))

        # 1) 肯定 ↔ 否定
        if is_neg:
            aff_form = build_lesson(lemma, grp, "ta" if is_past else "masu")
            target_f = aff_form.surface if (aff_form and is_past) else lemma
            _add("affirm_neg", "否定 → 肯定", target_f, "抽掉否定層，動作從「沒做／不做」翻轉為「做了／做」。")
        else:
            neg_l = build_lesson(lemma, grp, "nakatta" if is_past else "nai")
            if neg_l:
                _add("affirm_neg", "肯定 → 否定", neg_l.surface, "只加上否定詞尾，其餘主語與受詞完全不變，整句翻成否定。")

        # 2) 現在 ↔ 過去
        if is_past:
            pres_l = build_lesson(lemma, grp, "nai") if is_neg else None
            target_p = pres_l.surface if pres_l else lemma
            _add("present_past", "過去 → 非過去", target_p, "抽掉過去標記「た／かった」，時間從回顧移回現在或將來。")
        else:
            past_l = build_lesson(lemma, grp, "nakatta" if is_neg else "ta")
            if past_l:
                _add("present_past", "非過去 → 過去", past_l.surface, "只把時態換成過去形，動作落定為「已經發生過的事」。")

        # 3) 可能形（能不能做到）
        pot_l = build_lesson(lemma, grp, "potential")
        if pot_l:
            _add("potential", "能力／可能形", pot_l.surface, "把「做不做」推進成「能不能做到」（常搭配助詞「が」標示對象）。")

        # 4) 自動詞 ↔ 他動詞（切換視角並同步翻轉 が ↔ を）
        if lemma in _PAIR_MAP:
            partner, expected_p = _PAIR_MAP[lemma]
            partner_grp = _infer_verb_group(partner)
            new_p = "を" if expected_p == "が" else "が"
            # 若原句是特定詞形（例如 壊れたら），盡量將對應自他動詞也變成同一詞形！
            partner_form = partner
            if partner_grp and base_surf != lemma:
                for fkey in ("tara", "ta", "te", "nai", "teiru", "teru", "teta", "masu"):
                    orig_l = build_lesson(lemma, grp, fkey)
                    if orig_l and orig_l.surface == base_surf:
                        p_l = build_lesson(partner, partner_grp, fkey)
                        if p_l:
                            partner_form = p_l.surface
                        break
            swapped_sent = _replace_once(base_sent, base_surf, partner_form)
            if f"{expected_p}{partner_form}" in swapped_sent:
                swapped_sent = swapped_sent.replace(f"{expected_p}{partner_form}", f"{new_p}{partner_form}", 1)
            elif expected_p in swapped_sent:
                swapped_sent = swapped_sent.replace(expected_p, new_p, 1)
            perspective_note = (
                f"從自動詞「{lemma}（事物自己變化，配「が」）」改為他動詞「{partner}（某人動手操作，配「を」）」。"
                if expected_p == "が"
                else f"從他動詞「{lemma}（有人施力操作，配「を」）」改為自動詞「{partner}（只描述事物本身變化，配「が」）」。"
            )
            _add("transitivity", "自動 ↔ 他動", partner_form, perspective_note, after_sent=swapped_sent)

        # 5) 口語 ↔ 書面／敬體
        matched_spoken = None
        for skey in SPOKEN_ENDINGS:
            sl = build_lesson(lemma, grp, skey)
            if sl and sl.surface == base_surf:
                matched_spoken = sl
                break
        if matched_spoken and matched_spoken.expanded:
            _add("spoken_written", "口語 → 書面完整形", matched_spoken.expanded,
                 f"還原口語省音：「{base_surf}」完整寫法為「{matched_spoken.expanded}」，語意相同但更正式清晰。")
        else:
            masu_l = build_lesson(lemma, grp, "masu_past" if is_past else "masu")
            if masu_l:
                _add("spoken_written", "普通體 → 禮貌體", masu_l.surface,
                     "只將句尾改為ます系敬體，事實完全相同，但語氣從親近口語轉為對外禮貌。")

        # 6) 條件（〜たら） ↔ 讓步（〜ても）
        te_l = build_lesson(lemma, grp, "te")
        tara_l = build_lesson(lemma, grp, "tara")
        if base_surf.endswith(("たら", "だら")) and te_l:
            _add("cond_concess", "條件 → 讓步", te_l.surface + "も",
                 "把「〜たら（如果發生，就…）」改成「〜ても（就算發生，依然…）」，前後邏輯從條件反轉成讓步。")
        elif tara_l:
            _add("cond_concess", "陳述 → 假設條件", tara_l.surface,
                 "只加上「〜たら」，把這件事變成前提條件「如果…的話」，後句接著交代結果。")

    return tuple(muts[:6])


def _build_spoken_drill(sentence: str, focus_surface: str, focus_lemma: str) -> SpokenDrill:
    """從這句實際台詞產出 10 秒「跟讀提示＋口語即時回應」練習，把聽懂轉成能開口。"""
    s = (sentence or "").strip()
    is_question = s.endswith(("？", "?", "か", "か。", "の", "の？", "かな"))
    has_spoken_drop = any(k in s for k in ("てる", "てた", "てない", "ちゃう", "ちゃった", "じゃった", "とく", "といて", "なきゃ", "ちゃだめ"))
    is_command = any(k in s for k in ("てろ", "でろ", "ください", "くれ", "なさい", "ちゃだめ", "じゃだめ"))
    is_cond = any(k in s for k in ("たら", "だら", "れば", "なら"))

    if has_spoken_drop:
        shadow_tip = "聽原聲時特別留意口語省音節奏（如 てる／ちゃった／なきゃ 連讀不停頓），試著不看字幕跟著念 2 遍。"
    elif is_question:
        shadow_tip = "疑問句尾音上揚（の？／か？）；聽完立刻按暫停，在 2 秒內用簡短日語口頭回一句。"
    else:
        shadow_tip = "先盲聽原聲 1 遍抓動詞與句尾語氣，再邊跟讀邊把焦點詞替換成你自己的生活情境。"

    if is_question:
        return SpokenDrill(
            shadow_tip=shadow_tip,
            context_prompt="對方正在向你提問——練習先用口語簡答，再用禮貌體完整回答：",
            sample_replies=(
                ("うん、そうだよ。／ううん、違うよ。", "對朋友自然口語（是喔／不是喔）"),
                ("はい、そうです。／いいえ、まだ分かりません。", "對不熟的人禮貌回應（是的／還不清楚）"),
            ),
        )
    if is_command:
        return SpokenDrill(
            shadow_tip=shadow_tip,
            context_prompt="對方正在要求或制止你——練習口頭回應（接受或婉拒）：",
            sample_replies=(
                ("分かった、今やるよ。", "口語爽快答應（知道了，我現在就做）"),
                ("すみません、少し待ってください。", "禮貌緩衝請求（不好意思，請稍等一下）"),
            ),
        )
    if is_cond:
        return SpokenDrill(
            shadow_tip=shadow_tip,
            context_prompt="這句提出了假設條件——練習順著這個條件，用日語接出你的結論：",
            sample_replies=(
                ("その時は、すぐ連絡するよ。", "接條件的口語回應（到時候我馬上聯絡你）"),
                ("もしそうなったら、どうしましょうか。", "禮貌反問對策（萬一真的那樣，我們該怎麼辦呢？）"),
            ),
        )
    return SpokenDrill(
        shadow_tip=shadow_tip,
        context_prompt=f"聽完這句話後，假設你在現場對話中——用「{focus_lemma or focus_surface or '這句'}」自然接話：",
        sample_replies=(
            ("本当に？それは大変だね。", "自然附和與共鳴（真的嗎？那真不容易呢）"),
            ("なるほど、そういうことですか。", "禮貌確認理解（原來如此，是這麼一回事啊）"),
        ),
    )


def build_logic_pipeline(
    sentence: str,
    token: Token | None = None,
    tokens: tuple[Token, ...] | list[Token] = (),
    focus: Focus | None = None,
    analysis: SentenceGrammarAnalysis | None = None,
    concept_key: str = "",
    lemma: str = "",
) -> LogicPipeline:
    """組裝「4 步主動邏輯推導（每步附 3 秒輕互動選擇）＋ 單變因實驗室 ＋ 口語聽說提示」。"""
    sent = (sentence or "").strip()
    surf = token.surface if token is not None else ""
    eff_lemma = (token.lemma if token is not None else "") or lemma
    if not eff_lemma and focus is not None and getattr(focus, "walk", None) is not None:
        eff_lemma = focus.walk.start
    if not surf and focus is not None and getattr(focus, "walk", None) is not None:
        for _why, _change, step_result in reversed(focus.walk.steps):
            if step_result and step_result in sent:
                surf = step_result
                break
    if not surf:
        surf = eff_lemma
    lemma = eff_lemma or surf
    grp = _infer_verb_group(lemma, token)
    if token is not None:
        lesson = lesson_for_token(token)
    elif surf and lemma and grp:
        from galgame_anki.core.tokenizer import Token as _Token

        syn_tok = _Token(surf, lemma, "", 0, len(surf), verb_type=grp)
        lesson = lesson_for_token(syn_tok)
    else:
        lesson = None

    # === Step 1: 定主幹（找核心結論／述語） ===
    pred_text = ""
    topic_text = ""
    if analysis is not None:
        pred_text = analysis.skeleton_predicate or ""
        topic_text = analysis.skeleton_topic or ""
    if not pred_text:
        if surf and grp:
            pred_text = surf
        elif tokens:
            non_punct = [t for t in tokens if not t.is_punctuation]
            pred_text = non_punct[-1].surface if non_punct else sent
        else:
            pred_text = sent

    pred_clean = pred_text.split("（", 1)[0].strip() or pred_text
    s1_q = "日語邏輯『結論在句尾』——這句話真正決定核心動作或結論的是哪一段？"
    other_toks = [t.surface for t in tokens if not t.is_punctuation and t.surface != pred_clean and len(t.surface) >= 2]
    d1_a = other_toks[0] if other_toks else "句首的主題或時間詞"
    d1_b = "最前面的助詞（は／が）"
    s1_opts = (
        QuizOption(pred_clean, True, f"答對！先鎖定句尾核心「{pred_clean}」，整句的動作方向與時態就定下來了。"),
        QuizOption(d1_a, False, f"「{d1_a}」是前面補充對象或背景，真正的結論要看句尾「{pred_clean}」。"),
        QuizOption(d1_b, False, "助詞只負責標示角色關係，動作或判斷本身要找句尾述語。"),
    )
    step1 = LogicStep(
        step_no=1,
        title="1. 定主幹",
        input_text=sent or (surf or "示範句"),
        question=s1_q,
        options=s1_opts,
        conclusion=f"{topic_text or '（省略主語）'} ──▶ {pred_text}",
        explanation="讀日語長句與聽力抓重點的第一步：跳過中間修飾，直接鎖定句尾核心述語。",
    )

    # === Step 2: 剝詞尾（原形 → 活用／縮約層次） ===
    if lesson is not None:
        role_ans = FORMS[lesson.form][0]
        form_name = FORMS[lesson.form][1]
        walk_chain = f"{lesson.lemma} → " + (f"{lesson.expanded} → " if lesson.expanded else "") + lesson.surface
        distractor_pool = [
            ("否定（不做）", "這層詞尾不是否定形。"),
            ("假設條件（如果…）", "這層詞尾不是條件假設。"),
            ("持續進行（正在…）", "這層詞尾不是持續進行。"),
            ("過去已完成", "這層詞尾不是單純過去式。"),
            ("禁止（不可以…）", "這層詞尾不是禁止語氣。"),
        ]
        picked_d = [
            QuizOption(lbl, False, f"再看一次詞尾：「{lesson.surface}」屬於 {form_name}（{FORMS[lesson.form][2]}）。")
            for lbl, _ in distractor_pool
            if lbl[:2] not in role_ans and lbl[:2] not in form_name
        ][:2]
        s2_opts = (
            QuizOption(f"{form_name}（{role_ans}）", True, f"沒錯！{lesson.rule} {FORMS[lesson.form][2]}"),
            *picked_d,
        )
        step2 = LogicStep(
            step_no=2,
            title="2. 剝詞尾",
            input_text=lesson.surface,
            question=f"把「{lesson.surface}」還原回原形「{lesson.lemma}」，這個詞尾加上了哪一層邏輯？",
            options=s2_opts,
            conclusion=f"{walk_chain}（{form_name}）",
            explanation=f"{lesson.rule} {FORMS[lesson.form][2]}",
        )
    elif token is not None and token.lookup and token.lookup.reasons and surf != lemma:
        reasons_txt = " → ".join(token.lookup.reasons)
        s2_opts = (
            QuizOption(reasons_txt, True, f"正確！「{surf}」由原形「{lemma}」逐層疊上：{reasons_txt}。"),
            QuizOption("單純名詞原形（無活用）", False, f"這不是原形，而是由「{lemma}」變化而來。"),
            QuizOption("純語氣終助詞", False, "詞尾承載了實質語法層次，不只是語氣詞。"),
        )
        step2 = LogicStep(
            step_no=2,
            title="2. 剝詞尾",
            input_text=surf,
            question=f"「{surf}」是從原形「{lemma}」變來的，它疊上了哪層活用？",
            options=s2_opts,
            conclusion=f"{lemma} → {surf}（{reasons_txt}）",
            explanation="由外往內一層一層剝除詞尾，就能瞬間看穿複雜的否定、可能與過去組合。",
        )
    else:
        target_label = surf or lemma or pred_clean
        s2_opts = (
            QuizOption("已為辭書原形／基本單位", True, f"「{target_label}」本身已是基本形式，直接核對詞典語境與前後搭配。"),
            QuizOption("口語省略了「い」的進行形", False, "此處沒有「てる／てた」縮約結構。"),
            QuizOption("五段可能形＋過去否定", False, "此處沒有疊加可能與過去否定詞尾。"),
        )
        step2 = LogicStep(
            step_no=2,
            title="2. 剝詞尾",
            input_text=target_label,
            question=f"觀察焦點詞「{target_label}」，它目前處於什麼形態？",
            options=s2_opts,
            conclusion=f"{lemma or target_label}（基本單位）",
            explanation="先確認目標詞是原形還是活用形，才不會查錯詞頭或漏掉否定／條件層。",
        )

    # === Step 3: 辨助詞與視角 ===
    particle_found = ""
    for p_cand in ("を", "が", "に", "で", "は", "と", "から", "のに", "けど"):
        if p_cand in sent:
            particle_found = p_cand
            break
    if lemma in _PAIR_MAP:
        partner, exp_p = _PAIR_MAP[lemma]
        is_vi = exp_p == "が"
        correct_lbl = f"自動詞視角（配「が」描述事物自身變化）" if is_vi else f"他動詞視角（配「を」描述有人對對象施力）"
        wrong_lbl = f"他動詞視角（有人刻意去{partner}）" if is_vi else f"自動詞視角（事物自然自己{partner}）"
        s3_opts = (
            QuizOption(correct_lbl, True, f"準確！「{lemma}」與「{partner}」是一組自他動詞，助詞與視角會同步連動。"),
            QuizOption(wrong_lbl, False, f"剛好相反！那是對應詞「{partner}」的視角。"),
            QuizOption("場所手段標記（與自他無關）", False, "此處核心差異在於事件是否帶有明確施動者（自他視角）。"),
        )
        step3 = LogicStep(
            step_no=3,
            title="3. 辨助詞與視角",
            input_text=step2.conclusion,
            question=f"這句選用「{lemma}」而不是「{partner}」，說話者採取了哪一種視角？",
            options=s3_opts,
            conclusion=f"{lemma}（{'自・が' if is_vi else '他・を'}） ↔ {partner}（{'他・を' if is_vi else '自・が'}）",
            explanation="日語非常重視「自然發生（自動詞＋が）」vs「人為操作（他動詞＋を）」的視角區分。",
        )
    elif particle_found:
        p_desc_map = {
            "を": ("標示他動詞作用的對象（受詞）", "標示動作發生的地點", "標示比較基準"),
            "が": ("標示動作主體或變化焦點（主語）", "標示動作使用的工具", "標示時間起點"),
            "に": ("標示時間點、落點或動作對象", "標示直接受詞", "標示並列舉例"),
            "で": ("標示動作場所、工具或手段", "標示存在位置（ある／いる）", "標示主題對比"),
            "は": ("標示全句討論主題或前後對比", "標示直接受詞", "標示工具手段"),
            "と": ("標示引用內容、共同對象或必然條件", "標示移動終點", "標示讓步逆接"),
            "から": ("標示起點或主觀原因理由", "標示動作受詞", "標示禮貌敬語"),
            "のに": ("標示事與願違的意外逆接（明明…卻…）", "標示動作時間點", "標示並列舉例"),
            "けど": ("標示轉折或委婉鋪陳（雖然…但是…）", "標示強制命令", "標示工具手段"),
        }
        good_p, bad_p1, bad_p2 = p_desc_map[particle_found]
        s3_opts = (
            QuizOption(good_p, True, f"答對！助詞「{particle_found}」負責把前面的名詞／子句精準掛載到句尾述語。"),
            QuizOption(bad_p1, False, f"這不是「{particle_found}」在這句的任務；它實際上{good_p}。"),
            QuizOption(bad_p2, False, f"記住「{particle_found}」的核心職責：{good_p}。"),
        )
        step3 = LogicStep(
            step_no=3,
            title="3. 辨助詞與視角",
            input_text=step2.conclusion,
            question=f"看句中的助詞「{particle_found}」——它把前面的詞掛載成什麼邏輯角色？",
            options=s3_opts,
            conclusion=f"助詞「{particle_found}」：{good_p}",
            explanation="每個助詞就像程式碼的參數名稱；認清助詞，語序再怎麼變動都不會讀反主客關係。",
        )
    else:
        s3_opts = (
            QuizOption("日語口語常省略已知的「你／我」主語", True, "沒錯！對話中主語常由語境與敬語／授受方向暗示，不必每次說出口。"),
            QuizOption("這句話缺少了必要的動詞", False, "句中核心述語仍在，只是省略了說話雙方已知的主語。"),
            QuizOption("所有名詞都自動變成受詞", False, "受詞必須靠語境或「を」判斷，不會自動轉換。"),
        )
        step3 = LogicStep(
            step_no=3,
            title="3. 辨助詞與視角",
            input_text=step2.conclusion,
            question="這句沒有明寫主語與多餘格助詞，對話聽力中應如何補回視角？",
            options=s3_opts,
            conclusion="結合場景與句尾語氣，自動補回省略的「我／你」",
            explanation="真實日語對話大量省略「私は／君は」，要靠動詞視角與句尾態度即時鎖定主語。",
        )

    # === Step 4: 單變因實驗與口語聽說轉換 ===
    mutations = build_mutations(sent, lemma or "食べる", surf or (lemma or "食べる"), grp)
    if mutations:
        m0 = mutations[0]
        s4_opts = (
            QuizOption(f"換成「{m0.after_form}」：{m0.label}", True, m0.diff_note),
            QuizOption("整句意思完全不變，只是寫法不同", False, f"這不只是寫法差異！{m0.diff_note}"),
            QuizOption("句子會失去原本的動作含義", False, f"詞幹動作「{lemma}」不變，只改變了單一語法變因：{m0.label}。"),
        )
        step4 = LogicStep(
            step_no=4,
            title="4. 單變因驗證",
            input_text=step3.conclusion,
            question=f"單變因實驗：如果只做「{m0.label}」（{m0.before_form} → {m0.after_form}），句意會如何變化？",
            options=s4_opts,
            conclusion=f"{m0.before_sentence}  ⇒  {m0.after_sentence}",
            explanation=m0.diff_note + " 點下方其他變因按鈕可繼續做最小對比實驗。",
        )
    else:
        step4 = LogicStep(
            step_no=4,
            title="4. 單變因驗證",
            input_text=step3.conclusion,
            question="把這句內化成聽說能力時，下一步最有效的是哪個動作？",
            options=(
                QuizOption("盲聽原聲 1 遍 → 替換 1 個名詞造出自己的口語回應", True, "正確！固定句型骨架、只替換 1 個實詞，能最快把『看懂』變成『聽懂並答出』。"),
                QuizOption("把整句死背成中文逐字翻譯", False, "死背中文翻譯會拖慢聽力反應速度；記住日語骨架與語感遷移才有效。"),
                QuizOption("略過聲優原聲只看文字", False, "聽懂日本人說話必須靠聲音把語調、縮約與句尾態度連在一起。"),
            ),
            conclusion="固定骨架，替換 1 個實詞並開口跟讀回應",
            explanation="結合上方「聽原聲」與下方口語回應提示，把單句讀懂轉化成真實對話反射。",
        )

    drill = _build_spoken_drill(sent, surf, lemma)
    return LogicPipeline(
        sentence=sent,
        focus_surface=surf or lemma,
        focus_lemma=lemma or surf,
        steps=(step1, step2, step3, step4),
        mutations=mutations,
        spoken_drill=drill,
    )
