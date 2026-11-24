#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_SVG_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
ICON_PNG_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps"

mkdir -p "$APP_DIR" "$ICON_SVG_DIR" "$ICON_PNG_DIR"

# 1. 安裝向量圖示與 256px PNG 圖示
cp "${SCRIPT_DIR}/galgame_anki/data/galanki.svg" "${ICON_SVG_DIR}/galanki.svg"
QT_QPA_PLATFORM=offscreen python3 - "${SCRIPT_DIR}/galgame_anki/data/galanki.svg" "${ICON_PNG_DIR}/galanki.png" <<'PY' 2>/dev/null || true
import sys
from PySide6.QtCore import QSize
from PySide6.QtGui import QGuiApplication, QIcon

app = QGuiApplication([])
pm = QIcon(sys.argv[1]).pixmap(QSize(256, 256))
if not pm.isNull():
    pm.save(sys.argv[2], "PNG")
PY

# 2. 建立標準 XDG .desktop 選單快捷方式（無需開啟終端機）
cat > "${APP_DIR}/galanki.desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=GalAnki
Name[zh_TW]=GalAnki 日語伴讀
Name[zh_CN]=GalAnki 日语伴读
GenericName=Japanese Immersion & Anki Mining Tool
GenericName[zh_TW]=Galgame・動畫日語沉浸學習與 Anki 製卡
GenericName[zh_CN]=Galgame・动画日语沉浸学习与 Anki 制卡
Comment=邊玩 Galgame・看 mpv 動畫邊系統化學習日語的一鍵 Anki 工具
Exec=${SCRIPT_DIR}/run.sh
Path=${SCRIPT_DIR}
Icon=galanki
Terminal=false
StartupNotify=true
StartupWMClass=galanki
Categories=Education;Languages;
Keywords=GalAnki;galgame;japanese;anki;mpv;ocr;日語;日语;學習;学习;
EOF

chmod +x "${SCRIPT_DIR}/run.sh" "${APP_DIR}/galanki.desktop"

# 3. 刷新桌面應用程式選單與圖示快取
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APP_DIR" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -f -t "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" 2>/dev/null || true
command -v kbuildsycoca6 >/dev/null 2>&1 && kbuildsycoca6 --noincremental 2>/dev/null || true

echo "✅ 已在系統應用程式選單建立「GalAnki 日語伴讀」快捷方式：${APP_DIR}/galanki.desktop"
