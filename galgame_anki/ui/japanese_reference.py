"""學習藍圖中的離線總索引。"""
from html import escape

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton, QSplitter, QTextBrowser, QVBoxLayout, QWidget)

from galgame_anki.core.japanese_reference import ReferenceIndex
from galgame_anki.ui.theme import get_palette


class JapaneseReference(QWidget):
    lesson_requested = Signal(str)
    pattern_requested = Signal(str)

    def __init__(self, grammar, theme_name):
        super().__init__()
        self.index = ReferenceIndex(grammar)
        self.group = None
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.browse = QPushButton("全部分類")
        self.browse.clicked.connect(self.home)
        self.search = QLineEdit()
        self.search.setClearButtonEnabled(True)
        self.search.setPlaceholderText("搜尋中文、日文或讀音，例如 4月、三個人、はつか、被動")
        self.search.setAccessibleName("搜尋全部日語要點")
        bar.addWidget(self.browse)
        bar.addWidget(self.search, 1)
        layout.addLayout(bar)
        self.count = QLabel()
        layout.addWidget(self.count)
        split = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(split)
        self.results = QListWidget()
        self.results.setAccessibleName("分類與搜尋結果")
        self.reader = QTextBrowser()
        palette = get_palette(theme_name)
        self.reader.document().setDefaultStyleSheet(
            f"a {{ color: {palette.pill_info_fg}; }} h2 {{ color: {palette.accent_lilac}; }}"
        )
        self.reader.setOpenLinks(False)
        self.reader.document().setDocumentMargin(12)
        split.addWidget(self.results)
        split.addWidget(self.reader)
        split.setSizes([235, 550])
        split.setStretchFactor(1, 1)
        self.results.currentItemChanged.connect(self.display)
        self.search.textChanged.connect(self.refresh)
        self.reader.anchorClicked.connect(self.follow)
        self.home()

    def home(self):
        self.group = None
        self.search.clear()
        self.refresh()

    def refresh(self):
        query = self.search.text().strip()
        self.results.clear()
        self.reader.clear()
        if not query and self.group is None:
            groups = dict.fromkeys(t.group for t in self.index.topics.values())
            for group in groups:
                item = QListWidgetItem(group, self.results)
                item.setData(Qt.ItemDataRole.UserRole, ("group", group, None))
            self.count.setText(f"{len(groups)} 個分類 · {len(self.index.topics)} 個主題")
        else:
            matches = [(key, row) for key, row in self.index.find(query)
                       if query or self.index.topics[key].group == self.group]
            for key, row in matches:
                topic = self.index.topics[key]
                item = QListWidgetItem(topic.title, self.results)
                item.setData(Qt.ItemDataRole.UserRole, ("topic", key, row))
                item.setToolTip(topic.group)
            self.count.setText(f"搜尋結果 · {len(matches)} 個主題" if query else f"{self.group} · {len(matches)} 個主題")
            if not matches:
                self.reader.setPlainText("沒有找到相符項目。可改用較短的詞、假名或中文意思，也可點「全部分類」瀏覽。")
        if self.results.count():
            self.results.setCurrentRow(0)

    def display(self, current):
        if not current:
            return
        kind, key, row = current.data(Qt.ItemDataRole.UserRole)
        if kind == "group":
            parts = [f"<h2>{escape(key)}</h2>"]
            for t in self.index.topics.values():
                if t.group == key:
                    parts.append(f'<p><a href="topic:{escape(t.key)}">{escape(t.title)}</a></p>')
            self.reader.setHtml("".join(parts))
            return
        t = self.index.topics[key]
        parts = [f'<p><a href="group:current">{escape(t.group)}</a></p><h2>{escape(t.title)}</h2>',
                 f"<p>{escape(t.rule)}</p>"]
        date = self.index.date_parts(self.search.text())
        if date:
            month, day = date
            month_text, month_kana, _ = self.index.topics["months"].rows[month - 1]
            day_text, day_kana, _ = self.index.topics["dates"].rows[day - 1]
            parts.append(f"<p><b>{month_text}{day_text}</b>　{month_kana}　{day_kana}</p>")
        if row is not None:
            parts.append("<p><b>命中條目</b>　" + "　".join(escape(f) for f in t.rows[row] if f) + "</p>")
        if t.before:
            prev = self.index.topics[t.before]
            parts.append(f'<p>先掌握　<a href="topic:{escape(prev.key)}">{escape(prev.title)}</a></p>')
        if key.startswith(("study-", "pattern-")):
            label = "逐步看懂這個用法" if key.startswith("study-") else "開啟句型練習"
            parts.append(f'<p><a href="lesson:current">{label}</a></p>')
        parts.append('<table cellpadding="5">')
        for first, second, third in t.rows:
            parts.append(f"<tr><td><b>{escape(first)}</b></td><td>{escape(second)}</td><td>{escape(third)}</td></tr>")
        parts.append("</table>")
        related = [p for p in self.index.topics.values() if p.before == key]
        if related:
            parts.append("<h3>接著可以查</h3>" + "　".join(f'<a href="topic:{escape(p.key)}">{escape(p.title)}</a>' for p in related))
        self.reader.setHtml("".join(parts))

    def follow(self, url):
        if url.scheme() == "topic":
            self.open_topic(url.path())
        elif self.results.currentItem():
            _, key, _ = self.results.currentItem().data(Qt.ItemDataRole.UserRole)
            if url.scheme() == "group":
                self.group = self.index.topics[key].group
                self.search.clear()
                self.refresh()
            elif key.startswith("study-"):
                self.lesson_requested.emit(key.removeprefix("study-"))
            elif key.startswith("pattern-"):
                self.pattern_requested.emit(key.removeprefix("pattern-"))

    def open_topic(self, key):
        if key not in self.index.topics:
            return
        self.group = self.index.topics[key].group
        self.search.clear()
        self.refresh()
        for i in range(self.results.count()):
            if self.results.item(i).data(Qt.ItemDataRole.UserRole)[1] == key:
                self.results.setCurrentRow(i)
                break
