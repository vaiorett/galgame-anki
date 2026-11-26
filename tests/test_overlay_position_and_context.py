import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QSize, QThreadPool, Qt
from PySide6.QtGui import QContextMenuEvent, QGuiApplication, QImage, QMouseEvent, QWheelEvent
from PySide6.QtQml import QJSEngine
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from galgame_anki.adapters.window_pin import build_kwin_pin_script
from galgame_anki.config import AppConfig
from galgame_anki.core.context_meaning import explain_lookup_in_sentence
from galgame_anki.core.dictionary import DictEntry, LookupMatch, _strip_jitendex_pos_prefix
from galgame_anki.core.sentence_store import SentenceRecord
from galgame_anki.core.tokenizer import Token, TokenizedSentence
from galgame_anki.ui.lookup_popup import LookupDetailPanel, OverlayLookupPopup
from galgame_anki.ui.overlay_window import (
    OVERLAY_MIN_HEIGHT,
    OVERLAY_MIN_WIDTH,
    OverlaySizeDialog,
    OverlayWindow,
    _lookup_popup_geometry,
)
from galgame_anki.ui.settings_dialog import SettingsDialog
from galgame_anki.ui.theme import build_stylesheet


class TestOverlayPositionAndContext(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_popup_geometry_uses_free_space_for_each_screen(self):
        area = QRect(-1920, 80, 1920, 1000)
        for subtitles, side in (
            (QRect(-1460, 933, 820, 128), "above"),
            (QRect(-1460, 84, 820, 128), "below"),
            (QRect(-1430, 200, 490, 780), "left"),
            (QRect(-1904, 212, 590, 726), "right"),
        ):
            with self.subTest(side=side):
                rect = _lookup_popup_geometry(area, subtitles, QSize(385, 310), subtitles.center().x())
                self.assertTrue(area.contains(rect))
                self.assertFalse(subtitles.intersects(rect))
                if side == "above":
                    self.assertLess(rect.bottom(), subtitles.top())
                if side == "below":
                    self.assertGreater(rect.top(), subtitles.bottom())
                if side == "left":
                    self.assertLess(rect.right(), subtitles.left())
                if side == "right":
                    self.assertGreater(rect.left(), subtitles.right())
        self.assertFalse(_lookup_popup_geometry(area, area, QSize(385, 310), 0).isValid())

    def test_kwin_resize_keeps_bottom_anchor_but_leaves_native_popup_alone(self):
        js = QJSEngine()
        js.evaluate("""
            var overlay = {caption: "GalAnki 覆蓋層", frameGeometry: {x: -1540, y: -210, width: 820, height: 128}};
            var popup = {caption: "GalAnki 查詞浮窗", frameGeometry: {x: -1320, y: -520, width: 385, height: 310}};
            var KWin = {MaximizeArea: 0};
            var workspace = {
                windowList: () => [overlay, popup],
                clientArea: () => ({x: -1920, y: -1200, width: 1920, height: 1120})
            };
        """)
        script = js.evaluate(build_kwin_pin_script())
        self.assertFalse(script.isError(), script.toString())
        js.evaluate("overlay.frameGeometry.height = 250; rememberOverlayPosition(overlay);")
        self.assertEqual(js.evaluate("overlay.frameGeometry.y").toInt(), -336)
        self.assertEqual(js.evaluate("popup.frameGeometry.y").toInt(), -520)
        js.evaluate("overlay.frameGeometry.x = -1800; rememberOverlayPosition(overlay);")
        self.assertEqual(js.evaluate("overlay.frameGeometry.x").toInt(), -1800)

    def test_kwin_does_not_reanchor_on_subtitle_resize_during_drag(self):
        js = QJSEngine()
        js.evaluate("""
            var overlay = {caption: "GalAnki 覆蓋層", move: true,
                           frameGeometry: {x: 494, y: 734, width: 300, height: 60}};
            var KWin = {MaximizeArea: 0};
            var workspace = {windowList: () => [overlay],
                clientArea: () => ({x: 0, y: 0, width: 800, height: 800})};
        """)
        script = js.evaluate(build_kwin_pin_script())
        self.assertFalse(script.isError(), script.toString())
        js.evaluate("overlay.frameGeometry = {x:260, y:190, width:120, height:70}; rememberOverlayPosition(overlay)")
        self.assertEqual(js.evaluate("overlay.frameGeometry.x").toInt(), 260)
        self.assertEqual(js.evaluate("overlay.frameGeometry.y").toInt(), 190)

    def test_transparent_drag_clears_reused_frame_and_only_paints_corner_arc(self):
        previous_style = self.app.styleSheet()
        self.addCleanup(self.app.setStyleSheet, previous_style)
        self.app.setStyleSheet(build_stylesheet("starlight"))
        overlay = OverlayWindow()
        self.addCleanup(overlay.close)
        overlay.set_backdrop("transparent", save=False)
        overlay.show()

        def show_word(word):
            tok = Token(word, word, "", 0, len(word))
            overlay.show_record(SentenceRecord(record_id=1, raw_text=word, source="mpv",
                    tokenized=TokenizedSentence(word, [tok], 0, False, word)))
            self.app.processEvents()

        show_word("前前前前")

        def render(image):
            overlay.render(image, renderFlags=QWidget.RenderFlag.DrawChildren)

        frame = QImage(overlay.size(), QImage.Format.Format_ARGB32_Premultiplied)
        frame.fill(Qt.GlobalColor.magenta)
        render(frame)
        before = frame.copy()
        overlay.move(overlay.pos() + QPoint(80, 12))
        show_word("日日日日")
        self.assertEqual(overlay.size(), frame.size())
        render(frame)
        fresh = QImage(frame.size(), frame.format())
        fresh.fill(Qt.GlobalColor.transparent)
        render(fresh)
        self.assertNotEqual(before, fresh)
        self.assertEqual(frame, fresh)
        self.assertEqual(frame.pixelColor(0, 0).alpha(), 0)

        mark = overlay.corner_mark
        area = fresh.copy(QRect(mark.mapTo(overlay, QPoint()), mark.size()))
        self.assertEqual(area.size(), QSize(7, 7))
        touched = sum(area.pixelColor(x, y).alpha() > 0 for x in range(7) for y in range(7))
        self.assertGreater(touched, 2)
        self.assertLess(touched, 22)
        self.assertEqual(area.pixelColor(0, 0).alpha(), 0)

    def test_kwin_keeps_live_overlay_visible_at_every_drag_position(self):
        js = QJSEngine()
        js.evaluate("""
            function signal() { return {fire(){}, connect(fn){ this.fire = fn; }}; }
            function windowStub(caption) {
                return {caption, opacity: 0.83, move: false,
                        frameGeometry: {x: 0, y: 0, width: 320, height: 90}, frameGeometryChanged: signal(),
                        interactiveMoveResizeStarted: signal(), interactiveMoveResizeFinished: signal()};
            }
            var overlay = windowStub("GalAnki 覆蓋層"), player = windowStub("mpv");
            var workspace = {windowList: () => [overlay, player]};
        """)
        script = js.evaluate(build_kwin_pin_script() + "\nworkspace.savedX = () => savedOverlayGeom.x;")
        self.assertFalse(script.isError(), script.toString())
        js.evaluate("overlay.interactiveMoveResizeStarted.fire()")
        self.assertAlmostEqual(js.evaluate("overlay.opacity").toNumber(), 0.83)
        js.evaluate("overlay.move = player.move = true; overlay.interactiveMoveResizeStarted.fire(); player.interactiveMoveResizeStarted.fire()")
        for x in (25, 80, 103):
            update = js.evaluate(f"overlay.frameGeometry.x = {x}; overlay.frameGeometryChanged.fire()")
            self.assertFalse(update.isError(), update.toString())
            self.assertAlmostEqual(js.evaluate("overlay.opacity").toNumber(), 0.83)
            self.assertEqual(js.evaluate("workspace.savedX()").toInt(), x)
        self.assertAlmostEqual(js.evaluate("player.opacity").toNumber(), 0.83)
        js.evaluate("overlay.interactiveMoveResizeStarted.fire(); overlay.move = false; overlay.interactiveMoveResizeFinished.fire()")
        self.assertAlmostEqual(js.evaluate("overlay.opacity").toNumber(), 0.83)

    def test_native_wayland_popup_uses_full_parent_height_and_no_vertical_slide(self):
        overlay = OverlayWindow()
        self.addCleanup(overlay.close)
        overlay.resize(330, 90)
        overlay.show()
        self.app.processEvents()
        tok = Token("単語", "単語", "たんご", 0, 2)
        overlay.popup.popup_for_token(tok, "bilingual", False, show=False)
        overlay._show_wayland_lookup(overlay.mapToGlobal(QPoint(102, 12)))
        window = overlay.popup.windowHandle()
        self.assertIsNotNone(window)
        self.assertEqual(window.transientParent(), overlay.windowHandle())
        self.assertEqual(window.property("_q_waylandPopupAnchorRect"), QRect(102, 0, 1, 90))
        self.assertEqual(window.property("_q_waylandPopupGravity"), (Qt.Edge.TopEdge | Qt.Edge.RightEdge).value)
        self.assertEqual(window.property("_q_waylandPopupConstraintAdjustment"), 9)

    def test_wayland_hover_and_move_keep_subtitles_on_one_surface(self):
        overlay = OverlayWindow()
        self.addCleanup(overlay.close)
        overlay.set_backdrop("transparent", save=False)
        overlay.show()
        self.app.processEvents()
        self.assertIsNone(overlay.container.windowHandle())
        with patch.object(QGuiApplication, "platformName", return_value="wayland"):
            for i, word in enumerate(("字幕", "次の台詞です", "短い")):
                tok = Token(word, word, "", 0, len(word))
                overlay.show_record(SentenceRecord(record_id=i, raw_text=word, source="mpv",
                        tokenized=TokenizedSentence(word, [tok], 0, False, word)))
                overlay._on_token_hovered(tok, overlay.mapToGlobal(QPoint(24, 16)))
                self.app.processEvents()
                self.assertTrue(overlay.popup.isVisible())
                self.assertIsNotNone(overlay.popup.windowHandle())
                self.assertIsNone(overlay.container.windowHandle())
                overlay.move(overlay.pos() + QPoint(15, 8))
                self.app.processEvents()
                self.assertFalse(overlay.popup.isVisible())
                self.assertIsNone(overlay.container.windowHandle())

    def test_subtitle_size_stays_fixed_and_long_lines_scroll_without_a_bar(self):
        overlay = OverlayWindow()
        self.addCleanup(overlay.close)
        overlay.restore_saved_state(40, 60, 480, 100, False, True, True)
        overlay.show()

        def show_words(words):
            tokens = []
            start = 0
            for word in words:
                tokens.append(Token(word, word, "", start, start + len(word)))
                start += len(word)
            sentence = "".join(words)
            overlay.show_record(SentenceRecord(record_id=1, raw_text=sentence, source="mpv",
                    tokenized=TokenizedSentence(sentence, tokens, 0, False, sentence)))
            self.app.processEvents()
            return overlay.size()

        fixed = QSize(480, 100)
        origin = overlay.pos()
        self.assertEqual(show_words(["はい"]), fixed)
        self.assertEqual(show_words(["長い字幕を", "読みます。"] * 24), fixed)
        scroll = overlay.scroll_area
        bar = scroll.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        self.assertFalse(bar.isVisible())
        point = scroll.viewport().rect().center()
        wheel = QWheelEvent(point, scroll.viewport().mapToGlobal(point), QPoint(), QPoint(0, -120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.ScrollUpdate, False)
        self.app.sendEvent(scroll.viewport(), wheel)
        self.assertGreater(bar.value(), 0)
        bar.setValue(bar.maximum())
        last = overlay.sentence_widget.layout().itemAt(47).widget()
        self.assertTrue(scroll.viewport().rect().contains(last.mapTo(scroll.viewport(), last.rect().center())))
        self.assertEqual(show_words(["はい"]), fixed)
        self.assertEqual(bar.value(), 0)
        overlay._toggle_inline_trans(True)
        overlay.set_inline_translation(overlay._current_record, "這是一段很長的中文譯文。" * 48)
        overlay._adjust_font_size(2)
        self.app.processEvents()
        self.assertEqual(overlay.size(), fixed)
        self.assertEqual(overlay.pos(), origin)
        self.assertFalse(bar.isVisible())
        self.assertTrue(overlay.corner_mark.isVisible())
        self.assertFalse(hasattr(overlay, "size_grip"))

    def test_top_and_bottom_hover_and_corner_controls(self):
        overlay = OverlayWindow()
        self.addCleanup(overlay.close)
        overlay.set_backdrop("transparent", save=False)
        tok = Token("単語", "単語", "たんご", 0, 2)
        overlay.show_record(SentenceRecord(record_id=1, raw_text=tok.surface, source="mpv",
                tokenized=TokenizedSentence(tok.surface, [tok], 0, False, tok.surface)))
        screen = overlay.screen().availableGeometry()
        overlay.resize(min(620, screen.width() - 30), 110)
        overlay.show()
        for y in (screen.top() + 6, screen.bottom() - overlay.height() - 8):
            overlay.move(screen.left() + 10, y)
            self.app.processEvents()
            overlay._on_token_hovered(tok, overlay.mapToGlobal(QPoint(80, 48)))
            self.assertTrue(overlay.popup.isVisible())
            self.assertFalse(overlay.geometry().intersects(overlay.popup.geometry()))
        overlay.popup.hide()
        button = overlay.settings_button
        QTest.mouseMove(overlay.container, QPoint(8, 8))
        self.app.processEvents()
        idle = button.grab().toImage()
        at_corner = button.mapTo(overlay, button.rect().center())
        self.assertIs(overlay.childAt(at_corner), button)
        QTest.mouseMove(button, button.rect().center())
        self.app.processEvents()
        self.assertNotEqual(button.grab().toImage(), idle)
        self.assertFalse(overlay._options_menu.isVisible())
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        self.assertTrue(overlay._options_menu.isVisible())
        overlay._options_menu.hide()
        self.assertTrue(overlay.corner_mark.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        self.assertIn("background-color: transparent;", overlay.container.styleSheet())
        self.assertIn("border: 1px solid transparent;", overlay.container.styleSheet())
        self.app.sendEvent(overlay, QContextMenuEvent(QContextMenuEvent.Reason.Mouse,
                           at_corner, overlay.mapToGlobal(at_corner)))
        self.assertTrue(overlay._options_menu.isVisible())
        playback = next(a.menu() for a in overlay._options_menu.actions() if a.text() == "播放與字幕")
        previous = next(a for a in playback.actions() if a.text() == "上一句")
        steps = []
        overlay.prev_requested.connect(lambda: steps.append(-1))
        previous.trigger()
        self.assertEqual(steps, [-1])
        overlay._options_menu.hide()
        overlay.show_toast("底色：透明字幕")
        self.assertTrue(overlay.toast_label.isVisible())

    def test_wty_examples_and_etymology_do_not_become_current_senses(self):
        ko = DictEntry("壊れる", "こわれる", "v1", [
            "壊れる【こわれる】\n自下一\n1. 坏，碎。\nコップが壊れる\n杯子碎了。\n2. (计划等)告吹，破裂。"
        ], "wty-ja-zh", "bilingual")
        sentence = "だって地球が壊れたら…"
        match = LookupMatch("壊れたら", "壊れる", "こわれる", ("假定（〜たら）",), bilingual_entries=[ko], pitches=["3"])
        tok = Token("壊れたら", match.lemma, match.reading, 6, 11, verb_type="一段", lookup=match)
        senses = [s.text for s in match.senses_by_language()["zh"]]
        self.assertNotIn("杯子碎了", " ".join(senses))
        self.assertEqual(explain_lookup_in_sentence(tok, sentence, "")[0], "如果地球毀了……")
        panel = LookupDetailPanel()
        self.addCleanup(panel.close)
        panel.show_token(tok, sentence=sentence)
        self.assertTrue(panel.bi_view.toPlainText().startswith("本句義 如果地球毀了"))
        self.assertNotIn(sentence, panel.bi_view.toPlainText())
        self.assertIn("壊れたら", panel.bi_view.toPlainText())
        self.assertTrue(panel.pitch_widget.isHidden())
        panel.pitch_button.click()
        self.assertFalse(panel.pitch_widget.isHidden())

        na = DictEntry("撫でる", "なでる", "v1", [
            "词源繼承自上古日語動詞撫づ。\n發展\n自然地從上古日語的下二段活用發展為一段。\n撫摸；愛撫"
        ], "wty-ja-zh", "bilingual")
        match = LookupMatch("撫でた", "撫でる", "なでる", ("過去形",), bilingual_entries=[na])
        tok = Token(match.surface, match.lemma, match.reading, 2, 5, lookup=match)
        self.assertIn("撫摸了", explain_lookup_in_sentence(tok, "猫を撫でた。", "")[0])

    def test_inflection_note_does_not_hide_english_fallback(self):
        en = DictEntry("塗す", "まぶす", "v5s", ["to cover (with)"], "JMdict", "bilingual")
        match = LookupMatch("まぶした", "塗す", "まぶす", ("過去形",), bilingual_entries=[en])
        tok = Token(match.surface, match.lemma, match.reading, 3, 7, lookup=match)
        popup = OverlayLookupPopup()
        self.addCleanup(popup.close)
        popup.popup_for_token(tok, "bilingual", False, sentence="粗塩をまぶした。")
        self.assertIn("日英備援", popup.def_label.text())
        self.assertEqual(_strip_jitendex_pos_prefix("5-dan (spec.)intransitiveto go"), "to go")

    def test_kwin_does_not_reanchor_during_interactive_resize(self):
        js = QJSEngine()
        js.evaluate("""
            var overlay = {caption: "GalAnki 覆蓋層", move: false, resize: true,
                           frameGeometry: {x: 100, y: 660, width: 820, height: 128}};
            var KWin = {MaximizeArea: 0};
            var workspace = {windowList: () => [overlay],
                clientArea: () => ({x: 0, y: 0, width: 1280, height: 800})};
        """)
        script = js.evaluate(build_kwin_pin_script())
        self.assertFalse(script.isError(), script.toString())
        js.evaluate("overlay.frameGeometry = {x: 100, y: 660, width: 960, height: 170}; rememberOverlayPosition(overlay);")
        self.assertEqual(js.evaluate("overlay.frameGeometry.x").toInt(), 100)
        self.assertEqual(js.evaluate("overlay.frameGeometry.y").toInt(), 660)
        self.assertEqual(js.evaluate("overlay.frameGeometry.width").toInt(), 960)
        self.assertEqual(js.evaluate("overlay.frameGeometry.height").toInt(), 170)

    def test_bottom_right_prev_next_replay_controls_and_focus_safety(self):
        overlay = OverlayWindow()
        self.addCleanup(overlay.close)
        overlay.move(120, 120)
        overlay.show()

        player = QWidget()
        self.addCleanup(player.close)
        player.showFullScreen()
        player.activateWindow()
        self.app.processEvents()
        self.assertIs(self.app.focusWindow(), player.windowHandle())

        # 鼠標不在覆蓋層上方時，右下角按鈕必須隱藏
        QTest.mouseMove(overlay, QPoint(-30, -30))
        self.app.processEvents()
        self.assertFalse(overlay.corner_controls.isVisible())

        # 鼠標移入覆蓋層上方時，右下角按鈕立即顯示並貼齊右下角
        QTest.mouseMove(overlay.container, QPoint(24, 24))
        self.app.processEvents()
        self.assertTrue(overlay.corner_controls.isVisible())

        for size in (QSize(820, 128), QSize(260, 66)):
            overlay.set_overlay_size(size.width(), size.height(), save=False)
            self.app.processEvents()
            dock = overlay.corner_controls
            self.assertTrue(dock.isVisible())
            self.assertTrue(overlay.container.rect().contains(dock.geometry()))
            self.assertFalse(dock.geometry().intersects(overlay.corner_mark.geometry()))
            self.assertGreater(dock.geometry().right(), overlay.container.width() // 2)
            self.assertGreater(dock.geometry().bottom(), overlay.container.height() // 2)

        for btn in (overlay.prev_button, overlay.replay_button, overlay.next_button, overlay.settings_button):
            self.assertEqual(btn.focusPolicy(), Qt.FocusPolicy.NoFocus)

        events = []
        overlay.prev_requested.connect(lambda: events.append("prev"))
        overlay.next_requested.connect(lambda: events.append("next"))
        overlay.replay_requested.connect(lambda: events.append("mpv_replay"))
        overlay.audio_preview_requested.connect(lambda: events.append("voice_replay"))

        tok = Token("世界", "世界", "せかい", 0, 2)
        overlay.show_record(SentenceRecord(record_id=1, raw_text="世界", source="mpv",
                tokenized=TokenizedSentence("世界", [tok], 0, False, "世界")))
        overlay._on_token_hovered(tok, overlay.mapToGlobal(QPoint(60, 24)))
        self.assertTrue(overlay.popup.isVisible())

        QTest.mouseClick(overlay.prev_button, Qt.MouseButton.LeftButton)
        self.assertFalse(overlay.popup.isVisible())
        QTest.mouseClick(overlay.next_button, Qt.MouseButton.LeftButton)
        # 未連接 mpv 時重播按鈕觸發聲優原聲試聽；連接 mpv 後改觸發 mpv 重播本句
        overlay.set_mpv_connected(False)
        QTest.mouseClick(overlay.replay_button, Qt.MouseButton.LeftButton)
        overlay.set_mpv_connected(True)
        QTest.mouseClick(overlay.replay_button, Qt.MouseButton.LeftButton)

        self.assertEqual(events, ["prev", "next", "voice_replay", "mpv_replay"])
        self.assertIs(self.app.focusWindow(), player.windowHandle())

        # 鼠標離開覆蓋層後，右下角按鈕再次隱藏
        QTest.mouseMove(overlay, QPoint(-30, -30))
        self.app.processEvents()
        self.assertFalse(overlay.corner_controls.isVisible())

    def test_overlay_custom_size_drag_wayland_fallback_lock_and_dialog(self):
        overlay = OverlayWindow()
        self.addCleanup(overlay.close)
        overlay.restore_saved_state(40, 60, 360, 76, False, True, True)
        overlay.show()
        self.app.processEvents()
        self.assertEqual(overlay.size(), QSize(360, 76))

        # 角落與四邊熱區偵測：右下角可縮放，但按鈕顯示時不被縮放攔截，鎖定後完全停用
        QTest.mouseMove(overlay.container, QPoint(24, 24))
        self.app.processEvents()
        corner_pt = QPoint(overlay.width() - 3, overlay.height() - 3)
        self.assertEqual(overlay._resize_edges_at(corner_pt), Qt.Edge.RightEdge | Qt.Edge.BottomEdge)
        self.assertEqual(overlay._resize_edges_at(QPoint(2, overlay.height() // 2)), Qt.Edge.LeftEdge)
        self.assertEqual(overlay._resize_edges_at(QPoint(overlay.width() // 2, 2)), Qt.Edge.TopEdge)
        btn_pt = overlay.prev_button.mapTo(overlay, overlay.prev_button.rect().center())
        self.assertEqual(overlay._resize_edges_at(btn_pt), Qt.Edge(0))

        overlay._toggle_lock(True)
        self.assertEqual(overlay._resize_edges_at(corner_pt), Qt.Edge(0))
        overlay._toggle_lock(False)

        # Wayland 原生 startSystemResize 呼叫路徑驗證
        with patch.object(overlay.windowHandle(), "startSystemResize", return_value=True) as sys_resize:
            press = QMouseEvent(
                QEvent.Type.MouseButtonPress,
                QPointF(corner_pt),
                QPointF(overlay.mapToGlobal(corner_pt)),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
            overlay.mousePressEvent(press)
            sys_resize.assert_called_once_with(Qt.Edge.RightEdge | Qt.Edge.BottomEdge)
        overlay._finish_interactive_resize()

        # 非 Wayland / fallback 手動拖曳縮放 + 持久化 state_saved 訊號
        saved_states = []
        overlay.state_saved.connect(saved_states.append)
        with patch.object(overlay.windowHandle(), "startSystemResize", return_value=False):
            start_local = QPoint(overlay.width() - 3, overlay.height() - 3)
            start_global = overlay.mapToGlobal(start_local)
            end_global = start_global + QPoint(140, 44)
            overlay.mousePressEvent(QMouseEvent(
                QEvent.Type.MouseButtonPress, QPointF(start_local), QPointF(start_global),
                Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
            ))
            overlay.mouseMoveEvent(QMouseEvent(
                QEvent.Type.MouseMove, QPointF(start_local + QPoint(140, 44)), QPointF(end_global),
                Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
            ))
            overlay.mouseReleaseEvent(QMouseEvent(
                QEvent.Type.MouseButtonRelease, QPointF(start_local + QPoint(140, 44)), QPointF(end_global),
                Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            ))
        self.assertEqual(overlay.size(), QSize(500, 120))
        self.assertFalse(overlay._resizing_active)
        self.assertTrue(saved_states)
        self.assertEqual((saved_states[-1]["width"], saved_states[-1]["height"]), (500, 120))

        # OverlaySizeDialog 預設尺寸、滑桿同步與取消還原 / 套用保存
        dlg = OverlaySizeDialog(500, 120, theme_name="starlight")
        self.addCleanup(dlg.close)
        emitted = []
        dlg.size_changed.connect(lambda w, h: emitted.append((w, h)))
        dlg.width_spin.setValue(960)
        dlg.height_slider.setValue(164)
        self.assertEqual(dlg.width_slider.value(), 960)
        self.assertEqual(dlg.height_spin.value(), 164)
        self.assertEqual(dlg.selected_size(), (960, 164))
        self.assertIn((960, 164), emitted)
        dlg.preset_buttons[0].click()
        self.assertEqual(dlg.selected_size(), (520, 88))
        dlg.reset_btn.click()
        self.assertEqual(dlg.selected_size(), (820, 128))

        # open_size_dialog 取消應還原原尺寸；套用應保存自訂尺寸
        def _reject_after_preview(self_dlg):
            self_dlg._set_values(660, 180, emit=True)
            return QDialog.DialogCode.Rejected

        with patch.object(OverlaySizeDialog, "exec", _reject_after_preview):
            overlay.open_size_dialog()
        self.assertEqual(overlay.size(), QSize(500, 120))

        def _accept_after_preview(self_dlg):
            self_dlg._set_values(680, 168, emit=True)
            return QDialog.DialogCode.Accepted

        with patch.object(OverlaySizeDialog, "exec", _accept_after_preview):
            overlay.open_size_dialog()
        self.assertEqual(overlay.size(), QSize(680, 168))
        self.assertEqual((saved_states[-1]["width"], saved_states[-1]["height"]), (680, 168))
        self.assertIn("680 × 168", overlay.act_resize.text())

        # 下限夾制與 SettingsDialog 同步儲存
        overlay.set_overlay_size(50, 10, save=False)
        self.assertEqual(overlay.size(), QSize(OVERLAY_MIN_WIDTH, OVERLAY_MIN_HEIGHT))

        with tempfile.TemporaryDirectory() as tmp:
            cfg = AppConfig(overlay_width=940, overlay_height=168)
            with patch.object(AppConfig, "config_path", return_value=Path(tmp) / "config.json"):
                settings = SettingsDialog(cfg, QThreadPool.globalInstance())
                self.addCleanup(settings.close)
                self.assertEqual(settings.overlay_width_spin.value(), 940)
                self.assertEqual(settings.overlay_height_spin.value(), 168)
                settings.overlay_width_spin.setValue(1020)
                settings.overlay_height_spin.setValue(148)
                settings._save_and_accept()
                self.assertEqual((cfg.overlay_width, cfg.overlay_height), (1020, 148))

    def test_overlay_text_alignment_left_and_center(self):
        overlay = OverlayWindow()
        self.addCleanup(overlay.close)
        overlay.restore_saved_state(40, 60, 620, 140, False, True, True, text_align="left")
        overlay.show()

        # 單行測試：左對齊靠左，居中對齊左右對稱
        words = ["外", "の", "世界"]
        tokens, pos = [], 0
        for w in words:
            tokens.append(Token(w, w, "", pos, pos + len(w)))
            pos += len(w)
        sent = "".join(words)
        overlay.show_record(SentenceRecord(record_id=1, raw_text=sent, source="mpv",
                tokenized=TokenizedSentence(sent, tokens, 0, False, sent)))
        self.app.processEvents()

        sw = overlay.sentence_widget
        first_left_x = sw.layout().itemAt(0).geometry().left()
        self.assertLess(first_left_x, 20)
        self.assertTrue(bool(overlay.inline_trans_label.alignment() & Qt.AlignmentFlag.AlignLeft))

        saved = []
        overlay.state_saved.connect(saved.append)
        overlay.act_center_text.trigger()
        self.app.processEvents()

        self.assertEqual(overlay._text_align, "center")
        self.assertTrue(overlay.act_center_text.isChecked())
        self.assertEqual(saved[-1]["text_align"], "center")
        self.assertTrue(bool(overlay.inline_trans_label.alignment() & Qt.AlignmentFlag.AlignHCenter))

        first_geom = sw.layout().itemAt(0).geometry()
        last_geom = sw.layout().itemAt(2).geometry()
        left_gap = first_geom.left()
        right_gap = sw.width() - (last_geom.right() + 1)
        self.assertGreater(left_gap, 120)
        self.assertAlmostEqual(left_gap, right_gap, delta=4)

        # 多行換行測試：每一行都獨立水平居中
        long_words = ["明日以降、", "学園で", "顔を", "合わせても、", "まるで", "今日の", "ことが", "なかったかのように", "振舞います。"]
        ltoks, pos = [], 0
        for w in long_words:
            ltoks.append(Token(w, w, "", pos, pos + len(w)))
            pos += len(w)
        lsent = "".join(long_words)
        overlay.show_record(SentenceRecord(record_id=2, raw_text=lsent, source="mpv",
                tokenized=TokenizedSentence(lsent, ltoks, 0, False, lsent)))
        self.app.processEvents()

        lines_by_y: dict[int, list[QRect]] = {}
        for i in range(sw.layout().count()):
            g = sw.layout().itemAt(i).geometry()
            lines_by_y.setdefault(g.top(), []).append(g)
        self.assertGreaterEqual(len(lines_by_y), 2)
        for rects in lines_by_y.values():
            lg = min(r.left() for r in rects)
            rg = sw.width() - (max(r.right() for r in rects) + 1)
            self.assertAlmostEqual(lg, rg, delta=4)

        # 切回左對齊與 SettingsDialog 持久化同步
        overlay.act_center_text.trigger()
        self.app.processEvents()
        self.assertEqual(overlay._text_align, "left")
        self.assertLess(sw.layout().itemAt(0).geometry().left(), 20)

        overlay.restore_saved_state(40, 60, 620, 140, False, True, True, text_align="center")
        self.assertEqual(overlay._text_align, "center")
        self.assertTrue(overlay.act_center_text.isChecked())

        with tempfile.TemporaryDirectory() as tmp:
            cfg = AppConfig(overlay_text_align="center")
            with patch.object(AppConfig, "config_path", return_value=Path(tmp) / "config.json"):
                settings = SettingsDialog(cfg, QThreadPool.globalInstance())
                self.addCleanup(settings.close)
                self.assertEqual(settings.overlay_align_combo.currentData(), "center")
                settings.overlay_align_combo.setCurrentIndex(0)
                settings._save_and_accept()
                self.assertEqual(cfg.overlay_text_align, "left")
