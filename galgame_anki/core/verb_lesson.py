"""從原形與詞典類別推導常見詞形。只有表面形完整吻合時才解釋實際台詞。"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from html import escape
import re

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from galgame_anki.core.tokenizer import Token


# 原形、詞典類別、中文動作（用於藍圖的可切換例句）
EXAMPLES = (
    ("食べる", "v1", "吃"), ("帰る", "v5", "回家"), ("見る", "v1", "看"),
    ("話す", "v5", "說話"), ("殺す", "v5", "殺死"), ("書く", "v5", "寫"),
    ("泳ぐ", "v5", "游泳"), ("読む", "v5", "讀"), ("待つ", "v5", "等"),
    ("買う", "v5", "買"), ("遊ぶ", "v5", "玩"), ("死ぬ", "v5", "死"),
    ("行く", "v5", "去"), ("する", "vs", "做"), ("来る", "vk", "來"),
)
GROUPS = {"v1": "一段", "v5": "五段", "vs": "サ變", "vk": "カ變"}
FORMS = {
    "te": ("接下一段", "て形", "把動作接下去。接「ください」可提出請求，接另一個動作可說明先後或方式。"),
    "nai": ("說「不做」", "ない形", "「ない」否定前面的動作。"),
    "nakatta": ("說「當時沒做」", "なかった形", "「なかった」表示過去的否定（當時沒有做）。"),
    "tara": ("提出假設", "〜たら", "先換成た形，再加「ら」，提出「如果……」的條件。"),
    "ba": ("條件假定", "〜ば（假定形）", "把詞尾換成 え段再加「ば」，表示「只要……就……」的一般條件。"),
    "ta": ("說「做了」", "た形", "把動作當成已發生或已完成的事來說。"),
    "masu": ("禮貌地說", "ます形", "「ます」讓說法較禮貌；是習慣還是將來，要看時間和上下文。"),
    "masu_past": ("禮貌地說「做了」", "ました形", "「ました」是禮貌體的過去式。"),
    "masu_neg": ("禮貌地說「不做」", "ません形", "「ません」是禮貌體的否定式。"),
    "tai": ("說「想做」", "たい形", "「たい」表示想做前面的動作。"),
    "potential": ("說「能夠做」", "可能形", "表示有能力或條件做到該動作（一段動詞「〜られる」亦可表被動，須配合助詞判斷）。"),
    "passive": ("說「被做」", "被動形", "主語承受來自他人的動作（常搭配「〜に」標示動作發起者）。"),
    "causative": ("說「讓／叫人做」", "使役形", "表示讓、允許或強迫對方做該動作。"),
    "volitional": ("說「來做吧／打算」", "意志形", "表示說話者的意志或向對方提議「一起……吧」。"),
    "imperative": ("直接命令", "命令形", "語氣最強硬的直接命令，多見於緊急場景或男性粗獷口吻。"),
    "tero": ("要求對方繼續", "てろ／でろ", "「〜ていろ」口語常省掉「い」。說話者命令對方繼續做某事，或維持目前的狀態，語氣強硬。"),
    "teiru": ("說動作或狀態持續", "ている", "先接成て形，再接「いる」。可描述正在做的事，也可描述動作留下的狀態。"),
    "teita": ("說當時持續著", "ていた", "表示過去某時點正在進行，或當時維持著某種狀態。"),
}
# 完整詞尾、以「て／で」接續後的兩種縮寫、句中作用。なきゃ／なくちゃ 以否定形為基礎。
SPOKEN_ENDINGS = {
    "teru": ("ている", "てる", "でる", "動作正在進行，或先前動作造成的狀態仍在持續。"),
    "teta": ("ていた", "てた", "でた", "把時間拉回先前：當時正在做，或當時仍是那個狀態。"),
    "tenai": ("ていない", "てない", "でない", "當前沒有在做，或到目前為止還沒做。"),
    "tenakatta": ("ていなかった", "てなかった", "でなかった", "當時沒有在做，或那時為止還沒做。"),
    "tenda": ("ているのだ", "てんだ", "でんだ", "把持續中的動作當作解釋；搭配疑問詞時，常帶有追問的語氣。"),
    "terunda": ("ているんだ", "てるんだ", "でるんだ", "口語用正在進行／持續中的狀態向對方說明原因或現況。"),
    "tetanda": ("ていたのだ", "てたんだ", "でたんだ", "用先前正在做的事解釋眼前情況。"),
    "chau": ("てしまう", "ちゃう", "じゃう", "表示把動作做完，也可能帶有『不小心、偏偏』的心情。"),
    "chatta": ("てしまった", "ちゃった", "じゃった", "事情已經做完或發生了；是否有懊惱，要看上下文。"),
    "toku": ("ておく", "とく", "どく", "先做某事，讓之後用得上，或讓結果保持下去。"),
    "toite": ("ておいて", "といて", "どいて", "先做好或讓狀態保持下去；用在句末時常是在請對方這麼做。"),
    "toita": ("ておいた", "といた", "どいた", "已經事先做好了準備，或已經把狀態留著。"),
    "chadame": ("てはだめ", "ちゃだめ", "じゃだめ", "禁止前面的動作，意思是『不能這麼做』。"),
    "nakya": ("なければ", "なきゃ", "なきゃ", "意思是『如果不……』。句末省略後半句時，常暗示『得去做』。"),
    "nakucha": ("なくては", "なくちゃ", "なくちゃ", "意思是『如果不……不行』，口語常省略後半句表示『必須去做』。"),
}
FORMS.update({key: (short + " 的用法", full + " → " + short, meaning)
              for key, (full, short, _voiced, meaning) in SPOKEN_ENDINGS.items()})

# 每種詞形對應的精準中文活用步驟標籤（供語境釋義與卡片顯示，杜絕否定／禁止／必須極性反轉）
FORM_REASONS: dict[str, tuple[str, ...]] = {
    "te": ("て形",),
    "nai": ("否定形",),
    "nakatta": ("否定形", "過去形"),
    "tara": ("假定條件（〜たら）",),
    "ba": ("假定條件（〜ば）",),
    "ta": ("過去形",),
    "masu": ("禮貌形（〜ます）",),
    "masu_past": ("禮貌形（〜ます）", "過去形"),
    "masu_neg": ("禮貌形（〜ます）", "否定形"),
    "tai": ("願望（〜たい）",),
    "potential": ("可能形",),
    "passive": ("被動形",),
    "causative": ("使役形",),
    "volitional": ("意志／提議形",),
    "imperative": ("命令形",),
    "tero": ("進行／狀態（〜ている）", "命令形"),
    "teiru": ("進行／狀態（〜ている）",),
    "teita": ("進行／狀態（〜ている）", "過去形"),
}
SPOKEN_REASONS: dict[str, tuple[str, ...]] = {
    "teru": ("進行／狀態（〜ている）",),
    "teta": ("進行／狀態（〜ている）", "過去形"),
    "tenai": ("進行／狀態（〜ている）", "否定形"),
    "tenakatta": ("進行／狀態（〜ている）", "否定形", "過去形"),
    "tenda": ("進行／狀態（〜ている）", "解釋／追問（〜のだ）"),
    "terunda": ("進行／狀態（〜ている）", "解釋（〜のだ）"),
    "tetanda": ("進行／狀態（〜ている）", "過去形", "解釋（〜のだ）"),
    "chau": ("完了／遺憾（〜てしまう）",),
    "chatta": ("完了／遺憾（〜てしまう）", "過去形"),
    "toku": ("預先（〜ておく）",),
    "toite": ("預先（〜ておく）", "て形"),
    "toita": ("預先（〜ておく）", "過去形"),
    "chadame": ("禁止（〜てはだめ）",),
    "nakya": ("必須（〜なければ）",),
    "nakucha": ("必須（〜なくては）",),
}
FORM_REASONS.update(SPOKEN_REASONS)

# 五段動詞的 あ段、い段與て形接尾（濁音時為で）
ROWS = {
    "う": ("わ", "い", "って"), "つ": ("た", "ち", "って"), "る": ("ら", "り", "って"),
    "む": ("ま", "み", "んで"), "ぶ": ("ば", "び", "んで"), "ぬ": ("な", "に", "んで"),
    "く": ("か", "き", "いて"), "ぐ": ("が", "ぎ", "いで"), "す": ("さ", "し", "して"),
}
# 五段動詞的 え段（可能／假定／命令）與 お段（意志）
ROWS_EO = {
    "う": ("え", "お"), "つ": ("て", "と"), "る": ("れ", "ろ"),
    "む": ("め", "も"), "ぶ": ("べ", "ぼ"), "ぬ": ("ね", "の"),
    "く": ("け", "こ"), "ぐ": ("げ", "ご"), "す": ("せ", "そ"),
}

_GODAN_SURU_VERBS = frozenset({
    "こする", "擦る", "ゆする", "揺する", "すする", "啜る",
    "かする", "掠る", "なする", "いぶする",
})
_ARU_LEMMAS = frozenset({"ある", "有る", "在る"})
_IRU_LEMMAS = frozenset({"いる", "居る"})
_ARU_BLOCKED_FORMS = frozenset({
    "teiru", "teita", "tero", "teru", "teta", "tenai", "tenakatta",
    "tenda", "terunda", "tetanda", "imperative", "volitional",
    "potential", "passive", "causative",
})
_IRU_BLOCKED_FORMS = frozenset({
    "teiru", "teita", "tero", "teru", "teta", "tenai", "tenakatta",
    "tenda", "terunda", "tetanda",
})


@dataclass(frozen=True)
class VerbLesson:
    lemma: str
    group: str
    form: str
    fixed: str
    old: str
    new: str
    rule: str
    expanded: str = ""

    @property
    def surface(self) -> str:
        return self.fixed + self.new

    @property
    def reasons(self) -> tuple[str, ...]:
        return FORM_REASONS.get(self.form, ())

    def diagram(self, result_label: str = "台詞裡") -> str:
        # 使用底線及粗體標記改動；Qt 與 Anki 直接繼承各自主題的文字色。
        fixed, old, new = map(escape, (self.fixed, self.old, self.new))
        columns = [("詞典裡", f"{fixed}<u>{old}</u>")]
        if self.expanded:
            columns.append(("省略前", escape(self.expanded)))
        elif self.form in ("teiru", "teita", "tero", "tara"):
            suffix = {"teiru": "いる", "teita": "いた", "tero": "ろ", "tara": "ら"}[self.form]
            step = "先變成た形" if self.form == "tara" else "先接動作"
            columns.append((step, f"{fixed}<u>{escape(self.new.removesuffix(suffix))}</u>"))
        columns.append((result_label, f"{fixed}<b><u>{new}</u></b>"))
        headings = "<td></td>".join(f"<td>{escape(title)}</td>" for title, _ in columns)
        words = "<td> → </td>".join(f'<td><big>{word}</big></td>' for _, word in columns)
        return f'<table cellpadding="3" cellspacing="0"><tr>{headings}</tr><tr>{words}</tr></table>'

    def html(self) -> str:
        return self.diagram() + f'<div>{escape(self.rule)}</div><div>{escape(FORMS[self.form][2])}</div>'


def build_lesson(lemma: str, group: str, form: str) -> VerbLesson | None:
    if not lemma or form not in FORMS:
        return None
    if lemma in _ARU_LEMMAS and form in _ARU_BLOCKED_FORMS:
        return None
    if lemma in _IRU_LEMMAS and form in _IRU_BLOCKED_FORMS:
        return None
    if form in SPOKEN_ENDINGS:
        negative = form in ("nakya", "nakucha")
        base = build_lesson(lemma, group, "nai" if negative else "te")
        if base is None:
            return None
        full, short, voiced, _ = SPOKEN_ENDINGS[form]
        if negative:
            stem = base.new.removesuffix("ない")
        else:
            stem = base.new[:-1]
            if base.new.endswith("で"):
                full, short = "で" + full[1:], voiced
        expanded = base.fixed + stem + full
        rule = f"口語把「{full}」說成「{short}」。"
        return VerbLesson(lemma, group, form, base.fixed, base.old, stem + short, rule, expanded)
    old, fixed = lemma[-1], lemma[:-1]
    if group in ("vs", "vk"):
        if group == "vs" and lemma.endswith("する") and lemma not in _GODAN_SURU_VERBS:
            old, fixed = "する", lemma[:-2]
            mapping = {
                "te": "して", "nai": "しない", "nakatta": "しなかった",
                "ta": "した", "tara": "したら", "ba": "すれば",
                "masu": "します", "masu_past": "しました", "masu_neg": "しません",
                "tai": "したい", "potential": "できる", "passive": "される",
                "causative": "させる", "volitional": "しよう", "imperative": "しろ",
                "teiru": "している", "teita": "していた", "tero": "してろ",
            }
            rule = "する 這一組要整組認。先找出「する」，再換上右邊的寫法。"
        elif group == "vk" and lemma.endswith(("来る", "くる")):
            is_kanji = lemma.endswith("来る")
            old, fixed = lemma[-2:], lemma[:-2]
            if is_kanji:
                mapping = {
                    "te": "来て", "nai": "来ない", "nakatta": "来なかった",
                    "ta": "来た", "tara": "来たら", "ba": "来れば",
                    "masu": "来ます", "masu_past": "来ました", "masu_neg": "来ません",
                    "tai": "来たい", "potential": "来られる", "passive": "来られる",
                    "causative": "来させる", "volitional": "来よう", "imperative": "来い",
                    "teiru": "来ている", "teita": "来ていた", "tero": "来てろ",
                }
            else:
                mapping = {
                    "te": "きて", "nai": "こない", "nakatta": "こなかった",
                    "ta": "きた", "tara": "きたら", "ba": "くれば",
                    "masu": "きます", "masu_past": "きました", "masu_neg": "きません",
                    "tai": "きたい", "potential": "こられる", "passive": "こられる",
                    "causative": "こさせる", "volitional": "こよう", "imperative": "こい",
                    "teiru": "きている", "teita": "きていた", "tero": "きてろ",
                }
            rule = "来る（くる）會換讀音。否定／可能／被動／使役／意志／命令讀「こ」，ます／て／た讀「き」，ば讀「く」。"
        else:
            return None
        return VerbLesson(lemma, group, form, fixed, old, mapping[form], rule)
    elif group == "v1" and old == "る":
        mapping = {
            "te": "て", "nai": "ない", "nakatta": "なかった",
            "ta": "た", "tara": "たら", "ba": "れば",
            "masu": "ます", "masu_past": "ました", "masu_neg": "ません",
            "tai": "たい", "potential": "られる", "passive": "られる",
            "causative": "させる", "volitional": "よう", "imperative": "ろ",
            "teiru": "ている", "teita": "ていた", "tero": "てろ",
        }
        rule = f"保留「{fixed}」，去掉「る」，再接需要的詞尾。這是「一段」的變法。"
        if form in ("potential", "passive"):
            rule += " 一段動詞的「〜られる」兼作可能形與被動形（口語可能形常省略為「〜れる」），需配合助詞判斷。"
        return VerbLesson(lemma, group, form, fixed, old, mapping[form], rule)
    elif group == "v5" and old in ROWS:
        a, i, te = ROWS[old]
        e, o = ROWS_EO[old]
        if lemma in ("行く", "いく", "ゆく") or lemma.endswith("行く"):
            te = "って"
        elif lemma in ("問う", "請う", "乞う", "こう", "とう"):
            te = "うて"
        ta = te[:-1] + ("だ" if te.endswith("で") else "た")
        if form in ("nai", "nakatta") and lemma in _ARU_LEMMAS:
            new_aru = "ない" if form == "nai" else "なかった"
            return VerbLesson(lemma, group, form, "", lemma, new_aru, f"表示存在的「ある」否定時整個換成「{new_aru}」。")
        if form in ("masu", "masu_past", "masu_neg") and lemma in (
            "くださる", "下さる", "なさる", "いらっしゃる", "おっしゃる", "仰る", "ござる", "御座る"
        ):
            hon_suffix = {"masu": "います", "masu_past": "いました", "masu_neg": "いません"}[form]
            return VerbLesson(lemma, group, form, fixed, old, hon_suffix, "這個敬語動詞接「ます」時，把「る」換成「い」，再接敬體詞尾。")
        mapping = {
            "te": te, "ta": ta, "tara": ta + "ら", "ba": e + "ば",
            "nai": a + "ない", "nakatta": a + "なかった",
            "masu": i + "ます", "masu_past": i + "ました", "masu_neg": i + "ません",
            "tai": i + "たい", "potential": e + "る", "passive": a + "れる",
            "causative": a + "せる", "volitional": o + "う", "imperative": e,
            "teiru": te + "いる", "teita": te + "いた", "tero": te + "ろ",
        }
        if form in ("te", "teiru", "teita", "tero", "ta", "tara"):
            ending = ta if form in ("ta", "tara") else te
            rule = f"保留前面的字，把詞尾「{old}」換成「{ending}」。"
            if lemma in ("行く", "いく", "ゆく") or lemma.endswith("行く"):
                rule += "行く 是例外，和「書く → 書いて」的接法不同。"
        elif form in ("potential", "ba", "imperative"):
            suffix_map = {"potential": "る", "ba": "ば", "imperative": ""}
            suf = suffix_map[form]
            rule = (
                f"詞尾先從「{old}」換成 え段「{e}」" + (f"，再接「{suf}」。" if suf else "即可構成命令形。")
            )
        elif form == "volitional":
            rule = f"詞尾先從「{old}」換成 お段「{o}」，再接「う」。"
        else:
            step = a if form in ("nai", "nakatta", "passive", "causative") else i
            suffix = {
                "nai": "ない", "nakatta": "なかった",
                "passive": "れる", "causative": "せる",
                "masu": "ます", "masu_past": "ました", "masu_neg": "ません", "tai": "たい",
            }[form]
            rule = f"詞尾先從「{old}」換成「{step}」，再接「{suffix}」。"
        rule += " 同一個詞尾會隨接法換音，這是「五段」的特徵。"
        if form == "tara":
            rule += " 加上「ら」後，整個形式表示條件。"
        return VerbLesson(lemma, group, form, fixed, old, mapping[form], rule)
    else:
        return None


@lru_cache(maxsize=2048)
def spoken_surfaces(lemma: str, group: str) -> frozenset[str]:
    return frozenset(lesson.surface for form in SPOKEN_ENDINGS
                     if (lesson := build_lesson(lemma, group, form)))


@lru_cache(maxsize=2048)
def spoken_reasons_for_surface(lemma: str, group: str, surface: str) -> tuple[str, ...]:
    """回傳口語縮寫表層形對應的精準活用步驟（如 食べちゃだめ → ('禁止（〜てはだめ）',)）。"""
    for form in SPOKEN_ENDINGS:
        lesson = build_lesson(lemma, group, form)
        if lesson and lesson.surface == surface:
            return SPOKEN_REASONS.get(form, ())
    return ()


@lru_cache(maxsize=2048)
def form_reasons_for_surface(lemma: str, group: str, surface: str) -> tuple[str, ...]:
    """回傳任意已定義詞形表層對應的精準活用步驟。"""
    for form in FORMS:
        lesson = build_lesson(lemma, group, form)
        if lesson and lesson.surface == surface:
            return FORM_REASONS.get(form, ())
    return ()


def verb_group(lemma: str, pos: str) -> str:
    if "一段" in pos or re.search(r"\bv1\b", pos):
        return "v1"
    if "五段" in pos or re.search(r"\bv5", pos) or lemma in _GODAN_SURU_VERBS:
        return "v5"
    if "サ変" in pos or re.search(r"\bvs\b", pos) or lemma.endswith("する"):
        return "vs"
    if "カ変" in pos or re.search(r"\bvk\b", pos) or lemma in ("来る", "くる") or lemma.endswith(("て来る", "で来る", "てくる", "でくる")):
        return "vk"
    return ""


def lesson_for_token(token: Token, *, preview: bool = False) -> VerbLesson | None:
    if token.surface == token.lemma and not preview:
        return None
    parts_of_speech = [entry.pos for entry in token.lookup.all_entries] if token.lookup else []
    for pos in [token.verb_type, *parts_of_speech]:
        group = verb_group(token.lemma, pos)
        if not group:
            continue
        for form in FORMS:
            lesson = build_lesson(token.lemma, group, form)
            if lesson and (lesson.surface == token.surface or (preview and token.surface == token.lemma)):
                return lesson
    return None
