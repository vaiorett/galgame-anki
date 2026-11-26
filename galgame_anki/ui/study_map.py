"""從字幕用例跳到先備知識、對比例句和個人複習。"""
from html import escape

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton, QTextBrowser,
                               QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from galgame_anki.core.study_map import CONCEPTS
from galgame_anki.core.study_steps import EXAMPLES, REQUIRES
from galgame_anki.ui.theme import get_palette
from galgame_anki.ui.web_dictionary import show_web_dictionary


PATTERNS = {"ability": "n4_potential", "state": "n5_teiru", "completion": "n4_teshimau", "prepare": "n4_teoku",
            "condition": "n4_tara", "connect": "n5_te_kudasai", "stance": "n5_noda", "trend": "n3_ippouda"}


class StudyMap(QWidget):
    pattern_requested = Signal(str)
    reference_requested = Signal(str)

    def __init__(self, store, theme_name):
        super().__init__()
        self.store = store
        palette = get_palette(theme_name)
        self.focus = None
        self.sentence = ""
        self.lemma = ""
        self.revealed = False
        self.review = False
        self.saved_index = 0
        layout = QVBoxLayout(self)
        split = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(split, 1)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setAccessibleName("語法與詞彙的先備關係")
        self.tree.setIndentation(13)
        self.tree.setMinimumWidth(136)
        split.addWidget(self.tree)
        self.nodes = {}
        branches = {}
        for key, item in CONCEPTS.items():
            if item.branch not in branches:
                branch = QTreeWidgetItem(self.tree, [item.branch])
                branch.setFlags(branch.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                branches[item.branch] = branch
            parent = self.nodes[item.parent] if item.parent else branches[item.branch]
            node = QTreeWidgetItem(parent, [item.title])
            node.setData(0, Qt.ItemDataRole.UserRole, key)
            self.nodes[key] = node
        self.tree.expandAll()
        self.reader = QTextBrowser()
        self.reader.document().setDefaultStyleSheet(
            f"a {{ color: {palette.pill_info_fg}; }} h2, h3 {{ color: {palette.accent_lilac}; }}"
        )
        self.reader.document().setDocumentMargin(10)
        self.reader.setOpenLinks(False)
        self.reader.anchorClicked.connect(self.follow)
        split.addWidget(self.reader)
        split.setSizes([220, 550])
        split.setStretchFactor(1, 1)
        navigation = QHBoxLayout()
        layout.addLayout(navigation)
        self.previous_step = QPushButton("上一步")
        self.previous_step.clicked.connect(lambda: self.move_step(-1))
        navigation.addWidget(self.previous_step)
        self.step_label = QLabel()
        navigation.addWidget(self.step_label)
        navigation.addStretch()
        self.next_step = QPushButton("下一步")
        self.next_step.setObjectName("PrimaryBtn")
        self.next_step.clicked.connect(lambda: self.move_step(1))
        navigation.addWidget(self.next_step)
        bottom = QHBoxLayout()
        layout.addLayout(bottom)
        self.recall = QPushButton("核對答案")
        self.recall.clicked.connect(self.toggle_answer)
        bottom.addWidget(self.recall)
        self.save = QPushButton("留作我的例句")
        self.save.clicked.connect(self.keep)
        bottom.addWidget(self.save)
        self.review_btn = QPushButton("重溫例句")
        self.review_btn.clicked.connect(self.toggle_review)
        bottom.addWidget(self.review_btn)
        self.more = QPushButton("下一句")
        self.more.clicked.connect(self.next_example)
        bottom.addWidget(self.more)
        bottom.addStretch()
        self.tree.currentItemChanged.connect(self.changed)
        self.select("roles")

    def select(self, key):
        if key in self.nodes:
            self.tree.setCurrentItem(self.nodes[key])

    def selected_key(self):
        current = self.tree.currentItem()
        return current.data(0, Qt.ItemDataRole.UserRole) if current else None

    def show_focus(self, focus, sentence, lemma):
        self.focus, self.sentence, self.lemma = focus, sentence, lemma
        self.select(focus.key)
        self.changed()

    def move_step(self, direction):
        self.step = max(0, min(self.step + direction, len(self.walk.steps)))
        self.revealed = False
        self.render()

    def changed(self):
        self.step = 0
        self.show_relations = False
        self.revealed = self.review = False
        self.saved_index = 0
        self.render()

    def toggle_answer(self):
        self.revealed = not self.revealed
        self.render()
        if self.revealed:
            self.reader.scrollToAnchor("answer")

    def keep(self):
        key = self.selected_key()
        if self.focus and self.sentence and key in (self.focus.key, self.focus.lexical_key):
            self.store.keep_study_example(key, self.lemma, self.sentence, self.detail(key))
            self.render()

    def detail(self, key):
        if self.focus.key == key:
            return self.focus.evidence + ("　·　" + self.focus.vocabulary
                                          if self.focus.vocabulary != self.focus.evidence else "")
        return self.focus.vocabulary

    def toggle_review(self):
        self.review = not self.review
        self.saved_index = 0
        self.revealed = False
        self.render()

    def next_example(self):
        self.saved_index += 1
        self.revealed = False
        self.render()

    def render(self):
        key = self.selected_key()
        if not key:
            return
        item = CONCEPTS[key]
        examples = self.store.study_examples(key)
        matches = bool(self.sentence and self.focus and key in (self.focus.key, self.focus.lexical_key))
        self.save.setVisible(matches and not self.review)
        saved = any(r["lemma"] == self.lemma and r["sentence"] == self.sentence for r in examples)
        self.save.setEnabled(not saved)
        self.save.setText("已留作例句" if saved else "留作我的例句")
        self.review_btn.setVisible(bool(examples))
        self.review_btn.setText("返回解說" if self.review else f"重溫例句（{len(examples)}）")
        self.more.setVisible(self.review and len(examples) > 1)
        self.recall.setText("收起答案" if self.revealed else "核對答案")
        live = (self.focus.walk if key == self.focus.key else self.focus.lexical_walk) if matches else None
        self.walk = live or EXAMPLES[key]
        count = len(self.walk.steps)
        summary = self.step == count
        self.previous_step.setVisible(not self.review)
        self.previous_step.setEnabled(self.step > 0)
        self.step_label.setVisible(not self.review)
        self.step_label.setText("綜合練習" if summary else f"第 {self.step + 1} 步，共 {count} 步")
        self.next_step.setVisible(not self.review and not summary)
        if not summary:
            self.next_step.setText("比較與練習" if self.step == count - 1 else "用這個結果，走下一步")
        self.recall.setVisible(self.review or summary)
        if self.review and examples:
            row = examples[self.saved_index % len(examples)]
            parts = [f"<p>我的例句 · {self.saved_index % len(examples)+1} / {len(examples)}</p>",
                     f"<h2>{escape(row['sentence'])}</h2>",
                     f"<p>找出「{escape(row['lemma'])}」，用自己的話解釋它跟哪個詞搭配、在這句起什麼作用。</p>"]
            if self.revealed:
                parts.append(f'<p><a name="answer"></a>{escape(row["excerpt"])}</p>')
        else:
            parts = [f"<h2>{item.title}</h2>"]
            if matches:
                parts.append(f"<p><b>原句</b>　{escape(self.sentence)}</p>")
            if summary:
                parts.append("<p>" + " → ".join(escape(v) for v in (self.walk.start, *(s[2] for s in self.walk.steps))) + "</p>")
                parts += [f"<h3>比較兩句</h3><p>{item.first}<br>{item.second}</p><p>{item.contrast}</p>",
                          f"<h3>換你判斷</h3><p>{item.prompt}</p>"]
                if self.revealed:
                    parts.append(f'<p><a name="answer"></a>{item.solution}</p>')
            else:
                action, reason, result = self.walk.steps[self.step]
                parts += [f"<p>{'這句的拆解' if live else '用例示範'}　·　{escape(action)}</p>",
                          f"<p>{'先辨認' if not self.step else '沿用上一步的結果'}</p><h3>{escape(self.walk.before(self.step))}</h3>",
                          f"<p>{escape(reason)}</p><p>↓</p><h3>{escape(result)}</h3>"]
                if self.step + 1 < count:
                    parts.append(f"<p>下一步會用這個結果來{escape(self.walk.steps[self.step + 1][0])}。</p>")
            if matches and self.focus.key != self.focus.lexical_key and key == self.focus.key:
                parts.append(f'<p><a href="topic:{self.focus.lexical_key}">比較本句的詞彙搭配</a></p>')
            if summary or self.show_relations:
                for label, edges in (("先要理解", REQUIRES.get(key, ())),
                                     ("學會後可以用在", [(target, why) for target, edges in REQUIRES.items()
                                                         for source, why in edges if source == key])):
                    if edges:
                        parts.append(f"<h3>{label}</h3>")
                        for node, why in edges:
                            parts.append(f'<p><a href="topic:{node}">{CONCEPTS[node].title}</a>　{why}</p>')
            if not summary:
                label = "收起前置與後續" if self.show_relations else "查看前置與後續的關係"
                parts.append(f'<p><a href="edges:toggle">{label}</a></p>')
            if key in PATTERNS:
                parts.append('<p><a href="pattern:index">看相關句型的接續與例句</a></p>')
            sources = '<a href="tufs:module">東外大 · 練習與用例</a>'
            if matches and self.lemma:
                sources += '　<a href="dict:word">コトバンク · 核對選詞</a>'
            if matches:
                parts.append('<p><a href="reference:search">在日語總索引查這個詞</a></p>')
            parts.append(f"<p>{sources}</p>")
        self.reader.setHtml("".join(parts))

    def follow(self, link):
        if link.scheme() == "topic":
            self.select(link.path())
        elif link.scheme() == "edges":
            self.show_relations = not self.show_relations
            self.render()
        elif link.scheme() == "pattern" and self.selected_key() in PATTERNS:
            self.pattern_requested.emit(PATTERNS[self.selected_key()])
        elif link.scheme() == "reference":
            self.reference_requested.emit(self.lemma)
        elif link.scheme() == "dict":
            show_web_dictionary(self.lemma, self, "kotobank")
        elif link.scheme() == "tufs":
            self.open_module()

    def open_module(self):
        from PySide6.QtWidgets import QMessageBox
        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView
        except ImportError:
            QMessageBox.information(self, "東京外國語大學", "線上教材需要 Qt WebEngine。本機例句和練習可繼續使用。")
            return
        item = CONCEPTS[self.selected_key()]
        url = QUrl("https://www.coelang.tufs.ac.jp/mt/ja/" + item.module)
        dialog = QDialog(self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.setWindowTitle("東京外國語大學 · " + item.title)
        dialog.resize(820, 600)
        layout = QVBoxLayout(dialog)
        guide = QLabel("先聽一遍，再扮演其中一個角色接話。" if "dmod" in item.module else
                       "查閱模組例子，再回到自己的台詞，比較同一個用法。")
        guide.setWordWrap(True)
        layout.addWidget(guide)
        view = QWebEngineView(dialog)
        layout.addWidget(view, 1)
        retry = QPushButton("載入失敗，重新開啟教材")
        retry.clicked.connect(lambda: view.load(url))
        layout.addWidget(retry)
        retry.hide()
        view.loadFinished.connect(lambda ok: retry.setVisible(not ok))
        dialog.open()
        view.load(url)
