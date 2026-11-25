"""從原形與詞典類別推導常見詞形。只有表面形完整吻合時才解釋實際台詞。"""
from __future__ import annotations

from dataclasses import dataclass
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
    "tara": ("提出假設", "〜たら", "先換成た形，再加「ら」，提出「如果……」的條件。"),
    "ta": ("說「做了」", "た形", "把動作當成已發生或已完成的事來說。"),
    "masu": ("禮貌地說", "ます形", "「ます」讓說法較禮貌；是習慣還是將來，要看時間和上下文。"),
    "tai": ("說「想做」", "たい形", "「たい」表示想做前面的動作。"),
    "tero": ("要求對方繼續", "てろ／でろ", "「〜ていろ」口語常省掉「い」。說話者命令對方繼續做某事，或維持目前的狀態，語氣強硬。"),
    "teiru": ("說動作或狀態持續", "ている", "先接成て形，再接「いる」。可描述正在做的事，也可描述動作留下的狀態。"),
}
# 五段動詞的 あ段、い段與て形接尾（濁音時為で）
ROWS = {
    "う": ("わ", "い", "って"), "つ": ("た", "ち", "って"), "る": ("ら", "り", "って"),
    "む": ("ま", "み", "んで"), "ぶ": ("ば", "び", "んで"), "ぬ": ("な", "に", "んで"),
    "く": ("か", "き", "いて"), "ぐ": ("が", "ぎ", "いで"), "す": ("さ", "し", "して"),
}


@dataclass(frozen=True)
class VerbLesson:
    lemma: str
    group: str
    form: str
    fixed: str
    old: str
    new: str
    rule: str

    @property
    def surface(self) -> str:
        return self.fixed + self.new

    def diagram(self, result_label: str = "台詞裡") -> str:
        # 使用底線及粗體標記改動；Qt 與 Anki 直接繼承各自主題的文字色。
        fixed, old, new = map(escape, (self.fixed, self.old, self.new))
        columns = [("詞典裡", f"{fixed}<u>{old}</u>")]
        if self.form in ("teiru", "tero", "tara"):
            suffix = {"teiru": "いる", "tero": "ろ", "tara": "ら"}[self.form]
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
    old, fixed = lemma[-1], lemma[:-1]
    if group in ("vs", "vk"):
        if group == "vs" and lemma.endswith("する"):
            old, fixed = "する", lemma[:-2]
            pieces = ("して", "しない", "した", "します", "したい")
        elif group == "vk" and lemma in ("来る", "くる"):
            old, fixed = lemma, ""
            pieces = (("来て", "来ない", "来た", "来ます", "来たい")
                      if lemma == "来る" else ("きて", "こない", "きた", "きます", "きたい"))
        else:
            return None
        te, nai, ta, masu, tai = pieces
        rule = ("する 這一組要整組認。先找出「する」，再換上右邊的寫法。"
                if group == "vs" else "来る（くる）會換讀音。否定讀「こない」，其餘這幾種從「き」開始。")
    elif group == "v1" and old == "る":
        te, nai, ta, masu, tai = "て", "ない", "た", "ます", "たい"
        rule = f"保留「{fixed}」，去掉「る」，再接需要的詞尾。這是「一段」的變法。"
    elif group == "v5" and old in ROWS:
        a, i, te = ROWS[old]
        if lemma in ("行く", "いく", "ゆく") or lemma.endswith("行く"):
            te = "って"
        elif lemma in ("問う", "請う", "乞う", "こう", "とう"):
            te = "うて"
        ta = te[:-1] + ("だ" if te.endswith("で") else "た")
        nai, masu, tai = a + "ない", i + "ます", i + "たい"
        if form == "nai" and lemma in ("ある", "有る", "在る"):
            return VerbLesson(lemma, group, form, "", lemma, "ない", "表示存在的「ある」要整個換成「ない」。")
        if form == "masu" and lemma in ("くださる", "下さる", "なさる", "いらっしゃる", "おっしゃる", "仰る", "ござる", "御座る"):
            return VerbLesson(lemma, group, form, fixed, old, "います", "這個敬語動詞接「ます」時，把「る」換成「い」，再接「ます」。")
        if form in ("te", "teiru", "tero", "ta", "tara"):
            ending = ta if form in ("ta", "tara") else te
            rule = f"保留前面的字，把詞尾「{old}」換成「{ending}」。"
            if lemma in ("行く", "いく", "ゆく") or lemma.endswith("行く"):
                rule += "行く 是例外，和「書く → 書いて」的接法不同。"
        else:
            step = a if form == "nai" else i
            suffix = {"nai": "ない", "masu": "ます", "tai": "たい"}[form]
            rule = f"詞尾先從「{old}」換成「{step}」，再接「{suffix}」。"
        rule += " 同一個詞尾會隨接法換音，這是「五段」的特徵。"
    else:
        return None
    if form == "tara":
        rule += " 加上「ら」後，整個形式表示條件。"
    new = {"te": te, "ta": ta, "tara": ta + "ら", "nai": nai, "masu": masu, "tai": tai, "teiru": te + "いる", "tero": te + "ろ"}[form]
    return VerbLesson(lemma, group, form, fixed, old, new, rule)


def verb_group(lemma: str, pos: str) -> str:
    if "一段" in pos or re.search(r"\bv1\b", pos):
        return "v1"
    if "五段" in pos or re.search(r"\bv5", pos):
        return "v5"
    if lemma.endswith("する"):
        return "vs"
    if lemma in ("来る", "くる"):
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
