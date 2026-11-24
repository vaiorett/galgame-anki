"""跨平台覆蓋層置頂與 Wayland KWin 原生置頂／懸停查詞定位適配器。

在 Linux KDE Plasma Wayland 下，Qt 的 `WindowStaysOnTopHint` 與 `move(x, y)` 會被 Wayland
xdg-shell 協議忽略，導致點擊遊戲或 mpv 後覆蓋層立即掉到視窗後方。
本模組透過 KDE KWin 6 DBus Scripting（零修改 `~/.config/kwinrulesrc`、關閉即自動卸載）實現：
1. 0ms 即時將「GalAnki 覆蓋層」與「GalAnki 查詞浮窗」設為 KWin 原生 `keepAbove = true`（真實置頂）。
2. 自動避開工作列與視窗切換器（`skipTaskbar` / `skipPager`），並支援跨虛擬桌面跟隨（`onAllDesktops`）。
3. 懸停查詞浮窗彈出時自動對齊游標 X 座標並貼齊於覆蓋層正上方，絕不跑偏或擋住台詞。
4. 支援即時開關「📌 置頂」與程式關閉時自動卸載腳本。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

OVERLAY_WINDOW_TITLE = "GalAnki 覆蓋層"
POPUP_WINDOW_TITLE = "GalAnki 查詞浮窗"
KWIN_PLUGIN_NAME = "galanki_overlay_pin"
KWIN_UNPIN_PLUGIN_NAME = "galanki_overlay_unpin"


def _find_qdbus() -> str | None:
    return shutil.which("qdbus6") or shutil.which("qdbus")


def is_kde_wayland_kwin_available() -> bool:
    """偵測目前是否執行於支援 KWin DBus Scripting 的 KDE Plasma Wayland 環境。"""
    if os.name == "nt":
        return False
    if os.environ.get("QT_QPA_PLATFORM", "").lower() in ("offscreen", "minimal"):
        return False
    if os.environ.get("XDG_SESSION_TYPE", "").lower() != "wayland":
        return False
    return _find_qdbus() is not None


def build_kwin_pin_script(
    overlay_title: str = OVERLAY_WINDOW_TITLE,
    popup_title: str = POPUP_WINDOW_TITLE,
    pinned: bool = True,
    initial_geometry: tuple[int, int, int, int] | None = None,
) -> str:
    """產生 KWin 6 JS 腳本：控制覆蓋層與查詞浮窗置頂、記憶拖曳位置與懸停彈窗精準對齊。"""
    ov_js = json.dumps(overlay_title, ensure_ascii=False)
    pop_js = json.dumps(popup_title, ensure_ascii=False)
    pin_js = "true" if pinned else "false"
    if initial_geometry is not None and initial_geometry[0] >= 0 and initial_geometry[1] >= 0:
        ix, iy, iw, ih = initial_geometry
        init_geom_js = f"{{ x: {int(ix)}, y: {int(iy)}, width: {int(iw)}, height: {int(ih)} }}"
    else:
        init_geom_js = "null"

    return f"""
const OVERLAY_TITLE = {ov_js};
const POPUP_TITLE = {pop_js};
const PINNED = {pin_js};
let savedOverlayGeom = {init_geom_js};
let placingPopup = false;

function findOverlay() {{
    const list = workspace.windowList ? workspace.windowList() : [];
    for (const w of list) {{
        if (w && w.caption === OVERLAY_TITLE) return w;
    }}
    return null;
}}

function clampRect(w, x, y, width, height) {{
    let area = null;
    try {{
        area = workspace.clientArea(KWin.MaximizeArea, w);
    }} catch (_e) {{
        area = null;
    }}
    if (!area || area.width <= 0 || area.height <= 0) {{
        return {{ x: Math.max(8, Math.round(x)), y: Math.max(8, Math.round(y)), width, height }};
    }}
    const nx = Math.max(area.x + 6, Math.min(Math.round(x), area.x + area.width - width - 6));
    const ny = Math.max(area.y + 6, Math.min(Math.round(y), area.y + area.height - height - 6));
    return {{ x: nx, y: ny, width, height }};
}}

function positionPopupAboveOverlay(pop) {{
    if (!pop || placingPopup) return;
    const ov = findOverlay();
    if (!ov) return;
    const og = ov.frameGeometry;
    const pg = pop.frameGeometry;
    if (!og || !pg || pg.width <= 0 || pg.height <= 0) return;

    const cx = (workspace.cursorPos && workspace.cursorPos.x !== undefined)
        ? workspace.cursorPos.x
        : (og.x + 24);
    let targetX = cx - Math.round(pg.width * 0.35);
    let targetY = og.y - pg.height - 8;
    if (targetY < 10) {{
        targetY = og.y + og.height + 8;
    }}
    const rect = clampRect(pop, targetX, targetY, pg.width, pg.height);
    if (Math.abs(pg.x - rect.x) > 3 || Math.abs(pg.y - rect.y) > 3) {{
        placingPopup = true;
        try {{
            pop.frameGeometry = rect;
        }} finally {{
            placingPopup = false;
        }}
    }}
}}

