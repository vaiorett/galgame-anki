"""Qt 執行緒池非阻塞背景工作器（AnkiConnect、詞典下載與匯入）。"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, Signal

from galgame_anki.core.dictionary import DictionaryEngine


class WorkerSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(str, int)


class FunctionWorker(QRunnable):
    """通用背景工作器：執行任意同步函式並透過 Qt Signal 回傳結果。"""

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            result = self.fn(*self.args, **self.kwargs)
            try:
                self.signals.finished.emit(result)
            except RuntimeError:
                pass
        except Exception as exc:
            try:
                self.signals.failed.emit(str(exc))
            except RuntimeError:
                pass


class DictDownloadWorker(QRunnable):
    """在背景下載並匯入開源 Yomitan 詞典，即時回報進度條。"""

    def __init__(self, dictionary: DictionaryEngine, catalog_id: str) -> None:
        super().__init__()
        self.dictionary = dictionary
        self.catalog_id = catalog_id
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            result = self.dictionary.download_and_import_free_dictionary(
                self.catalog_id,
                progress=lambda msg, pct: self.signals.progress.emit(msg, pct),
            )
            self.signals.finished.emit(result)
        except Exception as exc:
            self.signals.failed.emit(str(exc))


class DictBundleDownloadWorker(QRunnable):
    """在背景一鍵下載並匯入全套 5 部黃金推薦詞典。"""

    def __init__(self, dictionary: DictionaryEngine) -> None:
        super().__init__()
        self.dictionary = dictionary
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            results = self.dictionary.download_recommended_bundle(
                progress=lambda msg, pct: self.signals.progress.emit(msg, pct),
            )
            self.signals.finished.emit(results)
        except Exception as exc:
            self.signals.failed.emit(str(exc))


class DictImportWorker(QRunnable):
    """在背景匯入本機 Yomitan .zip 詞典，以執行緒安全 Signal 回報進度。"""

    def __init__(self, dictionary: DictionaryEngine, zip_path: Any) -> None:
        super().__init__()
        self.dictionary = dictionary
        self.zip_path = zip_path
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            result = self.dictionary.import_yomitan_zip(
                self.zip_path,
                progress=lambda msg, pct: self.signals.progress.emit(msg, pct),
            )
            self.signals.finished.emit(result)
        except Exception as exc:
            self.signals.failed.emit(str(exc))
