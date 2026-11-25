"""可切換動詞與表達目的的詞尾對照。"""
from html import escape

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QTextBrowser, QVBoxLayout, QWidget

from galgame_anki.core.verb_lesson import EXAMPLES, FORMS, GROUPS, VerbLesson, build_lesson
from galgame_anki.ui.theme import get_palette


class VerbExplorer(QWidget):
    def __init__(self, theme_name: str) -> None:
        super().__init__()
        self.palette_colors = get_palette(theme_name)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 10)
        choices = QHBoxLayout()
        choices.addWidget(QLabel("動詞"))
        self.verbs = QComboBox()
        for lemma, group, gloss in EXAMPLES:
            self.verbs.addItem(f"{lemma}（{gloss}）", (lemma, group))
        choices.addWidget(self.verbs)
        choices.addSpacing(12)
        choices.addWidget(QLabel("想表達什麼"))
        self.forms = QComboBox()
        for form, (intent, _, _) in FORMS.items():
            self.forms.addItem(intent, form)
        choices.addWidget(self.forms)
        choices.addStretch()
        layout.addLayout(choices)
        self.page = QTextBrowser()
        layout.addWidget(self.page)
        self.verbs.currentIndexChanged.connect(self.render)
        self.forms.currentIndexChanged.connect(self.render)
        self.render()

    def select_lesson(self, lesson: VerbLesson) -> None:
        pair = (lesson.lemma, lesson.group)
        idx = next((i for i in range(self.verbs.count()) if tuple(self.verbs.itemData(i)) == pair), -1)
        if idx == -1:
            self.verbs.addItem(lesson.lemma, pair)
            idx = self.verbs.count() - 1
        self.verbs.setCurrentIndex(idx)
        self.forms.setCurrentIndex(self.forms.findData(lesson.form))

    def render(self) -> None:
        lemma, group = self.verbs.currentData()
        form = self.forms.currentData()
        lesson = build_lesson(lemma, group, form)
        if lesson is None:
            return
        p = self.palette_colors
        intent, name, purpose = FORMS[form]
        # 同一個意思換動詞，觀察詞尾如何跟著類別換；再固定動詞比較不同接法。
        compare_lemma, compare_group = ("帰る", "v5") if group == "v1" else ("食べる", "v1")
        other = build_lesson(compare_lemma, compare_group, form)
        def row(a: str, b: str, c: str) -> str:
            return f'<tr><td>{escape(a)}</td><td><b>{escape(b)}</b></td><td>{escape(c)}</td></tr>'
        compare = row(lemma, lesson.surface, GROUPS[group]) + row(compare_lemma, other.surface, GROUPS[compare_group])
        situations = {
            "te": (("パンを食べて、出かける。", "吃完麵包再出門。「食べて」把吃和出門接起來。"),
                   ("パンを食べてください。", "請吃麵包。接上「ください」，整句成了請求。")),
            "tero": (("ここで座ってろ。", "在這裡坐好。「座っていろ」省掉「い」，說話者要求對方繼續坐著。"),),
            "teiru": (("今、ご飯を食べている。", "現在正在吃飯。"), ("窓が開いている。", "窗戶開著。開窗後的狀態持續著。")),
            "tara": (("雨が降ったら、家にいる。", "如果下雨，我就待在家。"),),
            "ta": (("昨日、映画を見た。", "昨天看了電影。"),),
            "nai": (("朝はコーヒーを飲まない。", "早上不喝咖啡。"),),
            "masu": (("明日、学校へ行きます。", "明天要去學校。時間由「明日」交代。"),),
            "tai": (("日本へ行きたい。", "我想去日本。"),),
        }
        examples = ''.join(f'<p><b>{jp}</b><br>{zh}</p>' for jp, zh in situations[form])
        self.page.setHtml(f"""
        <style>h3 {{color:{p.accent_lilac}}} td {{padding:4px 14px}} p {{line-height:1.35}}</style>
        <h2>{escape(lemma)}　·　{intent}</h2>
        {lesson.diagram("變化後")}
        <p>{escape(lesson.rule)}</p>
        <h3>這個接法在句子裡做什麼？</h3><p>{purpose} 這個形式叫「{name}」。</p>
        {examples}
        <h3>同樣要「{intent}」，換一個動詞會怎樣？</h3>
        <table cellspacing="3" cellpadding="3"><tr><th>詞典裡</th><th>變化後</th><th>變化類別</th></tr>{compare}</table>
        <p>「一段／五段」說明一個動詞<b>怎麼換詞尾</b>。「て形／た形」說明這次<b>換成了哪個形式</b>。
        兩個都以「る」結尾的動詞，也可能採用不同的變法。</p>""")
