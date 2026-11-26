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
TOAST_WINDOW_TITLE = "GalAnki 收錄通知"
KWIN_PLUGIN_NAME = "galanki_overlay_pin"
KWIN_UNPIN_PLUGIN_NAME = "galanki_overlay_unpin"
KWIN_MINIMIZE_ANKI_PLUGIN = "galanki_minimize_anki_once"
KWIN_RULE_SECTION = "galanki-overlay-layer"


def ensure_kwin_overlay_layer_rule(
    enabled: bool = True,
    kwinrules_path: Path | None = None,
    reconfigure: bool = True,
) -> bool:
    """在 KDE Plasma 6 `~/.config/kwinrulesrc` 冪等寫入或移除 `layer=overlay` 規則，使覆蓋層在 mpv 全螢幕時亦穩居最上層。"""
    if kwinrules_path is None:
        if not is_kde_wayland_kwin_available():
            return False
        kwinrules_path = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "kwinrulesrc"

    existing_text = kwinrules_path.read_text(encoding="utf-8") if kwinrules_path.exists() else ""

    # 逐段保留原始文字，絕不改動使用者既有的 mpv / obsidian 等其他視窗規則（含 \s 等跳脫）
    sections: list[tuple[str, list[str]]] = []
    cur_name: str | None = None
    cur_lines: list[str] = []
    preamble: list[str] = []
    for raw_ln in existing_text.splitlines():
        s = raw_ln.strip()
        if s.startswith("[") and s.endswith("]") and len(s) >= 3:
            if cur_name is not None:
                sections.append((cur_name, cur_lines))
            cur_name = s[1:-1]
            cur_lines = []
        else:
            if cur_name is None:
                preamble.append(raw_ln)
            else:
                cur_lines.append(raw_ln)
    if cur_name is not None:
        sections.append((cur_name, cur_lines))

    # 移除舊的 GalAnki 規則段
    sections = [(name, lines) for name, lines in sections if name != KWIN_RULE_SECTION]

    # 更新 [General] Order= 與 count=
    general_idx = next((i for i, (name, _) in enumerate(sections) if name == "General"), -1)
    if general_idx >= 0:
        g_name, g_lines = sections[general_idx]
        new_g_lines: list[str] = []
        saw_order = False
        for ln in g_lines:
            if ln.startswith("Order="):
                saw_order = True
                items = [x for x in ln[len("Order="):].split(",") if x and x != KWIN_RULE_SECTION]
                if enabled:
                    items.append(KWIN_RULE_SECTION)
                new_g_lines.append("Order=" + ",".join(items))
            else:
                new_g_lines.append(ln)
        if not saw_order and enabled:
            new_g_lines.append(f"Order={KWIN_RULE_SECTION}")
        sections[general_idx] = (g_name, new_g_lines)
    elif enabled:
        sections.append(("General", [f"Order={KWIN_RULE_SECTION}"]))

    if enabled:
        rule_lines = [
            "Description=GalAnki 字幕覆蓋層與查詞浮窗置頂於全螢幕之上",
            "acceptfocus=false",
            "acceptfocusrule=2",
            "above=true",
            "aboverule=2",
            "layer=overlay",
            "layerrule=2",
            "skippager=true",
            "skippagerrule=2",
            "skiptaskbar=true",
            "skiptaskbarrule=2",
            f"title=^(?:{OVERLAY_WINDOW_TITLE}|{POPUP_WINDOW_TITLE}|{TOAST_WINDOW_TITLE})$",
            "titlematch=3",
        ]
        sections.append((KWIN_RULE_SECTION, rule_lines))

    out_chunks: list[str] = []
    if preamble and any(ln.strip() for ln in preamble):
        out_chunks.append("\n".join(preamble).strip())
    for name, lines in sections:
        body = "\n".join(ln for ln in lines if ln.strip() != "")
        out_chunks.append(f"[{name}]\n{body}" if body else f"[{name}]")

    new_text = "\n\n".join(out_chunks) + "\n"
    changed = new_text != existing_text
    if changed:
        kwinrules_path.parent.mkdir(parents=True, exist_ok=True)
        kwinrules_path.write_text(new_text, encoding="utf-8")

    if reconfigure and changed:
        qdbus = _find_qdbus()
        if qdbus:
            try:
                subprocess.run(
                    [qdbus, "org.kde.KWin", "/KWin", "reconfigure"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=3,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                pass
    return True


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
    toast_title: str = TOAST_WINDOW_TITLE,
) -> str:
    """產生 KWin 6 JS 腳本：控制覆蓋層、查詞浮窗與右下角收錄通知置頂、記憶拖曳位置並在字幕改變尺寸時維持螢幕邊界。"""
    ov_js = json.dumps(overlay_title, ensure_ascii=False)
    pop_js = json.dumps(popup_title, ensure_ascii=False)
    toast_js = json.dumps(toast_title, ensure_ascii=False)
    pin_js = "true" if pinned else "false"
    if initial_geometry is not None:
        ix, iy, iw, ih = initial_geometry
        init_geom_js = f"{{ x: {int(ix)}, y: {int(iy)}, width: {int(iw)}, height: {int(ih)} }}"
    else:
        init_geom_js = "null"

    return f"""
const OVERLAY_TITLE = {ov_js};
const POPUP_TITLE = {pop_js};
const TOAST_TITLE = {toast_js};
const PINNED = {pin_js};
let savedOverlayGeom = {init_geom_js};
let placingOverlay = false;
let placingToast = false;

function clampRect(w, x, y, width, height) {{
    let area = null;
    try {{
        area = workspace.clientArea(KWin.MaximizeArea, w);
    }} catch (_e) {{
        area = null;
    }}
    if (!area || area.width <= 0 || area.height <= 0) {{
        return {{ x: Math.round(x), y: Math.round(y), width, height }};
    }}
    const nx = Math.max(area.x + 6, Math.min(Math.round(x), area.x + area.width - width - 6));
    const ny = Math.max(area.y + 6, Math.min(Math.round(y), area.y + area.height - height - 6));
    return {{ x: nx, y: ny, width, height }};
}}

function rememberOverlayPosition(w) {{
    if (placingOverlay || !w.frameGeometry) return;
    const g = w.frameGeometry;
    if (w.move || w.resize || !savedOverlayGeom || (savedOverlayGeom.width === g.width && savedOverlayGeom.height === g.height)) {{
        savedOverlayGeom = {{ x: g.x, y: g.y, width: g.width, height: g.height }};
        return;
    }}
    let desiredX = g.x;
    let desiredY = g.y;
    let area;
    try {{
        area = workspace.clientArea(KWin.MaximizeArea, w);
    }} catch (_e) {{
        area = null;
    }}
    if (area) {{
        if (Math.abs(area.x + area.width - (savedOverlayGeom.x + savedOverlayGeom.width)) <= 24)
            desiredX = savedOverlayGeom.x + savedOverlayGeom.width - g.width;
        if (Math.abs(area.y + area.height - (savedOverlayGeom.y + savedOverlayGeom.height)) <= 24)
            desiredY = savedOverlayGeom.y + savedOverlayGeom.height - g.height;
    }}
    const rect = clampRect(w, desiredX, desiredY, g.width, g.height);
    savedOverlayGeom = rect;
    if (g.x === rect.x && g.y === rect.y) return;
    placingOverlay = true;
    try {{
        w.frameGeometry = rect;
    }} finally {{
        placingOverlay = false;
    }}
}}

function positionToastBottomRight(toast) {{
    if (!toast || placingToast) return;
    const tg = toast.frameGeometry;
    if (!tg || tg.width <= 0 || tg.height <= 0) return;
    let area = null;
    try {{
        area = workspace.clientArea(KWin.MaximizeArea, toast);
    }} catch (_e) {{
        area = null;
    }}
    if (!area || area.width <= 0 || area.height <= 0) return;
    const nx = Math.round(area.x + area.width - tg.width - 18);
    const ny = Math.round(area.y + area.height - tg.height - 22);
    if (Math.abs(tg.x - nx) > 4 || Math.abs(tg.y - ny) > 4) {{
        placingToast = true;
        try {{
            toast.frameGeometry = {{ x: nx, y: ny, width: tg.width, height: tg.height }};
        }} finally {{
            placingToast = false;
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
        if (!w.move && !w.resize && savedOverlayGeom && w.frameGeometry) {{
            const g = w.frameGeometry;
            const rect = clampRect(w, savedOverlayGeom.x, savedOverlayGeom.y, g.width, g.height);
            if (Math.abs(g.x - rect.x) > 4 || Math.abs(g.y - rect.y) > 4) {{
                w.frameGeometry = rect;
            }}
        }}
        if (w.frameGeometry) {{
            const geometry = w.frameGeometry;
            savedOverlayGeom = {{ x: geometry.x, y: geometry.y, width: geometry.width, height: geometry.height }};
        }}
    }} else if (w.caption === POPUP_TITLE) {{
        w.keepAbove = PINNED;
        w.onAllDesktops = PINNED;
        w.skipTaskbar = true;
        w.skipPager = true;
    }} else if (w.caption === TOAST_TITLE) {{
        w.keepAbove = true;
        w.onAllDesktops = true;
        w.skipTaskbar = true;
        w.skipPager = true;
        positionToastBottomRight(w);
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
            if (w.caption === OVERLAY_TITLE) {{
                rememberOverlayPosition(w);
            }} else if (w.caption === TOAST_TITLE) {{
                positionToastBottomRight(w);
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
        threading.Thread(
            target=self._apply_sync,
            args=(bool(pinned), initial_geometry, force_reload),
            daemon=True,
        ).start()

    def ensure_active(self, initial_geometry: tuple[int, int, int, int] | None = None) -> None:
        """若尚未載入 KWin 置頂腳本，背景自動啟用（保留執行期間使用者拖曳過的位置）。"""
        self.apply_pin(self._pinned, initial_geometry=initial_geometry, force_reload=False)

    def _apply_sync(
        self,
        pinned: bool,
        initial_geometry: tuple[int, int, int, int] | None,
        force_reload: bool,
    ) -> None:
        qdbus = _find_qdbus()
        if not qdbus:
            return
        with self._lock:
            if self._active and not force_reload:
                return
            ensure_kwin_overlay_layer_rule(enabled=pinned, reconfigure=True)
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
                self._unload_locked(qdbus, KWIN_MINIMIZE_ANKI_PLUGIN)
                self._active = False
            if self._script_path is not None:
                try:
                    self._script_path.unlink(missing_ok=True)
                except OSError:
                    pass
                self._script_path = None


def build_kwin_minimize_anki_script(window_ms: int = 18000) -> str:
    """產生一次性 KWin 6 JS 腳本：自動將剛在背景啟動的 Anki 主視窗最小化一次，絕不干擾之後手動點開 Anki。"""
    deadline_ms = max(3000, int(window_ms))
    return f"""
const DEADLINE = Date.now() + {deadline_ms};
let done = false;

function isAnkiWindow(w) {{
    if (!w) return false;
    const rc = String(w.resourceClass || "").toLowerCase();
    const rn = String(w.resourceName || "").toLowerCase();
    const cap = String(w.caption || "");
    if (rc.includes("anki") || rn.includes("anki")) {{
        if (!cap.includes("GalAnki")) return true;
    }}
    return false;
}}

function tryMinimize(w) {{
    if (done || Date.now() > DEADLINE) return;
    if (isAnkiWindow(w)) {{
        w.minimized = true;
        done = true;
    }}
}}

const list = workspace.windowList ? workspace.windowList() : [];
for (const w of list) {{
    tryMinimize(w);
}}
if (!done && workspace.windowAdded) {{
    workspace.windowAdded.connect(tryMinimize);
}}
""".strip() + "\n"


def minimize_anki_once_kwin(window_ms: int = 18000) -> None:
    """背景啟動 Anki 後，在 KDE Plasma Wayland 自動將剛彈出的 Anki 視窗最小化一次並自動卸載腳本。"""
    if not is_kde_wayland_kwin_available():
        return
    qdbus = _find_qdbus()
    if not qdbus:
        return

    def _run() -> None:
        script_file = Path(tempfile.gettempdir()) / f"galanki_min_anki_{os.getpid()}.js"
        try:
            subprocess.run(
                [qdbus, "org.kde.KWin", "/Scripting", "unloadScript", KWIN_MINIMIZE_ANKI_PLUGIN],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
                check=False,
            )
            script_file.write_text(build_kwin_minimize_anki_script(window_ms), encoding="utf-8")
            res = subprocess.run(
                [qdbus, "org.kde.KWin", "/Scripting", "loadScript", str(script_file), KWIN_MINIMIZE_ANKI_PLUGIN],
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
        except (OSError, subprocess.SubprocessError):
            pass

        def _cleanup() -> None:
            try:
                subprocess.run(
                    [qdbus, "org.kde.KWin", "/Scripting", "unloadScript", KWIN_MINIMIZE_ANKI_PLUGIN],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=2,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                pass
            try:
                script_file.unlink(missing_ok=True)
            except OSError:
                pass

        timer = threading.Timer(max(4.0, window_ms / 1000.0 + 1.5), _cleanup)
        timer.daemon = True
        timer.start()

    threading.Thread(target=_run, daemon=True).start()
