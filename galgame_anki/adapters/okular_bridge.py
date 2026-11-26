"""外置 KDE Okular 閱讀伴讀橋接（純剪貼簿文字）：DBus 書名頁碼查詢與 PDF/EPUB 折行清理。"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

_CJK_OR_JP_CHAR_RE = re.compile(
    r"[\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff\uff00-\uffef]"
)
_OKULAR_SERVICE_RE = re.compile(r"^org\.kde\.okular(?:-(\d+))?$")


@dataclass(frozen=True)
class OkularContext:
    """目前執行中 Okular 的文件書名與頁碼資訊。"""

    title: str
    page: int = 0
    total_pages: int = 0
    document_url: str = ""

    @property
    def label(self) -> str:
        if self.title and self.page > 0:
            return f"{self.title} · p.{self.page}"
        return self.title


def parse_okular_document_title(raw_doc: str) -> str:
    """將 Okular DBus 回傳的 file:// URL 或本機路徑轉為乾淨書名。"""
    s = (raw_doc or "").strip()
    if not s:
        return ""
    if s.startswith("file://"):
        parsed = urlparse(s)
        s = unquote(parsed.path or "")
    else:
        s = unquote(s)
    return Path(s).stem.strip() if s else ""


def _pick_qdbus_executable(override: str | None = None) -> str | None:
    if override:
        return override
    return shutil.which("qdbus-qt6") or shutil.which("qdbus")


def query_okular_context(
    qdbus_exe: str | None = None,
    timeout: float = 0.6,
) -> OkularContext | None:
    """透過 KDE DBus 靜默查詢執行中 Okular 的書名與頁碼（未開啟或不可用時安全回傳 None）。"""
    exe = _pick_qdbus_executable(qdbus_exe)
    if not exe:
        return None
    try:
        res = subprocess.run(
            [exe],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None

    services: list[tuple[int, str]] = []
    for line in res.stdout.splitlines():
        svc = line.strip()
        m = _OKULAR_SERVICE_RE.match(svc)
        if m:
            pid = int(m.group(1)) if m.group(1) else 0
            services.append((pid, svc))
    if not services:
        return None

    # 優先取 PID 較大（最近開啟）且有載入文件的 Okular 實例
    services.sort(key=lambda item: item[0], reverse=True)
    for _pid, svc in services:
        try:
            doc_res = subprocess.run(
                [exe, svc, "/okular", "org.kde.okular.currentDocument"],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        raw_doc = (doc_res.stdout or "").strip()
        if doc_res.returncode != 0 or not raw_doc:
            continue

        title = parse_okular_document_title(raw_doc)
        if not title:
            continue

        page = 0
        total_pages = 0
        try:
            p_res = subprocess.run(
                [exe, svc, "/okular", "org.kde.okular.currentPage"],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            if p_res.returncode == 0 and (p_res.stdout or "").strip().isdigit():
                page = int(p_res.stdout.strip())
        except (OSError, subprocess.SubprocessError, ValueError):
            pass

        try:
            t_res = subprocess.run(
                [exe, svc, "/okular", "org.kde.okular.pages"],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            if t_res.returncode == 0 and (t_res.stdout or "").strip().isdigit():
                total_pages = int(t_res.stdout.strip())
        except (OSError, subprocess.SubprocessError, ValueError):
            pass

        return OkularContext(
            title=title,
            page=page,
            total_pages=total_pages,
            document_url=raw_doc,
        )
    return None


def clean_okular_clipboard_text(raw: str) -> str:
    """修復從 Okular PDF / EPUB 複製多行日文時的硬換行斷句，同時保留英文單詞空格。"""
    if not raw:
        return ""
    normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
    if "\n" not in normalized:
        return normalized.strip()

    lines = [ln.strip() for ln in normalized.split("\n") if ln.strip()]
    if not lines:
        return ""

    out = [lines[0]]
    for cur in lines[1:]:
        prev = out[-1]
        if _CJK_OR_JP_CHAR_RE.search(prev[-1]) or _CJK_OR_JP_CHAR_RE.search(cur[0]):
            out.append(cur)
        else:
            out.append(" " + cur)
    return "".join(out).strip()