function applyToWindow(w) {{
    if (!w) return;
    if (w.caption === OVERLAY_TITLE) {{
        w.keepAbove = PINNED;
        w.onAllDesktops = PINNED;
        w.skipTaskbar = true;
        w.skipPager = true;
        if (savedOverlayGeom && w.frameGeometry) {{
            const g = w.frameGeometry;
            const rect = clampRect(w, savedOverlayGeom.x, savedOverlayGeom.y, g.width, g.height);
            if (Math.abs(g.x - rect.x) > 4 || Math.abs(g.y - rect.y) > 4) {{
                w.frameGeometry = rect;
            }}
        }}
    }} else if (w.caption === POPUP_TITLE) {{
        w.keepAbove = PINNED;
        w.onAllDesktops = PINNED;
        w.skipTaskbar = true;
        w.skipPager = true;
        positionPopupAboveOverlay(w);
    }}
}}

function trackWindow(w) {{
    if (!w) return;
    applyToWindow(w);
    if (w.captionChanged) {{
        w.captionChanged.connect(() => applyToWindow(w));
    }}
    if (w.frameGeometryChanged) {{
        w.frameGeometryChanged.connect(() => {{
            if (w.caption === OVERLAY_TITLE && w.frameGeometry && w.frameGeometry.width >= 400) {{
                savedOverlayGeom = {{
                    x: w.frameGeometry.x,
                    y: w.frameGeometry.y,
                    width: w.frameGeometry.width,
                    height: w.frameGeometry.height,
                }};
            }} else if (w.caption === POPUP_TITLE) {{
                positionPopupAboveOverlay(w);
            }}
        }});
    }}
}}

const existing = workspace.windowList ? workspace.windowList() : [];
for (const w of existing) {{
    trackWindow(w);
}}
if (workspace.windowAdded) {{
    workspace.windowAdded.connect(trackWindow);
}}
""".strip() + "\n"


class KWinOverlayPinner:
    """背景非同步管理 KDE Plasma Wayland KWin 6 置頂腳本的生命週期。"""

    def __init__(
        self,
        overlay_title: str = OVERLAY_WINDOW_TITLE,
        popup_title: str = POPUP_WINDOW_TITLE,
    ) -> None:
        self.overlay_title = overlay_title
        self.popup_title = popup_title
        self._lock = threading.Lock()
        self._script_path: Path | None = None
        self._pinned: bool = True
        self._active: bool = False

    @property
    def is_supported(self) -> bool:
        return is_kde_wayland_kwin_available()

    def apply_pin(
        self,
        pinned: bool,
        initial_geometry: tuple[int, int, int, int] | None = None,
        force_reload: bool = True,
    ) -> None:
        """非同步將 KWin 置頂狀態套用至覆蓋層與查詞浮窗（永不阻塞 UI 執行緒）。"""
        self._pinned = bool(pinned)
        if not self.is_supported:
            return
        if not force_reload and self._active:
            return
        threading.Thread(
            target=self._apply_sync,
            args=(bool(pinned), initial_geometry),
            daemon=True,
        ).start()

    def ensure_active(self, initial_geometry: tuple[int, int, int, int] | None = None) -> None:
        """若尚未載入 KWin 置頂腳本，背景自動啟用（保留執行期間使用者拖曳過的位置）。"""
        self.apply_pin(self._pinned, initial_geometry=initial_geometry, force_reload=False)

    def _apply_sync(
        self,
        pinned: bool,
        initial_geometry: tuple[int, int, int, int] | None,
    ) -> None:
        qdbus = _find_qdbus()
        if not qdbus:
            return
        with self._lock:
            self._unload_locked(qdbus, KWIN_PLUGIN_NAME)
            self._unload_locked(qdbus, KWIN_UNPIN_PLUGIN_NAME)

            js = build_kwin_pin_script(
                overlay_title=self.overlay_title,
                popup_title=self.popup_title,
                pinned=pinned,
                initial_geometry=initial_geometry,
            )
            tmp_dir = Path(tempfile.gettempdir())
            script_file = tmp_dir / f"galanki_kwin_pin_{os.getpid()}.js"
            try:
                script_file.write_text(js, encoding="utf-8")
                self._script_path = script_file
                res = subprocess.run(
                    [qdbus, "org.kde.KWin", "/Scripting", "loadScript", str(script_file), KWIN_PLUGIN_NAME],
                    capture_output=True,
                    text=True,
                    timeout=3,
                    check=False,
                )
                sid = res.stdout.strip()
                if res.returncode == 0 and sid.isdigit():
                    subprocess.run(
                        [qdbus, "org.kde.KWin", f"/Scripting/Script{sid}", "run"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=3,
                        check=False,
                    )
                    self._active = True
            except (OSError, subprocess.SubprocessError):
                pass

    def _unload_locked(self, qdbus: str, plugin_name: str) -> None:
        try:
            subprocess.run(
                [qdbus, "org.kde.KWin", "/Scripting", "unloadScript", plugin_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass

    def shutdown(self) -> None:
        """退出應用程式時卸載 KWin 腳本並清理暫存檔。"""
        qdbus = _find_qdbus()
        with self._lock:
            if qdbus and self._active:
                self._unload_locked(qdbus, KWIN_PLUGIN_NAME)
                self._unload_locked(qdbus, KWIN_UNPIN_PLUGIN_NAME)
                self._active = False
            if self._script_path is not None:
                try:
                    self._script_path.unlink(missing_ok=True)
                except OSError:
                    pass
                self._script_path = None
