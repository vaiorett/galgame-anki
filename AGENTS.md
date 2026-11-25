# AGENTS.md — GalAnki 專案交接與開發規範

本檔案供接手此專案的任何 AI 編程助手（Claude Code / Codex / Cursor / Gemini CLI / Antigravity 等）快速掌握架構、使用者偏好、硬性約束與本機環境細節。

---

## 1. 專案目標與設計核心

- **專案路徑**：`/home/vaiorett/Projects/galgame-anki`（獨立專案，切勿修改其他目錄）
- **定位**：邊玩日語生肉 Galgame／看 mpv 本地動畫邊系統化學習日語，一鍵製作高品質 Anki 記憶卡。
- **方法論來源**：`https://galgame.dev/topic/639/...`（Refold 沉浸式輸入 + 三階段「登樓撤梯」Lv.1→Lv.2→Lv.3 + SuperMemo 最小資訊原則 + FSRS 防爆倉）。

### 使用者硬性要求（務必遵守，不要踩雷）
1. **語言**：所有介面文案與與使用者溝通皆使用 **繁體中文**。
2. **禁止付費 AI API 功能**：使用者只訂閱網頁版 Google AI Pro / ChatGPT Plus，不要加入任何需要付費 API Key 的 AI 介面；僅保留免費「📋 問 Gemini/GPT」一鍵複製提問詞。
3. **禁止瀏覽器字幕同步功能**：使用者已明確要求刪除瀏覽器擴充／Bookmarklet／YouTube・Netflix・Bilibili 字幕同步功能，請勿重新加入。
4. **Anki 卡片外觀**：必須嚴格繼承 Anki 原生默認日間／夜間主題（透明底、不硬寫藍色或突兀配色），符合 Refold 精煉句卡設計（單一語境釋義＋聲優原聲＋當下畫面 CG＋整句理解＋離線語法骨架），**嚴禁把整頁原始詞典硬搬進 Anki**。
5. **單筆電螢幕極簡隱形伴讀（硬性 UX 偏好）**：
   - 啟動時預設**主視窗收進系統匣、只顯示字幕覆蓋層**，同時**自動在背景啟動 Anki 並透過 KWin 一次性最小化**；螢幕 100% 留給遊戲／mpv。
   - 收錄預設**一鍵直寫 Anki**（零彈窗、右下角 `FloatingUndoToast` 提供 6 秒 `↩ 撤銷`），**按住 `Shift` 點擊才開啟精煉編輯窗**。
   - 覆蓋層懸停工具列維持極簡（僅 6 個核心圖示＋`⚙` 設定選單），主視窗保持精簡 5 區頂欄，**不要再把 `📷 OCR` 按鈕或大排雜訊按鈕塞回主介面**。

---

## 2. 使用者本機實測環境

- **OS / 桌面**：Fedora Linux、KDE Plasma 6 Wayland（`kwin_wayland 6.8.3`、`Scale: 1.5` 1.5x HiDPI、中文字系 `~/下载`）
- **Python / 依賴**：Python 3.15、`PySide6`、`janome`（純 Python MeCab IPADIC Viterbi，已寫入 `pyproject.toml`）
- **Anki**：Flatpak `net.ankiweb.Anki`（Anki 26.x）+ AnkiConnect (`2055492159`, `http://127.0.0.1:8765`)，已啟用 FSRS
- **mpv**：Flatpak `io.mpv.Mpv` v0.41.0（`/tmp` 與宿主持久共用；設定檔位於 `~/.var/app/io.mpv.Mpv/config/mpv/mpv.conf`）
- **系統工具**：`ffmpeg`（純人聲轉碼／mpv 切音抽幀）、`spectacle`（KDE 靜默截圖）、`tesseract`（OCR）、`wine`、`qdbus`（已移除系統背景錄音功能）

---

## 3. 程式碼架構導覽

