"""輕量化・二次元化雙主題樣式表（🌸 櫻霧輕透 / 🌙 星夜琉璃）。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThemePalette:
    name: str
    bg_window: str
    bg_surface: str
    bg_card: str
    bg_input: str
    border: str
    border_accent: str
    text_primary: str
    text_secondary: str
    text_muted: str
    accent_pink: str
    accent_pink_hover: str
    accent_lilac: str
    accent_mint: str
    accent_gold: str
    pill_ok_bg: str
    pill_ok_fg: str
    pill_warn_bg: str
    pill_warn_fg: str
    pill_info_bg: str
    pill_info_fg: str
    overlay_bg: str
    overlay_border: str
    popup_bg: str
    token_selected_bg: str
    token_selected_fg: str
    token_1t_bg: str
    token_1t_fg: str
    token_1t_line: str
    token_hover_bg: str


SAKURA_PALETTE = ThemePalette(
    name="sakura",
    bg_window="#fdf7fa",
    bg_surface="#fffbfe",
    bg_card="#ffffff",
    bg_input="#faf2f6",
    border="#f1d5e3",
    border_accent="#f472b6",
    text_primary="#2e2236",
    text_secondary="#594766",
    text_muted="#8e7a9c",
    accent_pink="#ec4899",
    accent_pink_hover="#db2777",
    accent_lilac="#9333ea",
    accent_mint="#10b981",
    accent_gold="#d97706",
    pill_ok_bg="#e6f9f1",
    pill_ok_fg="#047857",
    pill_warn_bg="#fef3e2",
    pill_warn_fg="#b45309",
    pill_info_bg="#fce7f3",
    pill_info_fg="#be185d",
    overlay_bg="rgba(255, 250, 253, 232)",
    overlay_border="rgba(244, 114, 182, 165)",
    popup_bg="rgba(255, 252, 254, 248)",
    token_selected_bg="rgba(236, 72, 153, 0.16)",
    token_selected_fg="#be185d",
    token_1t_bg="rgba(245, 158, 11, 0.14)",
    token_1t_fg="#b45309",
    token_1t_line="#f59e0b",
    token_hover_bg="rgba(236, 72, 153, 0.10)",
)

STARLIGHT_PALETTE = ThemePalette(
    name="starlight",
    bg_window="#151322",
    bg_surface="#1b182c",
    bg_card="#221e36",
    bg_input="#171426",
    border="#352e52",
    border_accent="#f472b6",
    text_primary="#f4effb",
    text_secondary="#cbbfe0",
    text_muted="#9587b3",
    accent_pink="#f472b6",
    accent_pink_hover="#ec4899",
    accent_lilac="#a78bfa",
    accent_mint="#34d399",
    accent_gold="#fcd34d",
    pill_ok_bg="#14332a",
    pill_ok_fg="#6ee7b7",
    pill_warn_bg="#3a2614",
    pill_warn_fg="#fcd34d",
    pill_info_bg="#361c36",
    pill_info_fg="#f9a8d4",
    overlay_bg="rgba(23, 19, 38, 224)",
    overlay_border="rgba(244, 114, 182, 150)",
    popup_bg="rgba(28, 24, 46, 244)",
    token_selected_bg="rgba(244, 114, 182, 0.25)",
    token_selected_fg="#fbcfe8",
    token_1t_bg="rgba(252, 211, 77, 0.16)",
    token_1t_fg="#fde68a",
    token_1t_line="#fbbf24",
    token_hover_bg="rgba(244, 114, 182, 0.16)",
)

PALETTES: dict[str, ThemePalette] = {
    "sakura": SAKURA_PALETTE,
    "starlight": STARLIGHT_PALETTE,
}


def get_palette(theme_name: str) -> ThemePalette:
    return PALETTES.get(theme_name, SAKURA_PALETTE)


def build_stylesheet(theme_name: str = "sakura") -> str:
    p = get_palette(theme_name)
    btn_bg = "#fff5fa" if p.name == "sakura" else "#2a2542"
    btn_hover = "#fde7f2" if p.name == "sakura" else "#362f54"
    mine_text = "#ffffff" if p.name == "sakura" else "#151322"

    return f"""
QWidget {{
    background-color: {p.bg_window};
    color: {p.text_primary};
    font-family: "Noto Sans CJK TC", "Noto Sans CJK JP", "Noto Sans", sans-serif;
    font-size: 13px;
}}

QMainWindow, QDialog {{
    background-color: {p.bg_window};
}}

QFrame#TopBar {{
    background-color: {p.bg_surface};
    border-bottom: 1px solid {p.border};
}}

QFrame#CardPanel {{
    background-color: {p.bg_card};
    border: 1px solid {p.border};
    border-radius: 14px;
}}

QFrame#SentencePanel {{
    background-color: {p.bg_card};
    border: 1.5px solid {p.border_accent};
    border-radius: 16px;
}}

QPushButton {{
    background-color: {btn_bg};
    color: {p.text_primary};
    border: 1px solid {p.border};
    border-radius: 10px;
    padding: 5px 11px;
    font-weight: 500;
}}

