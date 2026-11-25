"""按需網頁查證網址組裝器（僅於使用者主動點擊詳情入口時呼叫，絕不自動聯網）。"""

from __future__ import annotations

from urllib.parse import quote

WEB_LOOKUP_SITES: tuple[tuple[str, str, str], ...] = (
    ("kotobank", "コトバンク（大辞泉・日日）", "https://kotobank.jp/gs/?q={q}"),
    ("jisho", "Jisho（日英對照）", "https://jisho.org/search/{q}"),
)

_SITE_TEMPLATES: dict[str, str] = {site_id: tpl for site_id, _label, tpl in WEB_LOOKUP_SITES}


def sanitize_web_query(query: str) -> str:
    """只保留選取的日文原形或語塊，避免誤傳多行字幕或前後標點。"""
    clean = " ".join((query or "").strip().splitlines()).strip()
    clean = clean.strip("「」『』（）()【】[]。、！？!? ")
    return clean[:40]


def build_web_lookup_url(site: str, query: str) -> str:
    """回傳指定辭典網站的 URL 編碼搜尋網址；若詞頭為空或站點未知回傳空字串。"""
    tpl = _SITE_TEMPLATES.get((site or "").strip().lower())
    clean = sanitize_web_query(query)
    if not tpl or not clean:
        return ""
    return tpl.format(q=quote(clean, safe=""))