```text
galgame_anki/
├── main.py                  # 進入點（支援 --diagnose / --init-anki / --demo）
├── config.py                # AppConfig 持久化設定（~/.config/galgame-anki/config.json）
├── core/
│   ├── tokenizer.py         # Janome 共享分詞 + Galgame 口語預合併 + 活用片語合併 + 1T 偵測
│   ├── deinflector.py       # 日語規則活用還原引擎
│   ├── dictionary.py        # SQLite 詞典庫（RLock 執行緒安全、Yomitan 匯入、跨詞典語意原子去重、例句/ISBN/詞性雜訊過濾）
│   ├── context_meaning.py   # 活用語境直覺譯義（如 わかった → 明白了）＋中譯／日語語法線索相關性重排
│   ├── word_associations.py # 離線日語「🌳 聯想記憶樹」（同漢字詞族以熟帶生・常用複合詞・自他/同音易混・防劇透作品重現）
│   ├── grammar_engine.py    # 離線 N5→N1 51 條句型 + 18 助詞白話角色 + 語序骨架 + 免費句意翻譯 + Web AI 提問詞
│   ├── learning_progress.py # SQLite 實戰語法解鎖進度
│   ├── hook_cleaner.py      # Textractor 漸進前綴重繪與重複字元清理
│   ├── anki_client.py       # AnkiConnect 客戶端 + Anki 26 collection.anki2 FSRS 偵測
│   ├── anki_templates.py    # Refold 三階段原生主題 Anki 卡片模板（含 PitchGraph SVG、RelatedWords 聯想折疊區）
│   ├── pitch_accent.py      # 東京式モーラ拆解、四型分類（平板/頭高/中高/尾高）與 Inline SVG 音調曲線產生器
│   ├── voice_pack.py        # 遊戲解包台詞↔純淨人聲 SQLite 索引、角色嚴格分軌濃縮聽力 MP3+LRC 匯出
│   ├── sentence_bank.py     # 1T 黃金句礦場兩階段高速掃描器（防劇透章節、角色過濾、JPDB 詞頻排序、覆蓋率統計）
│   └── sentence_store.py    # 近期 50 句歷史、多句合併（含 mpv 時間軸擴展）與音畫快取
├── adapters/
│   ├── hook_engine.py       # TextractorCLI 自動下載／32-64bit 注入／線程評分與去重
│   ├── xp3_psb.py           # 純 Python Kirikiri XP3（含 CxEncryption）+ M2 PSB (.scn) 解析與 Formats.dat 本機快取
│   ├── mpv_bridge.py        # Flatpak io.mpv.Mpv 與原生 mpv 自動偵測、IPC 監聽與原生字幕隱藏
│   ├── window_pin.py        # KDE Plasma 6 Wayland KWin DBus 原生 keepAbove 置頂與懸停浮窗定位
│   ├── media_utils.py       # ffplay/paplay 試聽 ＋ mpv FFmpeg 無損切音抽幀（無系統錄音）
│   ├── linux_wayland.py     # Wine 日區啟動、Spectacle 靜默 WebP 截圖、Wayland 環境診斷
│   ├── ocr_engine.py        # 框選 OCR 辨識
│   ├── text_sources.py      # 本機 WebSocket (127.0.0.1:6677) + wl-paste 剪貼簿監聽
│   └── windows.py / base.py # 跨平台抽象層
├── ui/
│   ├── main_window.py       # 單螢幕精簡控制台 ＋ QSystemTrayIcon 系統匣伴讀核心
│   ├── overlay_window.py    # 置頂遮蓋替換覆蓋層（極簡 6 圖示微工具列＋⚙ 選單、固定尺寸、無阻膠囊）
│   ├── toast_widget.py      # 右下角置頂浮動通知條（一鍵直寫 → 6 秒 ↩ 撤銷，永不搶遊戲焦點）
│   ├── sentence_widget.py   # FlowLayout 互動分詞台詞（支援懸停查詞與零殘影清空）
│   ├── lookup_popup.py      # 主視窗詳情卡 & 覆蓋層 Yomitan 懸停浮窗（含多候選原形切換）
│   ├── card_forge_dialog.py # 1 秒 Refold 精煉製卡確認窗（可點選核心釋義膠囊、試聽純人聲、CG 預覽）
│   ├── blueprint_dialog.py  # N5→N1 系統化學習藍圖 & 30 秒詞典／活用／五十音速查表
│   ├── media_bridge_dialog.py # mpv 本地動畫連線、無字幕自動倍速（sub-transition）與精讀句尾暫停視窗
│   ├── voice_pack_dialog.py   # 遊戲解包純淨人聲索引、角色分類濃縮聽力 MP3 獨立分檔匯出與即時試聽
│   ├── sentence_bank_dialog.py # 💎 1T 黃金句礦場（Sentence Bank）篩選、試聽與一鍵精煉收錄視窗
│   ├── pitch_widget.py        # 原生 QPainter HiDPI 高低音調曲線視覺化元件
│   ├── extra_dialogs.py     # 詞典管理（勾選啟用、上下排序、預設模式）＆ 難度避雷指南
│   ├── hook_dialog.py       # Hook 線程／特殊碼／日誌管理
│   ├── setup_wizard.py      # 一鍵初始化精靈
│   ├── settings_dialog.py   # 設定視窗
│   ├── theme.py             # 🌸 櫻霧輕透 / 🌙 星夜琉璃 二次元雙主題
│   └── workers.py           # QRunnable 背景任務執行器
└── data/
    ├── core_spoken_dict.json    # 優先序 #1 高頻口語詞表（解決 わかった/おいおい/あんた 等）
    ├── grammar_blueprint.json   # N5→N1 句型與助詞資料
    ├── game_difficulty_guide.json
    └── starter_dict.json
```