QPushButton:hover {{
    background-color: {btn_hover};
    border-color: {p.border_accent};
}}

QPushButton:pressed {{
    background-color: {p.bg_input};
}}

QPushButton:disabled {{
    opacity: 0.5;
    color: {p.text_muted};
}}

QPushButton#PrimaryBtn {{
    background-color: {p.accent_pink};
    color: #ffffff;
    border: 1px solid {p.accent_pink};
    font-weight: 700;
    padding: 6px 14px;
}}

QPushButton#PrimaryBtn:hover {{
    background-color: {p.accent_pink_hover};
}}

QPushButton#MineBtn {{
    background-color: {p.accent_pink};
    color: #ffffff;
    border: 1px solid {p.accent_pink};
    border-radius: 12px;
    font-weight: 700;
    padding: 7px 16px;
    font-size: 13px;
}}

QPushButton#MineBtn:hover {{
    background-color: {p.accent_pink_hover};
}}

QPushButton#AccentBtn {{
    background-color: {p.accent_lilac};
    color: {mine_text};
    border: 1px solid {p.accent_lilac};
    border-radius: 10px;
    font-weight: 600;
    padding: 6px 12px;
}}

QPushButton#AccentBtn:hover {{
    opacity: 0.92;
}}

QPushButton#StageBtn {{
    background-color: transparent;
    border: 1px solid {p.border};
    border-radius: 9px;
    padding: 3px 9px;
    color: {p.text_muted};
    font-size: 12px;
}}

QPushButton#StageBtn:checked {{
    background-color: {p.accent_pink};
    border-color: {p.accent_pink};
    color: #ffffff;
    font-weight: 700;
}}

QPushButton#GhostBtn {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 4px 8px;
    color: {p.text_secondary};
}}

QPushButton#GhostBtn:hover {{
    background-color: {btn_hover};
    border-color: {p.border};
}}

QMenu {{
    background-color: {p.bg_card};
    color: {p.text_primary};
    border: 1px solid {p.border};
    border-radius: 10px;
    padding: 6px;
}}

QMenu::item {{
    padding: 6px 14px;
    border-radius: 6px;
}}

QMenu::item:selected {{
    background-color: {btn_hover};
    color: {p.accent_pink};
}}

QTabWidget::pane {{
    border: 1px solid {p.border};
    border-radius: 10px;
    background-color: {p.bg_card};
}}

QTabBar::tab {{
    background-color: {p.bg_input};
    color: {p.text_muted};
    padding: 5px 12px;
    margin-right: 4px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    border: 1px solid {p.border};
    border-bottom: none;
    font-size: 12px;
}}

QTabBar::tab:selected {{
    background-color: {p.bg_card};
    color: {p.accent_pink};
    font-weight: 700;
}}

QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QComboBox {{
    background-color: {p.bg_input};
    color: {p.text_primary};
    border: 1px solid {p.border};
    border-radius: 9px;
    padding: 5px 9px;
}}

QLineEdit:focus, QTextEdit:focus, QComboBox:focus {{
    border-color: {p.border_accent};
}}

QComboBox::drop-down {{
    border: none;
    width: 22px;
}}

QPushButton#GhostBtn::menu-indicator {{
    image: none;
}}

QTextBrowser {{
    background-color: transparent;
    border: none;
    color: {p.text_primary};
}}

QListWidget {{
    background-color: {p.bg_surface};
    border: 1px solid {p.border};
    border-radius: 12px;
    outline: none;
    padding: 4px;
}}

QListWidget::item {{
    padding: 7px 9px;
    border-radius: 8px;
    margin: 1px 0;
}}

QListWidget::item:hover {{
    background-color: {btn_hover};
}}

QListWidget::item:selected {{
    background-color: {p.pill_info_bg};
    color: {p.pill_info_fg};
    font-weight: 600;
}}

QProgressBar {{
    background-color: {p.bg_input};
    border: 1px solid {p.border};
    border-radius: 7px;
    text-align: center;
    height: 16px;
}}

QProgressBar::chunk {{
    background-color: {p.accent_pink};
    border-radius: 6px;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 7px;
}}

QScrollBar::handle:vertical {{
    background: {p.border};
    border-radius: 3px;
    min-height: 24px;
}}

QLabel#MutedLabel {{
    color: {p.text_muted};
    font-size: 12px;
    background-color: transparent;
}}

QLabel#PillOk {{
    background-color: {p.pill_ok_bg};
    color: {p.pill_ok_fg};
    border-radius: 10px;
    padding: 2px 9px;
    font-size: 12px;
    font-weight: 600;
}}

QLabel#PillWarn {{
    background-color: {p.pill_warn_bg};
    color: {p.pill_warn_fg};
    border-radius: 10px;
    padding: 2px 9px;
    font-size: 12px;
    font-weight: 600;
}}

QLabel#PillInfo {{
    background-color: {p.pill_info_bg};
    color: {p.pill_info_fg};
    border-radius: 10px;
    padding: 2px 9px;
    font-size: 12px;
    font-weight: 600;
}}
"""


APP_STYLESHEET = build_stylesheet("sakura")
