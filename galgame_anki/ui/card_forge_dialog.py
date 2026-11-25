"""✨ Refold 精煉製卡確認視窗（遵循 galgame.dev/topic/639 與 SuperMemo 最小資訊原則）。

拒絕把整頁大字典硬搬進 Anki！
1 秒內讓你確認：
1. 這句話裡真正對應的「1 條精煉核心意思」（提供一鍵切換義項膠囊，也可直接用自己的話改寫）
2. 「先理解整句」：可一鍵核對自然中文句意或直接帶入遊戲解包雙語台詞，確保不記憶看不懂的東西
3. 「音畫情景記憶」：自動附帶遊戲解包純淨聲優原聲／mpv 影片原聲與當下畫面 CG 截圖縮圖
4. 嚴格還原攻略三階段（Lv.1 讀音破冰 / Lv.2 1T 音畫語境 / Lv.3 日日單語）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from galgame_anki.adapters.media_utils import play_mp3_bytes_async
from galgame_anki.core.context_meaning import rank_senses_by_context
from galgame_anki.core.grammar_engine import translate_sentence_free
from galgame_anki.ui.pitch_widget import PitchGraphWidget
from galgame_anki.ui.workers import FunctionWorker


@dataclass
class ForgedCardData:
    stage_mode: str
    sentence: str
    target_word: str
    target_reading: str
    pitch_accent: str
    definition_bilingual: str
    definition_monolingual: str
    sentence_translation: str
    include_audio: bool
    audio_clip: tuple[bytes, str] | None
    include_screenshot: bool
    screenshot_clip: tuple[bytes, str] | None
    related_words_html: str = ""


class CardForgeDialog(QDialog):
    """輕量 1 秒精煉製卡視窗：把「硬搬詞典」升級為「理解意思＋音畫記憶＋聯想樹＋原子知識卡」。"""

    def __init__(
        self,
        stage_mode: str,
        sentence_highlighted: str,
        raw_sentence: str,
        target_word: str,
        target_reading: str,
        pitch_accent: str,
        bi_senses: list[str],
        mono_senses: list[str],
        default_bi: str,
        default_mono: str,
        sentence_translation: str,
        audio_clip: tuple[bytes, str] | None,
        screenshot_clip: tuple[bytes, str] | None,
        include_audio_default: bool,
        include_screenshot_default: bool,
        thread_pool: QThreadPool,
        capture_shot_fn: Callable[[], tuple[bytes, str] | None] | None = None,
        alt_lemmas: list[tuple[str, str, str, str]] | None = None,
        audio_source: str = "",
        pack_translation: str = "",
        related_words_html: str = "",
        related_words_preview: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("✨ Refold 精煉製卡（聚焦重點・語境相關・聯想記憶）")
        self.resize(670, 620)

        self._raw_sentence = raw_sentence
        self._pitch_accent = pitch_accent
        self._pack_translation = pack_translation
        effective_trans = sentence_translation or pack_translation
        self._bi_senses = rank_senses_by_context(
            list(bi_senses),
            sentence_translation=effective_trans,
            raw_sentence=raw_sentence,
            target_surface=target_word,
        )
        # 若有語境線索或劇本譯文，優先自動預選語境相關度最高的重點釋義
        resolved_default_bi = self._bi_senses[0] if (self._bi_senses and effective_trans) else default_bi
        self._mono_senses = list(mono_senses)
        self._alt_lemmas = alt_lemmas or []
        self._audio_clip = audio_clip
        self._audio_source = audio_source
        self._related_words_html = related_words_html
        self._related_words_preview = related_words_preview
        self._screenshot_clip = screenshot_clip
        self._thread_pool = thread_pool
        self._capture_shot_fn = capture_shot_fn
        self._workers: list[object] = []
        self.result_data: ForgedCardData | None = None

        self._build_ui(
            stage_mode=stage_mode,
            sentence_highlighted=sentence_highlighted,
            target_word=target_word,
            target_reading=target_reading,
            default_bi=resolved_default_bi,
            default_mono=default_mono,
            sentence_translation=sentence_translation,
            include_audio_default=include_audio_default,
            include_screenshot_default=include_screenshot_default,
        )
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self._confirm)

    def _build_ui(
        self,
        stage_mode: str,
        sentence_highlighted: str,
        target_word: str,
        target_reading: str,
        default_bi: str,
        default_mono: str,
        sentence_translation: str,
        include_audio_default: bool,
        include_screenshot_default: bool,
    ) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # --- 頂部：階段選擇＋最小資訊原則提示 ---
        top = QHBoxLayout()
        tip = QLabel("💡 <b>好卡片原則</b>：一張卡只留本句真正用的 1 條意思＋聲優原聲＋畫面，拒絕死背大字典！")
        tip.setObjectName("MutedLabel")
        tip.setWordWrap(True)
        top.addWidget(tip, 1)

        self.stage_combo = QComboBox()
        self.stage_combo.addItem("Lv.1 讀音破冰卡", "lv1")
        self.stage_combo.addItem("Lv.2 1T 音畫語境卡", "lv2")
        self.stage_combo.addItem("Lv.3 日日單語卡", "lv3")
        idx = self.stage_combo.findData(stage_mode)
        if idx >= 0:
            self.stage_combo.setCurrentIndex(idx)
        top.addWidget(self.stage_combo)
        root.addLayout(top)

        # --- 1. 語境原句 & 焦點詞 ---
        sent_card = QFrame()
        sent_card.setObjectName("CardPanel")
        sl = QVBoxLayout(sent_card)
        sl.setContentsMargins(12, 10, 12, 10)
        sl.setSpacing(6)

        row_word = QHBoxLayout()
        row_word.addWidget(QLabel("目標詞："))
        self.word_edit = QLineEdit(target_word)
        self.word_edit.setMaximumWidth(170)
        row_word.addWidget(self.word_edit)

        row_word.addWidget(QLabel("讀音："))
        self.reading_edit = QLineEdit(target_reading)
        self.reading_edit.setMaximumWidth(170)
        self.reading_edit.textChanged.connect(self._refresh_pitch_graph)
        row_word.addWidget(self.reading_edit)

        parent_theme = getattr(getattr(self.parent(), "config", None), "ui_theme", "sakura")
        self._dark_mode = parent_theme == "starlight"
        self.pitch_widget = PitchGraphWidget(compact=True, dark_mode=self._dark_mode)
        row_word.addWidget(self.pitch_widget)
        row_word.addStretch()
        sl.addLayout(row_word)
        self._refresh_pitch_graph()

        # 若詞形有多個候選原形（如 あった -> ある / 会う / 合う），支援 1 秒直接切換！
        if self._alt_lemmas:
            alt_row = QHBoxLayout()
            alt_lbl = QLabel("🔄 切換其他候選詞：")
            alt_lbl.setObjectName("MutedLabel")
            alt_row.addWidget(alt_lbl)
            for alt_lemma, alt_rd, alt_bi, alt_mono in self._alt_lemmas[:3]:
                ab = QPushButton(f"{alt_lemma}" + (f"({alt_rd})" if alt_rd and alt_rd != alt_lemma else ""))
                ab.setFixedHeight(23)
                ab.clicked.connect(
                    lambda _c=False, lm=alt_lemma, rd=alt_rd, bi=alt_bi, mn=alt_mono: self._apply_alt_lemma(
                        lm, rd, bi, mn
                    )
                )
                alt_row.addWidget(ab)
            alt_row.addStretch()
            sl.addLayout(alt_row)

        sent_row = QHBoxLayout()
        sent_row.addWidget(QLabel("卡片原句："))
        self.sentence_edit = QLineEdit(sentence_highlighted)
        self.sentence_edit.setToolTip("Lv.1 可刪減為簡短詞組（如「南に位置」），Lv.2–3 建議保留完整自然語境句")
        sent_row.addWidget(self.sentence_edit, 1)
        sl.addLayout(sent_row)

        tr_row = QHBoxLayout()
        tr_row.addWidget(QLabel("整句理解："))
        self.trans_edit = QLineEdit(sentence_translation)
        self.trans_edit.setPlaceholderText("用自己的話寫 1 句自然理解，或點右側「🌐 核對句意」…")
        tr_row.addWidget(self.trans_edit, 1)

        self.trans_btn = QPushButton("🌐 核對句意")
        self.trans_btn.setToolTip("按需取得自然中文參考句意（零付費 API），幫助你先完全理解這句話")
        self.trans_btn.clicked.connect(self._fetch_translation)
        tr_row.addWidget(self.trans_btn)
        sl.addLayout(tr_row)

        root.addWidget(sent_card)

        # --- 2. 精煉核心釋義（1 點選義項膠囊，絕不硬搬整頁詞典） ---
        def_card = QFrame()
        def_card.setObjectName("CardPanel")
        dl = QVBoxLayout(def_card)
        dl.setContentsMargins(12, 10, 12, 10)
        dl.setSpacing(6)

        dl_head = QLabel("🎯 本句核心意思（點下方膠囊 1 秒挑選，或直接改寫成最直覺的 1 句話）")
        dl_head.setStyleSheet("font-weight: 700; background: transparent;")
        dl.addWidget(dl_head)

        if self._bi_senses:
            chip_row = QHBoxLayout()
            chip_row.setSpacing(5)
            for idx, sense in enumerate(self._bi_senses[:4]):
                prefix = "★ " if idx == 0 else ""
                short = prefix + sense[:20] + ("…" if len(sense) > 20 else "")
                btn = QPushButton(short)
                btn.setToolTip(
                    f"語境相關度最高 · 點擊填入：{sense}" if idx == 0 else f"點擊填入：{sense}"
                )
                btn.clicked.connect(lambda _c=False, s=sense: self.bi_edit.setPlainText(s))
                chip_row.addWidget(btn)
            chip_row.addStretch()
            dl.addLayout(chip_row)

        self.bi_edit = QTextEdit()
        self.bi_edit.setMaximumHeight(54)
        self.bi_edit.setPlaceholderText("只留這句話中真正對應的 1 行中文核心重點（已自動去除多本詞典重複義項）…")
        self.bi_edit.setPlainText(default_bi)
        dl.addWidget(self.bi_edit)

        mono_lbl = QLabel("🗾 日日單語核心釋義（Lv.3 主顯示 / Lv.1–2 進階折疊）：")
        mono_lbl.setObjectName("MutedLabel")
        dl.addWidget(mono_lbl)

        self.mono_edit = QTextEdit()
        self.mono_edit.setMaximumHeight(44)
        self.mono_edit.setPlaceholderText("已啟用日日單語詞典時自動填入（Lv.1–2 可留空，絕不重複塞入中文）…")
        self.mono_edit.setPlainText(default_mono)
        dl.addWidget(self.mono_edit)

        # CETPath 啟發：🌳 聯想記憶樹附帶勾選與 1 行精簡預覽
        self.include_related_chk = QCheckBox("🌳 附帶聯想記憶樹（同漢字詞族・搭配・自他/同音辨析，收進背面折疊區）")
        self.include_related_chk.setChecked(bool(self._related_words_html))
        self.include_related_chk.setEnabled(bool(self._related_words_html))
        dl.addWidget(self.include_related_chk)
        if self._related_words_preview:
            rel_preview_lbl = QLabel(self._related_words_preview)
            rel_preview_lbl.setObjectName("MutedLabel")
            rel_preview_lbl.setWordWrap(True)
            dl.addWidget(rel_preview_lbl)

        root.addWidget(def_card)

        # --- 3. 音畫情景記憶區（聲優原聲 ＋ 遊戲/動畫畫面 CG） ---
        media_card = QFrame()
        media_card.setObjectName("CardPanel")
        ml = QHBoxLayout(media_card)
        ml.setContentsMargins(12, 10, 12, 10)
        ml.setSpacing(12)

        # 左側：聲優原聲語音控制
        aud_col = QVBoxLayout()
        aud_col.setSpacing(6)
        self.include_audio_chk = QCheckBox("🔊 附帶聲優原聲語音 (.mp3)")
        self.include_audio_chk.setChecked(include_audio_default)
        aud_col.addWidget(self.include_audio_chk)

        self.audio_status_lbl = QLabel("")
        self.audio_status_lbl.setObjectName("MutedLabel")
        self.audio_status_lbl.setWordWrap(True)
        aud_col.addWidget(self.audio_status_lbl)

        aud_btns = QHBoxLayout()
        self.play_btn = QPushButton("▶ 試聽")
        self.play_btn.clicked.connect(self._play_audio)
        aud_btns.addWidget(self.play_btn)
        aud_btns.addStretch()
        aud_col.addLayout(aud_btns)
        ml.addLayout(aud_col, 1)

        # 右側：遊戲/動畫 CG 截圖預覽
        shot_col = QVBoxLayout()
        shot_col.setSpacing(5)
        self.include_shot_chk = QCheckBox("🖼 附帶當下畫面 CG")
        self.include_shot_chk.setChecked(include_screenshot_default)
        shot_col.addWidget(self.include_shot_chk)

        self.shot_preview = QLabel("尚未截取畫面")
        self.shot_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.shot_preview.setFixedSize(168, 95)
        self.shot_preview.setStyleSheet(
            "border: 1px dashed rgba(140,120,150,0.45); border-radius: 8px; font-size: 11px;"
        )
        shot_col.addWidget(self.shot_preview)

        self.reshot_btn = QPushButton("📷 重拍畫面")
        self.reshot_btn.setEnabled(self._capture_shot_fn is not None)
        self.reshot_btn.clicked.connect(self._recapture_shot)
        shot_col.addWidget(self.reshot_btn)
        ml.addLayout(shot_col)

        root.addWidget(media_card)

        # --- 底部確認列 ---
        bottom = QHBoxLayout()
        hint_k = QLabel("快捷鍵 Ctrl+Enter 立即收錄")
        hint_k.setObjectName("MutedLabel")
        bottom.addWidget(hint_k)
        bottom.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        bottom.addWidget(cancel_btn)

        self.confirm_btn = QPushButton("✨ 寫入 Anki（音畫精煉記憶卡）")
        self.confirm_btn.setObjectName("MineBtn")
        self.confirm_btn.clicked.connect(self._confirm)
        bottom.addWidget(self.confirm_btn)
        root.addLayout(bottom)

        self._refresh_audio_ui()
        self._refresh_shot_ui()

    def _refresh_audio_ui(self) -> None:
        if self._audio_clip:
            mp3_bytes, _ = self._audio_clip
            if self._audio_source == "voice_pack":
                self.audio_status_lbl.setText(
                    f"✨ 原包純淨人聲已就緒（零 BGM／零音效 · {len(mp3_bytes) / 1024:.1f} KB · 可試聽）"
                )
            elif self._audio_source == "mpv":
                self.audio_status_lbl.setText(
                    f"🎬 mpv 原聲切片已就緒（{len(mp3_bytes) / 1024:.1f} KB · 可試聽）"
                )
            else:
                self.audio_status_lbl.setText(f"✅ 原聲已就緒（{len(mp3_bytes) / 1024:.1f} KB · 可先試聽）")
            self.play_btn.setEnabled(True)
        elif self._audio_source == "silent":
            self.audio_status_lbl.setText("🔇 劇本確認本句為旁白／無配音角色")
            self.play_btn.setEnabled(False)
        else:
            self.audio_status_lbl.setText("⚠ 未找到此句純淨人聲（可至「📦 遊戲解包純淨人聲」建立遊戲索引，或本句無語音）")
            self.play_btn.setEnabled(False)

    def _refresh_shot_ui(self) -> None:
        if self._screenshot_clip:
            img_bytes, _ = self._screenshot_clip
            pix = QPixmap()
            if pix.loadFromData(img_bytes):
                scaled = pix.scaled(
                    self.shot_preview.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.shot_preview.setPixmap(scaled)
                return
        self.shot_preview.setPixmap(QPixmap())
        self.shot_preview.setText("無畫面快照\n（可點下方重拍）")

    def _play_audio(self) -> None:
        if self._audio_clip:
            play_mp3_bytes_async(self._audio_clip[0])

    def _recapture_shot(self) -> None:
        if not self._capture_shot_fn:
            return
        self.reshot_btn.setEnabled(False)
        worker = FunctionWorker(self._capture_shot_fn)
        worker.signals.finished.connect(self._on_reshot_done)
        worker.signals.failed.connect(lambda _e: self.reshot_btn.setEnabled(True))
        self._workers.append(worker)
        self._thread_pool.start(worker)

    def _on_reshot_done(self, shot: tuple[bytes, str] | None) -> None:
        self.reshot_btn.setEnabled(True)
        if shot:
            self._screenshot_clip = shot
            self.include_shot_chk.setChecked(True)
        self._refresh_shot_ui()

    def _fetch_translation(self) -> None:
        if self._pack_translation:
            self._on_trans_ok(self._pack_translation)
            return
        self.trans_btn.setEnabled(False)
        self.trans_btn.setText("翻譯中…")
        worker = FunctionWorker(translate_sentence_free, self._raw_sentence)
        worker.signals.finished.connect(self._on_trans_ok)
        worker.signals.failed.connect(self._on_trans_fail)
        self._workers.append(worker)
        self._thread_pool.start(worker)

    def _refresh_pitch_graph(self) -> None:
        reading = self.reading_edit.text().strip() or self.word_edit.text().strip()
        self.pitch_widget.set_pitch(reading, self._pitch_accent, dark_mode=self._dark_mode)

    def _apply_alt_lemma(self, lemma: str, reading: str, bi: str, mono: str) -> None:
        self.word_edit.setText(lemma)
        self.reading_edit.setText(reading)
        self._refresh_pitch_graph()
        if bi:
            self.bi_edit.setPlainText(bi)
        self.mono_edit.setPlainText(mono)

    def _on_trans_ok(self, text: str) -> None:
        self.trans_btn.setEnabled(True)
        self.trans_btn.setText("🌐 核對句意")
        if text:
            self.trans_edit.setText(text)
            # 取得本句自然譯文後，若原先有多個義項，自動按語境重疊度將最符合這句話的義項排第 1
            if self._bi_senses:
                reranked = rank_senses_by_context(
                    self._bi_senses,
                    sentence_translation=text,
                    raw_sentence=self._raw_sentence,
                    target_surface=self.word_edit.text().strip(),
                )
                if reranked and reranked[0] != self._bi_senses[0]:
                    self._bi_senses = reranked
                    self.bi_edit.setPlainText(reranked[0])

    def _on_trans_fail(self, _err: str) -> None:
        self.trans_btn.setEnabled(True)
        self.trans_btn.setText("🌐 核對句意")
        self.trans_edit.setPlaceholderText("離線無法連線，可直接輸入你自己的理解或貼上 Gemini/GPT 譯文")

    def _confirm(self) -> None:
        self.result_data = ForgedCardData(
            stage_mode=str(self.stage_combo.currentData() or "lv2"),
            sentence=self.sentence_edit.text().strip() or self._raw_sentence,
            target_word=self.word_edit.text().strip(),
            target_reading=self.reading_edit.text().strip(),
            pitch_accent=self._pitch_accent,
            definition_bilingual=self.bi_edit.toPlainText().strip(),
            definition_monolingual=self.mono_edit.toPlainText().strip(),
            sentence_translation=self.trans_edit.text().strip(),
            include_audio=self.include_audio_chk.isChecked(),
            audio_clip=self._audio_clip if self.include_audio_chk.isChecked() else None,
            include_screenshot=self.include_shot_chk.isChecked(),
            screenshot_clip=self._screenshot_clip if self.include_shot_chk.isChecked() else None,
            related_words_html=self._related_words_html if self.include_related_chk.isChecked() else "",
        )
        self.accept()