---

## 4. 已踩過並修復的關鍵技術坑（維護時請保留！）

1. **KDE Wayland 置頂**：Qt 的 `WindowStaysOnTopHint` 在 Wayland 無效；不要改強切 `QT_QPA_PLATFORM=xcb`（會讓 1.5x HiDPI 模糊）。請保持 [`adapters/window_pin.py`](galgame_anki/adapters/window_pin.py) 透過 KWin 6 DBus Scripting 控制 `w.keepAbove`（於 `MainWindow.closeEvent` 自動卸載）。
2. **`わかった` 誤判為 `分かつ`**：透過 `janome` + `core_spoken_dict.json` + [`dictionary.py`](galgame_anki/core/dictionary.py) 多候選詞頻／讀音／POS 消歧義解決；修改分詞或詞典後務必跑回歸測試 `test_frequency_disambiguation_wakatta_and_contextual_meaning`。
3. **覆蓋層換句殘影**：[`InteractiveSentenceWidget.clear_tokens()`](galgame_anki/ui/sentence_widget.py) 必須先 `w.hide(); w.setParent(None)` 再 `w.deleteLater()`，否則快速換句會出現前後句重疊。
4. **覆蓋層禁止自動縮放**：`OverlayWindow.show_record()` 嚴禁呼叫 `self.adjustSize()`；切換無阻膠囊回復時須先還原 `resize(target_w, target_h)` 再將 `_freeflow_mode = False`。
5. **Flatpak `io.mpv.Mpv` 隱藏原字幕**：實測 mpv v0.41.0 在 `--sub-visibility=no` 下仍可正常透過 `/tmp/galanki-mpv.sock` 推送 `sub-text` 事件。
6. **遊戲解包純淨人聲（已移除系統錄音／零著作權金鑰隨碼入庫）**：[`xp3_psb.py`](galgame_anki/adapters/xp3_psb.py) 不在倉庫內儲存任何遊戲解密 `ControlBlock` 或 `Formats.dat`；使用時由使用者動態下載公開社群資料庫快取至 `~/.local/share/galgame-anki/garbro/Formats.dat`，僅從 `.xp3` 索引偏移並對當前台詞單檔解包轉 MP3；命中旁白（無語音）時標記 `audio_source="silent"`，不產生多餘錄音檔案。

---

## 5. 常用開發與驗證指令

```bash
# 啟動應用程式
/home/vaiorett/Projects/galgame-anki/run.sh

# 執行完整單元 + Offscreen GUI 測試（使用隔離 XDG 目錄不汙染使用者設定）
XDG_CONFIG_HOME=/tmp/galanki_t_cfg XDG_DATA_HOME=/tmp/galanki_t_data \
QT_QPA_PLATFORM=offscreen \
PYTHONPATH=/home/vaiorett/Projects/galgame-anki \
python3 -m unittest discover -s /home/vaiorett/Projects/galgame-anki/tests -v
```
