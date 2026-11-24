"""零額外依賴本地 OCR 兜底引擎（Linux KDE Spectacle + Pillow + Tesseract jpn/jpn_vert）。

當特定遊戲因強加密、特殊引擎或跨容器無法直接記憶體 Hook 時，
按下「📷 OCR 框選抓詞」即可在 1 秒內辨識對話框日文並送入 GalAnki 分詞與一鍵製卡。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

_JP_CHAR_CLASS = r"\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff々ー。、！？「」『』（）・…"


def clean_ocr_japanese_text(raw: str) -> str:
    """移除 Tesseract 常見的日文字元間多餘空格與異常換行，保留乾淨台詞。"""
    lines = [ln.strip() for ln in raw.replace("\r\n", "\n").split("\n") if ln.strip()]
    joined = "".join(lines)
    # 去除日文與日文（含標點）之間被 OCR 誤插的空白
    pattern = re.compile(rf"(?<=[{_JP_CHAR_CLASS}])\s+(?=[{_JP_CHAR_CLASS}])")
    cleaned = pattern.sub("", joined)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned


def is_ocr_available() -> bool:
    return shutil.which("tesseract") is not None


def list_tesseract_japanese_langs() -> list[str]:
    tess = shutil.which("tesseract")
    if not tess:
        return []
    try:
        res = subprocess.run([tess, "--list-langs"], capture_output=True, text=True, timeout=5, check=False)
        langs = [ln.strip() for ln in res.stdout.splitlines() if ln.strip() in ("jpn", "jpn_vert")]
        return langs
    except (OSError, subprocess.SubprocessError):
        return []


def preprocess_image_for_ocr(input_path: Path, output_path: Path) -> Path:
    """放大 2 倍、灰階化與自動對比增強，顯著提升遊戲字體與描邊字幕的辨識率。"""
    try:
        from PIL import Image, ImageOps

        img = Image.open(input_path)
        gray = ImageOps.grayscale(img)
        w, h = gray.size
        if w < 1400:
            gray = gray.resize((w * 2, h * 2), Image.Resampling.LANCZOS)
        enhanced = ImageOps.autocontrast(gray, cutoff=2)
        enhanced.save(output_path)
        return output_path
    except Exception:
        return input_path


def run_tesseract_on_image(image_path: Path, vertical: bool = False) -> str:
    """對圖片執行本機 Tesseract 日語 OCR。"""
    tess = shutil.which("tesseract")
    if not tess:
        raise RuntimeError("系統未安裝 tesseract，無法執行本地 OCR。")

    lang = "jpn_vert" if vertical else "jpn"
    psm = "5" if vertical else "6"

    with tempfile.TemporaryDirectory(prefix="galanki_ocr_") as tmpdir:
        prepped = preprocess_image_for_ocr(image_path, Path(tmpdir) / "prepped.png")
        res = subprocess.run(
            [tess, str(prepped), "stdout", "-l", lang, "--psm", psm],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if res.returncode != 0:
            err = (res.stderr or "").strip()
            raise RuntimeError(f"Tesseract OCR 失敗（語言 {lang}）：{err}")
        return clean_ocr_japanese_text(res.stdout)


def capture_region_and_ocr(vertical: bool = False) -> str:
    """透過 KDE Spectacle 框選遊戲對話框區域並即刻辨識日文台詞。"""
    spectacle = shutil.which("spectacle")
    if not spectacle:
        raise RuntimeError("找不到 KDE Spectacle，無法啟動互動框選截圖。")

    with tempfile.TemporaryDirectory(prefix="galanki_region_") as tmpdir:
        shot_path = Path(tmpdir) / "region.png"
        # -b: 背景執行不開主視窗, -r: 互動框選區域, -n: 不顯示桌面通知
        res = subprocess.run(
            [spectacle, "-b", "-r", "-n", "-o", str(shot_path)],
            capture_output=True,
            timeout=120,
            check=False,
        )
        if res.returncode != 0 or not shot_path.exists():
            return ""
        return run_tesseract_on_image(shot_path, vertical=vertical)
