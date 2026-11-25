"""日語東京式高低音調（Pitch Accent）モーラ拆解、四型分類與 Inline SVG 視覺曲線產生器。

借鑑 Ajatt-Tools/Japanese 的高低折線與四型語義著色設計：
- 平板型 (heiban, [0])：藍色 — 首拍低、後續全高，接續助詞仍保持高音
- 頭高型 (atamadaka, [1])：紅色 — 首拍高、第二拍立刻下降
- 中高型 (nakadaka, [2..N-1])：橙色 — 首拍低、中間升起並於詞中下降
- 尾高型 (odaka, [N])：綠色 — 首拍低、詞尾仍高，但接續助詞（空心圓）處下降
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

# 與前一假名合成單一モーラ（拍）的小寫拗音假名（注意：促音 っ/ッ 獨立佔 1 拍，不併入前字！）
_COMBINING_SMALL_KANA = frozenset("ゃゅょぁぃぅぇぉゎャュョァィゥェォヮ")
_KANA_RE = re.compile(r"[ぁ-んァ-ヶー]")
_PITCH_NUM_RE = re.compile(r"\d+")

PITCH_TYPE_META: dict[str, dict[str, str]] = {
    "heiban": {
        "label_zh": "平板",
        "color_light": "#2563eb",
        "color_dark": "#60a5fa",
        "bg_light": "rgba(37, 99, 235, 0.10)",
        "bg_dark": "rgba(96, 165, 250, 0.16)",
    },
    "atamadaka": {
        "label_zh": "頭高",
        "color_light": "#dc2626",
        "color_dark": "#f87171",
        "bg_light": "rgba(220, 38, 38, 0.10)",
        "bg_dark": "rgba(248, 113, 113, 0.16)",
    },
    "nakadaka": {
        "label_zh": "中高",
        "color_light": "#d97706",
        "color_dark": "#fbbf24",
        "bg_light": "rgba(217, 119, 6, 0.11)",
        "bg_dark": "rgba(251, 191, 36, 0.16)",
    },
    "odaka": {
        "label_zh": "尾高",
        "color_light": "#059669",
        "color_dark": "#34d399",
        "bg_light": "rgba(5, 150, 105, 0.10)",
        "bg_dark": "rgba(52, 211, 153, 0.16)",
    },
}


@dataclass(frozen=True)
class PitchAccentInfo:
    reading: str
    moras: tuple[str, ...]
    downstep: int
    pitch_type: str      # heiban | atamadaka | nakadaka | odaka
    label_zh: str        # 平板 | 頭高 | 中高 | 尾高
    color_light: str
    color_dark: str
    levels: tuple[bool, ...]  # len == len(moras) + 1（最後一位代表後續助詞高低）

    def color(self, dark_mode: bool = False) -> str:
        return self.color_dark if dark_mode else self.color_light

    def badge_text(self) -> str:
        return f"{self.label_zh} [{self.downstep}]"


def split_kana_to_moras(reading: str) -> list[str]:
    """將平假名／片假名讀音精準拆分為モーラ（拍），正確合併拗音並保持促音獨立佔拍。"""
    cleaned = "".join(_KANA_RE.findall(reading or ""))
    if not cleaned:
        return []
    moras: list[str] = []
    for ch in cleaned:
        if ch in _COMBINING_SMALL_KANA and moras:
            moras[-1] = moras[-1] + ch
        else:
            moras.append(ch)
    return moras


def parse_pitch_numbers(pitches: str | list[str] | tuple[str, ...] | None) -> list[int]:
    """解析音調核字串（如 '0/2'、'[1]'、['0', '3']）為去重後的非負整數列表。"""
    if pitches is None:
        return []
    raw = "/".join(str(x) for x in pitches) if isinstance(pitches, (list, tuple)) else str(pitches)
    out: list[int] = []
    for m in _PITCH_NUM_RE.findall(raw):
        val = int(m)
        if val not in out:
            out.append(val)
    return out


def classify_pitch_type(mora_count: int, downstep: int) -> str:
    """依拍數與下降核位置分類東京式四型音調。"""
    if downstep <= 0:
        return "heiban"
    if downstep == 1:
        return "atamadaka"
    if mora_count > 0 and downstep >= mora_count:
        return "odaka"
    return "nakadaka"


def compute_pitch_levels(mora_count: int, downstep: int) -> list[bool]:
    """計算每一拍及尾端接續助詞（共 mora_count + 1 點）的高(True)／低(False)音調狀態。"""
    if mora_count <= 0:
        return []
    total = mora_count + 1
    if downstep <= 0:
        # 平板型：L H H ... H (助詞 H)
        return [False] + [True] * (total - 1)
    if downstep == 1:
        # 頭高型：H L L ... L (助詞 L)
        return [True] + [False] * (total - 1)
    # 中高型／尾高型：首拍 L，第 2..downstep 拍 H，其後全 L
    clamped = min(downstep, mora_count)
    return [False] + [True] * (clamped - 1) + [False] * (total - clamped)


def analyze_pitch_accent(reading: str, downstep: int) -> PitchAccentInfo | None:
    moras = split_kana_to_moras(reading)
    if not moras:
        return None
    n = len(moras)
    ds = max(0, min(int(downstep), n))
    ptype = classify_pitch_type(n, ds)
    meta = PITCH_TYPE_META[ptype]
    levels = compute_pitch_levels(n, ds)
    return PitchAccentInfo(
        reading="".join(moras),
        moras=tuple(moras),
        downstep=ds,
        pitch_type=ptype,
        label_zh=meta["label_zh"],
        color_light=meta["color_light"],
        color_dark=meta["color_dark"],
        levels=tuple(levels),
    )


def render_pitch_svg(
    reading: str,
    downstep: int,
    dark_mode: bool = False,
    compact: bool = False,
    use_css_colors: bool = False,
) -> str:
    """輸出可獨立嵌入 HTML／Anki 卡片的視覺化高低音調曲線 Inline SVG。"""
    info = analyze_pitch_accent(reading, downstep)
    if info is None:
        return ""

    step_x = 22 if compact else 28
    pad_x = 12 if compact else 16
    y_high = 9 if compact else 11
    y_low = 25 if compact else 31
    y_text = 41 if compact else 51
    height = 46 if compact else 58
    r_mora = 3.6 if compact else 4.5
    r_part = 3.2 if compact else 4.0
    font_size = 11.5 if compact else 14.0
    stroke_w = 2.0 if compact else 2.4

    n_moras = len(info.moras)
    total_pts = n_moras + 1
    width = pad_x * 2 + step_x * (total_pts - 1)

    pts: list[tuple[float, float]] = []
    for idx, is_high in enumerate(info.levels):
        cx = pad_x + idx * step_x
        cy = float(y_high if is_high else y_low)
        pts.append((cx, cy))

    color = info.color(dark_mode)
    text_color = "currentColor" if use_css_colors else ("#f0e8f5" if dark_mode else "#2d2336")
    cls_attr = f' class="pitch-svg pt-{info.pitch_type}"' if use_css_colors else ' class="pitch-svg"'

    # 1. 單詞主體折線
    word_pts_str = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts[:n_moras])
    # 2. 連接至尾端接續助詞的虛線
    last_x, last_y = pts[n_moras - 1]
    part_x, part_y = pts[n_moras]

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg"{cls_attr} '
        f'width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="{html.escape(info.reading)} {html.escape(info.badge_text())}">'
    ]

    if n_moras >= 2:
        parts.append(
            f'<polyline class="pt-line" fill="none" stroke="{color}" stroke-width="{stroke_w}" '
            f'stroke-linecap="round" stroke-linejoin="round" points="{word_pts_str}" />'
        )

    parts.append(
        f'<line class="pt-dash" x1="{last_x:.1f}" y1="{last_y:.1f}" x2="{part_x:.1f}" y2="{part_y:.1f}" '
        f'stroke="{color}" stroke-width="{stroke_w - 0.4:.1f}" stroke-dasharray="3,2.5" opacity="0.72" />'
    )

    # 3. 每一拍的實心圓點與假名文字（下降核所在拍額外繪製外環強調）
    for idx, mora in enumerate(info.moras):
        cx, cy = pts[idx]
        is_drop = info.downstep > 0 and (idx + 1) == info.downstep
        if is_drop:
            parts.append(
                f'<circle class="pt-drop-ring" cx="{cx:.1f}" cy="{cy:.1f}" r="{r_mora + 2.6:.1f}" '
                f'fill="none" stroke="{color}" stroke-width="1.3" opacity="0.55" />'
            )
        parts.append(
            f'<circle class="pt-dot" cx="{cx:.1f}" cy="{cy:.1f}" r="{r_mora:.1f}" fill="{color}" />'
        )
        mora_font = font_size - (1.8 if len(mora) > 1 else 0.0)
        parts.append(
            f'<text class="pt-kana" x="{cx:.1f}" y="{y_text}" text-anchor="middle" '
            f'fill="{text_color}" font-size="{mora_font:.1f}" '
            f'font-family="Noto Sans CJK JP, Hiragino Sans, Yu Gothic UI, sans-serif">'
            f"{html.escape(mora)}</text>"
        )

    # 4. 尾端接續助詞空心圓（區分平板 vs 尾高！）
    parts.append(
        f'<circle class="pt-particle" cx="{part_x:.1f}" cy="{part_y:.1f}" r="{r_part:.1f}" '
        f'fill="none" stroke="{color}" stroke-width="1.8" />'
    )
    parts.append("</svg>")
    return "".join(parts)


def render_pitch_graphs_html(
    reading: str,
    pitches: str | list[str] | tuple[str, ...] | None,
    dark_mode: bool = False,
    compact: bool = False,
    use_css_colors: bool = False,
    max_patterns: int = 2,
) -> str:
    """為單詞的 1～2 個常見音調核產生「SVG 曲線 ＋ 四型著色標籤」HTML。"""
    nums = parse_pitch_numbers(pitches)[:max_patterns]
    if not nums or not split_kana_to_moras(reading):
        return ""
    chips: list[str] = []
    for ds in nums:
        info = analyze_pitch_accent(reading, ds)
        if info is None:
            continue
        svg = render_pitch_svg(
            reading=reading,
            downstep=ds,
            dark_mode=dark_mode,
            compact=compact,
            use_css_colors=use_css_colors,
        )
        badge_cls = f"pitch-chip-tag pt-tag-{info.pitch_type}" if use_css_colors else "pitch-chip-tag"
        if use_css_colors:
            tag_html = f'<span class="{badge_cls}">{html.escape(info.badge_text())}</span>'
        else:
            meta = PITCH_TYPE_META[info.pitch_type]
            fg = meta["color_dark"] if dark_mode else meta["color_light"]
            bg = meta["bg_dark"] if dark_mode else meta["bg_light"]
            tag_html = (
                f'<span class="{badge_cls}" style="color:{fg}; background:{bg}; '
                f'border:1px solid {fg}; border-radius:5px; padding:1px 6px; font-size:11px; font-weight:700;">'
                f"{html.escape(info.badge_text())}</span>"
            )
        chips.append(f'<span class="pitch-item pt-{info.pitch_type}">{svg}{tag_html}</span>')
    return "".join(chips)
