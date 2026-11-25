# GalAnki 🌸

**邊玩生肉 Galgame、看 mpv 本地動畫，邊系統化學習日語的一鍵 Anki 工具。**

設計方法論源自 [《關於我一年不到速通 N1 這件事——基於啃生肉 Galgame 的日語學習攻略》](https://galgame.dev/topic/639/%E5%85%B3%E4%BA%8E%E6%88%91%E4%B8%80%E5%B9%B4%E4%B8%8D%E5%88%B0%E9%80%9F%E9%80%9An1%E8%BF%99%E4%BB%B6%E4%BA%8B-%E5%9F%BA%E4%BA%8E%E5%95%83%E7%94%9F%E8%82%89galgame%E7%9A%84%E6%97%A5%E8%AF%AD%E5%AD%A6%E4%B9%A0%E6%94%BB%E7%95%A5)（Refold 沉浸輸入 × 登樓撤梯三階段 × FSRS 防爆倉），針對 **Linux（Fedora + KDE Plasma Wayland + Wine + PipeWire + Flatpak）** 深度最佳化，無需付費 AI API。

---

## 核心功能

| 模組 | 說明 |
| :--- | :--- |
| **🎮 Galgame & mpv 字幕擷取** | 一鍵日區啟動 Wine 遊戲並自動注入 `TextractorCLI`；完整支援 **Flatpak `io.mpv.Mpv`** 與原生 `mpv` IPC 字幕串流（自動隱藏原字幕、**多句合併 `Ctrl+J`**、**無字幕片段自動倍速 `sub-transition`**、句尾自動暫停與重播）。 |
| **🪟 單螢幕隱形伴讀（系統匣＋極簡覆蓋層）** | 啟動時**主視窗收進系統匣＋背景自動啟動 Anki 並最小化**，螢幕 100% 留給遊戲／mpv！字幕框沿用儲存尺寸，長句可用滾輪閱讀。滑到右下角小圓弧再點齒輪，或按右鍵，即可開啟播放與設定選單；懸停查詞後**左鍵一鍵直寫 Anki（右下角浮動「↩ 撤銷」條），按住 `Shift` 點擊才開編輯窗**。 |
| **🧠 4 層日語 NLP ＋ 語境去重 ＋ 聯想記憶樹** | Janome 詞形還原 ＋ 口語活用合併 ＋ **跨詞典語意去重與語境相關排序**（自動過濾重複義項與例句雜訊、依本句推舉 `★` 首選重點）；內建東京式 **SVG 音調曲線** 與離線 **🌳 聯想記憶樹**（同漢字詞族「以熟帶生」・常用複合詞・自他動詞／同音異義詞辨析・防劇透同作品重現），可收折寫入 Anki `RelatedWords` 欄位。 |
| **📦 解包純人聲・角色濃縮聽力・1T 礦場** | 免拆滿硬碟直讀 Kirikiri `.xp3`（支援 **ATRI 等 CxDec** ＋ M2 `.scn` PSB）與已解包目錄，抽出 **0% BGM・0% 音效** 純人聲；支援 **按單一角色嚴格分軌匯出濃縮被動聽力 MP3＋.lrc（絕不混音不同角色）** 與 **💎 1T 黃金句礦場**（防劇透章節＋JPDB 詞頻挖礦）。 |
| **離線 N5→N1 語法技能樹** | 內建 **173 條 N5→N1 ＋ 口語實戰句型**（8 大領域分類、即時搜尋與進度追蹤）、24 個核心助詞角色、日語語序骨架、3 步上手與活用速查表；另附「複製 AI 提問」一鍵貼給網頁版 Gemini / ChatGPT。 |

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
./run.sh              # 隱形模式啟動（系統匣＋字幕覆蓋層＋背景自動啟動 Anki）
./run.sh --window     # 啟動時直接顯示主控制台視窗
./run.sh --diagnose   # 檢查 Wayland / Wine / Anki / mpv / FFmpeg 環境
./run.sh --init-anki  # 一鍵同步 Anki 牌組與三階段原生主題模板
./run.sh --demo       # 載入攻略範例句試玩
```

---

## 常用快捷鍵

| 快捷鍵 | 功能 |
| :--- | :--- |
| `Ctrl + M` / `Ctrl + Enter` | 一鍵直寫目前詞彙至 Anki（右下角可點 `↩ 撤銷`） |
| `Ctrl + Shift + Enter` / `Shift + 點擊收錄` | 開啟 Refold 精煉製卡編輯窗手動挑選釋義 |
| `Ctrl + P` | 試聽目前台詞的聲優原聲 |
| `Ctrl + J` | 合併上一句與本句台詞（連同 `mpv` 時間軸原聲一併合併） |
| `Ctrl + Shift + M` | 開啟「💎 1T 黃金句礦場（Sentence Bank）」從已解包劇本挖礦 |
| `Ctrl + B` | 開啟 N5→N1 系統化學習藍圖與 30 秒速查表 |
| `Ctrl + 1` / `2` / `3` | 切換登樓撤梯階段（Lv.1 假名輔助 ／ Lv.2 1T 雙語 ／ Lv.3 日日單語） |
| `Ctrl + Q` | 完全退出 GalAnki（平時按 `✕` 僅收進系統匣） |

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
