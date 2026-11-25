"""原生 Qt QPainter 高低音調曲線視覺化元件（HiDPI 抗鋸齒，免依賴外掛即可在查詞與製卡即時預覽）。"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from galgame_anki.core.pitch_accent import (
    PITCH_TYPE_META,
    PitchAccentInfo,
    analyze_pitch_accent,
    parse_pitch_numbers,
)


class PitchGraphWidget(QWidget):
    """以原生 QPainter 繪製 AJATT 風格高低音調折線（平板藍／頭高紅／中高橙／尾高綠＋助詞空心尾拍）。"""

    def __init__(
        self,
        compact: bool = False,
        dark_mode: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._compact = compact
        self._dark_mode = dark_mode
        self._items: list[PitchAccentInfo] = []
        self.setStyleSheet("background: transparent;")
        self.hide()

    def set_dark_mode(self, dark_mode: bool) -> None:
        if self._dark_mode != dark_mode:
            self._dark_mode = dark_mode
            self.update()

    def set_pitch(
        self,
        reading: str,
        pitches: str | list[str] | tuple[str, ...] | None,
        dark_mode: bool | None = None,
        max_patterns: int = 2,
    ) -> None:
        if dark_mode is not None:
            self._dark_mode = dark_mode
        nums = parse_pitch_numbers(pitches)[:max_patterns]
        items: list[PitchAccentInfo] = []
        for ds in nums:
            info = analyze_pitch_accent(reading, ds)
            if info is not None:
                items.append(info)
        self._items = items
        if not self._items:
            self.hide()
            return
        self.updateGeometry()
        self.show()
        self.update()

    def _item_width(self, info: PitchAccentInfo) -> int:
        step_x = 18 if self._compact else 23
        pad_x = 10 if self._compact else 12
        graph_w = pad_x * 2 + step_x * len(info.moras)
        badge_w = 50 if self._compact else 58
        return graph_w + badge_w + 6

    def sizeHint(self) -> QSize:  # noqa: N802
        if not self._items:
            return QSize(0, 0)
        h = 40 if self._compact else 48
        w = sum(self._item_width(it) for it in self._items) + 8 * max(0, len(self._items) - 1)
        return QSize(min(w, 460), h)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def paintEvent(self, _event) -> None:  # noqa: N802
        if not self._items:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        step_x = 18 if self._compact else 23
        pad_x = 10 if self._compact else 12
        y_high = 8.0 if self._compact else 10.0
        y_low = 21.0 if self._compact else 26.0
        y_text = 36.0 if self._compact else 44.0
        r_mora = 3.1 if self._compact else 3.9
        r_part = 2.8 if self._compact else 3.4
        kana_pt = 8.5 if self._compact else 10.0
        badge_pt = 8.0 if self._compact else 8.8
        text_color = QColor("#efe6f6" if self._dark_mode else "#2d2336")

        x_cursor = 0
        for info in self._items:
            accent_color = QColor(info.color(self._dark_mode))
            n_moras = len(info.moras)
            pts: list[QPointF] = [
                QPointF(
                    x_cursor + pad_x + idx * step_x,
                    y_high if is_high else y_low,
                )
                for idx, is_high in enumerate(info.levels)
            ]

            # 1. 折線
            if n_moras >= 2:
                pen = QPen(accent_color, 1.9 if self._compact else 2.2)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                path = QPainterPath(pts[0])
                for pt in pts[1:n_moras]:
                    path.lineTo(pt)
                painter.drawPath(path)

            # 2. 助詞虛線
            dash_color = QColor(accent_color)
            dash_color.setAlpha(185)
            dash_pen = QPen(dash_color, 1.5 if self._compact else 1.7, Qt.PenStyle.DashLine)
            painter.setPen(dash_pen)
            painter.drawLine(pts[n_moras - 1], pts[n_moras])

            # 3. 每一拍圓點＋假名
            for idx, mora in enumerate(info.moras):
                pt = pts[idx]
                if info.downstep > 0 and (idx + 1) == info.downstep:
                    ring_c = QColor(accent_color)
                    ring_c.setAlpha(135)
                    painter.setPen(QPen(ring_c, 1.2))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawEllipse(pt, r_mora + 2.3, r_mora + 2.3)

                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(accent_color)
                painter.drawEllipse(pt, r_mora, r_mora)

                kf = QFont("Noto Sans CJK JP", int(kana_pt))
                kf.setPointSizeF(kana_pt - (1.2 if len(mora) > 1 else 0.0))
                painter.setFont(kf)
                painter.setPen(text_color)
                rect = QRectF(pt.x() - step_x * 0.65, y_text - 13, step_x * 1.3, 16)
                painter.drawText(rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, mora)

            # 4. 助詞空心圓
            painter.setPen(QPen(accent_color, 1.6))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(pts[n_moras], r_part, r_part)

            # 5. 四型彩色徽章
            badge_x = x_cursor + pad_x * 2 + step_x * n_moras + 2
            badge_w = 48 if self._compact else 56
            badge_h = 19 if self._compact else 22
            badge_y = (self.height() - badge_h) / 2.0
            badge_rect = QRectF(badge_x, badge_y, badge_w, badge_h)

            bg_c = QColor(accent_color)
            bg_c.setAlpha(38 if self._dark_mode else 26)
            painter.setBrush(bg_c)
            painter.setPen(QPen(accent_color, 1.0))
            painter.drawRoundedRect(badge_rect, 6.0, 6.0)

            bf = QFont("Noto Sans CJK JP")
            bf.setPointSizeF(badge_pt)
            bf.setBold(True)
            painter.setFont(bf)
            painter.setPen(accent_color)
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, info.badge_text())

            x_cursor += self._item_width(info) + 8
        painter.end()
