# GalAnki 🌸

**邊玩生肉 Galgame、看 mpv 本地動畫，邊系統化學習日語的一鍵 Anki 工具。**

設計方法論源自 [《關於我一年不到速通 N1 這件事——基於啃生肉 Galgame 的日語學習攻略》](https://galgame.dev/topic/639/%E5%85%B3%E4%BA%8E%E6%88%91%E4%B8%80%E5%B9%B4%E4%B8%8D%E5%88%B0%E9%80%9F%E9%80%9An1%E8%BF%99%E4%BB%B6%E4%BA%8B-%E5%9F%BA%E4%BA%8E%E5%95%83%E7%94%9F%E8%82%89galgame%E7%9A%84%E6%97%A5%E8%AF%AD%E5%AD%A6%E4%B9%A0%E6%94%BB%E7%95%A5)（Refold 沉浸輸入 × 登樓撤梯三階段 × FSRS 防爆倉），針對 **Linux（Fedora + KDE Plasma Wayland + Wine + PipeWire + Flatpak）** 深度最佳化，無需付費 AI API。

---

## 核心功能

| 模組 | 說明 |
| :--- | :--- |
| **🎮 Galgame & mpv 字幕擷取** | 一鍵日區啟動 Wine 遊戲並自動注入 `TextractorCLI`（自動消重與漸進前綴修復）；完整支援 **Flatpak `io.mpv.Mpv`** 與原生 `mpv` IPC 字幕串流（自動隱藏原字幕）；內建 `Ctrl+Shift+O` 區域 OCR 兜底。 |
| **🪟 置頂遮蓋替換覆蓋層** | 預設 **🎭 遮蓋替換模式**（100% 不透明深黑／暖白／純黑底色直接覆蓋遊戲原字，絕不看兩份字幕）、**📌 KDE Wayland KWin 原生置頂**、固定尺寸零跳動、平時零按鈕、滑鼠懸停即查詞（Yomitan 風格）。 |
| **🧠 4 層日語 NLP 語境釋義** | Janome（MeCab IPADIC）詞形詞性還原 ＋ 口語活用合併 ＋ 高頻口語詞表 ＋ 詞頻消歧義，精準還原口語活用（如 `わかった → 明白了`），支援 Yomitan `.zip` 詞典管理、單獨開關與優先序調整。 |
| **📦 遊戲解包純淨人聲 ＋ 畫面 CG 精煉製卡** | 免拆滿硬碟直讀 Kirikiri `.xp3`（支援 **ATRI 等 CxDec** ＋ M2 `.scn` PSB）與已解包資料夾，建立「**台詞 ↔ 聲優原聲**」SQLite 索引並抽出 **0% BGM・0% 音效** 純淨原聲；支援 mpv FFmpeg 無損抽音抽幀 ＋ KDE `spectacle` 截圖，經 **1 秒 Refold 精煉確認窗**寫入 Anki。 |
| **🗺️ 離線 N5→N1 系統學習藍圖** | 內建 51 條 N5→N1 核心句型、18 個助詞白話角色、日語語序直譯骨架、30 秒詞典／活用速查表，邊玩邊點亮已遇見語法；另附 **📋 問 Gemini/GPT** 一鍵複製提問詞。 |

---

## 快速安裝與啟動

### 1. 環境需求（Fedora / Linux）
- Python 3.10+、`PySide6`、`janome`
- Anki（含 [AnkiConnect](https://ankiweb.net/shared/info/2055492159) `2055492159`，建議於牌組選項啟用 **FSRS**）
- 選配：`wine`（Galgame）、`mpv` 或 Flatpak `io.mpv.Mpv`（動畫）、`ffmpeg`（純人聲轉 MP3 與影片切音抽幀）、`spectacle`（KDE 截圖）、`tesseract`（OCR）

```bash
pip install --user PySide6 janome
```

### 2. 下載並加入系統應用程式選單（免開終端機！）
```bash
git clone https://github.com/vaiorett/galgame-anki.git
cd galgame-anki
chmod +x run.sh install-desktop.sh
./install-desktop.sh
```
執行後，直接在系統應用程式選單（KDE 開始選單）搜尋 **「GalAnki」** 即可點擊圖示啟動，也可右鍵釘選到工作列。

### 3. 命令列工具（選用）
```bash
./run.sh              # 啟動主程式
./run.sh --diagnose   # 檢查 Wayland / Wine / Anki / mpv / FFmpeg 環境
./run.sh --init-anki  # 一鍵同步 Anki 牌組與三階段原生主題模板
./run.sh --demo       # 載入攻略範例句試玩
```

---

## 常用快捷鍵

| 快捷鍵 | 功能 |
| :--- | :--- |
| `Ctrl + M` / `Ctrl + Enter` | 將目前選中詞彙打開 Refold 精煉製卡窗收錄至 Anki |
| `Ctrl + P` | 試聽目前台詞的聲優原聲 |
| `Ctrl + Shift + O` | 框選任意畫面區域執行離線日文 OCR |
| `Ctrl + B` | 開啟 N5→N1 系統化學習藍圖與 30 秒速查表 |
| `Ctrl + 1` / `2` / `3` | 切換登樓撤梯階段（Lv.1 假名輔助 ／ Lv.2 1T 雙語 ／ Lv.3 日日單語） |

---

## 專案架構

```text
galgame-anki/
├── galgame_anki/
│   ├── main.py                # CLI 與 GUI 啟動入口
│   ├── config.py              # 設定持久化 (~/.config/galgame-anki/config.json)
│   ├── core/                  # 分詞、活用還原、SQLite 詞典、語境釋義、純人聲包索引、離線語法、AnkiConnect
│   ├── adapters/              # Textractor Hook、XP3+CxDec/PSB 解析、mpv IPC、Wayland 置頂、FFmpeg 媒體、OCR、截圖
│   ├── ui/                    # 主視窗、置頂遮蓋覆蓋層、純人聲解包管理、查詞浮窗、Refold 製卡窗、學習藍圖
│   └── data/                  # 高頻口語詞表、N5→N1 語法藍圖、基礎詞典與應用圖示
├── tests/                     # 單元與 PySide6 Offscreen GUI 自動化測試
├── run.sh                     # 一鍵啟動腳本
├── install-desktop.sh         # 安裝系統選單快捷方式與圖示
└── AGENTS.md                  # AI 助手交接與開發規範
```

---

## 執行測試

```bash
XDG_CONFIG_HOME=/tmp/galanki_t_cfg XDG_DATA_HOME=/tmp/galanki_t_data \
QT_QPA_PLATFORM=offscreen \
python3 -m unittest discover -s tests -v
```
