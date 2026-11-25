"""只在使用者點選網頁辭典時，於 GalAnki 內顯示選定詞頭的查詢。"""

from urllib.parse import urlsplit

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import QDialog, QLabel, QMessageBox, QVBoxLayout, QWidget


def show_web_dictionary(url: str, parent: QWidget) -> None:
    try:
        from PySide6.QtWebEngineCore import QWebEnginePage
        from PySide6.QtWebEngineWidgets import QWebEngineView
    except ImportError:
        QMessageBox.information(parent, "網頁辭典", "此環境未安裝 Qt WebEngine。仍可使用本機詞典。")
        return

    start = urlsplit(url)
    allowed_hosts = {start.hostname}
    if start.hostname == "jisho.org":
        allowed_hosts.add("www.jisho.org")
    elif start.hostname == "kotobank.jp":
        allowed_hosts.add("www.kotobank.jp")

    class DictionaryPage(QWebEnginePage):
        def acceptNavigationRequest(self, target, navigation_type, is_main_frame):
            return target.scheme() == "https" and target.host() in allowed_hosts

        def createWindow(self, _window_type):
            return None

    dialog = QDialog(parent)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dialog.setWindowTitle("網頁辭典")
    dialog.resize(880, 660)
    layout = QVBoxLayout(dialog)
    status = QLabel("正在查詢網頁辭典…")
    status.setWordWrap(True)
    layout.addWidget(status)

    view = QWebEngineView(dialog)
    view.setPage(DictionaryPage(view))
    layout.addWidget(view, 1)

    def update_status(ok: bool) -> None:
        status.setText("" if ok else "網頁辭典暫時無法載入。關閉此視窗可繼續使用本機詞典。")
        status.setVisible(not ok)

    view.loadFinished.connect(update_status)
    dialog.open()
    view.load(QUrl(url))
