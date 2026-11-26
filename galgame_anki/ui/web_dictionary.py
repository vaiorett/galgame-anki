"""按需在程式內閱讀線上詞條。コトバンク日中詞條可直接用純輕量 HTML 解析器閱讀。"""

from __future__ import annotations

from html.parser import HTMLParser
import re

from PySide6.QtCore import Qt, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout, QWidget,
)

from galgame_anki.core.web_lookup import WEB_LOOKUP_SITES, build_web_lookup_url, sanitize_web_query
from galgame_anki.ui.theme import apply_soft_bold_document

_BLOCK_TAGS = frozenset({"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "dt", "dd", "section", "article"})
_SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "template"})


class _PlainHtmlExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        t = tag.lower()
        if t in _SKIP_TAGS:
            self._skip_depth += 1
        elif self._skip_depth == 0 and t in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif self._skip_depth == 0 and t in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data:
            self._parts.append(data)

    def get_text(self) -> str:
        raw = "".join(self._parts)
        return re.sub(r"\n{3,}", "\n\n", raw).strip()


def kotobank_entry(page: str) -> str:
    parser = _PlainHtmlExtractor()
    parser.feed(page or "")
    parser.close()
    text = parser.get_text()
    heading = re.search(r"日中辞典\s*第\s*3\s*版\s*の解説", text)
    if not heading:
        return ""
    entry = re.split(r"(?:^|\n)\s*(?:出典[\s　]|今日のキーワード)", text[heading.end():], maxsplit=1)[0]
    return entry.strip()


class WebDictionaryDialog(QDialog):
    def __init__(self, query: str, parent: QWidget | None, site_id: str):
        super().__init__(parent)
        self.query = query
        self.reply = None
        self.view = None
        self.network = QNetworkAccessManager(self)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(f"線上辭典 · {query}")
        self.resize(880, 660)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        layout.addLayout(row)
        headword = QLabel(query)
        headword.setTextFormat(Qt.TextFormat.PlainText)
        row.addWidget(headword)
        self.sources = QComboBox()
        for key, label, _ in WEB_LOOKUP_SITES:
            self.sources.addItem(label, key)
        idx = self.sources.findData((site_id or "kotobank").lower())
        self.sources.setCurrentIndex(idx if idx >= 0 else 0)
        row.addWidget(self.sources, 1)
        self.original = QPushButton("閱讀原頁")
        self.original.clicked.connect(self.show_original)
        row.addWidget(self.original)
        retry = QPushButton("重試")
        row.addWidget(retry)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.reader = QTextBrowser()
        apply_soft_bold_document(self.reader)
        layout.addWidget(self.reader, 1)
        self.sources.currentIndexChanged.connect(self.load_entry)
        retry.clicked.connect(self.load_entry)

    def url(self) -> QUrl:
        return QUrl(build_web_lookup_url(self.sources.currentData(), self.query))

    def cancel_request(self) -> None:
        if self.reply is not None:
            old, self.reply = self.reply, None
            try:
                old.abort()
            except RuntimeError:
                pass

    def closeEvent(self, event) -> None:
        self.cancel_request()
        if self.view is not None and hasattr(self.view, "stop"):
            try:
                self.view.stop()
            except RuntimeError:
                pass
        super().closeEvent(event)

    def load_entry(self) -> None:
        self.cancel_request()
        if self.sources.currentData() != "kotobank":
            self.reader.clear()
            self.original.hide()
            self.show_original()
            return
        if self.view is not None:
            if hasattr(self.view, "stop"):
                self.view.stop()
            self.view.hide()
        self.original.show()
        self.reader.clear()
        self.reader.show()
        self.status.setText("正在讀取小學館《日中辭典》…")
        request = QNetworkRequest(self.url())
        request.setTransferTimeout(15000)
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        reply = self.network.get(request)
        self.reply = reply
        reply.finished.connect(lambda: self.finish_entry(reply))

    def finish_entry(self, reply) -> None:
        reply.deleteLater()
        if self.reply is not reply:
            return
        self.reply = None
        if reply.error() != QNetworkReply.NetworkError.NoError:
            code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            detail = "查無此詞條" if code == 404 else reply.errorString()
            self.status.setText(f"コトバンク：{detail}。可切換時雨或日英辭典，也可重試。")
            return
        if hasattr(reply, "url"):
            final_host = (reply.url().host() or "").lower()
            if final_host and not (final_host == "kotobank.jp" or final_host.endswith(".kotobank.jp")):
                self.status.setText("來源網域異常，已停止讀取。")
                return
        entry = kotobank_entry(bytes(reply.readAll()).decode("utf-8", errors="replace"))
        self.reader.setPlainText(entry)
        self.status.setText("小學館《日中辭典》第 3 版" if entry else
                            "此頁未辨認到日中詞條。可閱讀原頁，或切換上方辭典。")

    def show_original(self) -> None:
        self.cancel_request()
        if self.view is None:
            try:
                from PySide6.QtWebEngineCore import QWebEnginePage
                from PySide6.QtWebEngineWidgets import QWebEngineView
            except ImportError:
                self.status.setText("顯示原頁需要 Qt WebEngine。可切換到コトバンク日中直接閱讀。")
                return
            class DictionaryPage(QWebEnginePage):
                def acceptNavigationRequest(page, target, navigation_type, is_main_frame):
                    host = self.url().host().lower().removeprefix("www.")
                    target_host = target.host().lower()
                    allowed_host = target_host in {host, "www." + host}
                    # 主框架與內嵌 iframe 一律嚴格限制為 HTTPS + 目標詞典網域，封鎖第三方廣告／追蹤框架
                    if target.scheme() != "https" or not allowed_host:
                        return False
                    return True

                def createWindow(page, _window_type):
                    return None

            self.view = QWebEngineView(self)
            self.view.setPage(DictionaryPage(self.view))
            self.layout().addWidget(self.view, 1)
            self.view.loadFinished.connect(self.page_loaded)
        self.original.hide()
        self.reader.hide()
        self.view.show()
        self.status.setText("正在查詢…")
        self.view.load(self.url())

    def page_loaded(self, ok: bool) -> None:
        if self.view is not None and not self.view.isHidden():
            self.status.setText("" if ok else "未能載入這個詞條。可切換上方辭典查同一個詞，或重試。")


def show_web_dictionary(query: str, parent: QWidget | None, site_id: str) -> None:
    query = sanitize_web_query(query)
    if build_web_lookup_url(site_id, query):
        dialog = WebDictionaryDialog(query, parent, site_id)
        dialog.open()
        dialog.load_entry()
