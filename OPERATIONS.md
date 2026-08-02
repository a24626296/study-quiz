# 航空考題自動化系統 — 使用與維運說明

這份文件整理整套系統的架構、檔案分工,以及「什麼情況需要手動做什麼事」。
之後如果隔一段時間沒碰,回來看這份就能想起來怎麼運作。

---

## 1. 兩套環境,各自獨立

| | 本機版 | 雲端版 |
|---|---|---|
| 檔案 | `study_pipeline.py`(單一檔案) | `scripts/generate_from_url.py` |
| 執行環境 | 你的 Windows 桌機 | GitHub Actions(Linux,無頭) |
| 用途 | 下載、Anki 匯出、本機線上快速模式 | 貼網址 → 自動出題 → 部署到 GitHub Pages |
| 播放清單 | 用 yt-dlp(本機不受雲端IP封鎖限制) | 用官方 YouTube Data API v3 |
| 刪除複習頁面 | 本機小伺服器,按鈕直接生效 | 需觸發「刪除線上複習頁面」workflow |

這兩支檔案**功能設計儘量一致,但程式碼各自獨立**,原因是執行環境差異太大
(桌機有 GUI 選單、yt-dlp、ffmpeg;雲端是無頭 CI,沒有這些)。
**新增/修改功能時,兩邊都要改**,這是目前架構下無法避免的維護成本。

---

## 2. `online_study/` 資料夾內的檔案分工

```
online_study/
├── viewer.html         ← 靜態樣板,手動維護,不會被程式覆寫
├── viewer.js            ← 靜態樣板,手動維護,不會被程式覆寫
├── style.css             ← 靜態樣板,手動維護,不會被程式覆寫
├── chat-config.js         ← 靜態,你自己填入AI對話金鑰,第一次建立後不會被覆寫
├── data/{video_id}.json    ← Python 自動產生,每支影片的題目資料
├── data/{video_id}.srt      ← Python 自動產生(選用),修正逐字稿
├── manifest.json             ← Python 自動產生,記錄科目/網址/時間
└── index.html                  ← Python 自動產生,複習清單首頁
```

**關鍵原則(類似 Anki 正面/背面 Code 分離)**:
- `viewer.html` / `viewer.js` / `style.css` 這三個是**共用樣板**,以後要加按鈕、
  改介面,只要改這三個檔案,所有影片頁面立刻套用新樣板,完全不用重新出題。
- `data/*.json` 才是真正的內容(Gemini 出的題目),跟樣板完全分開存放。

---

## 3. 什麼時候需要「手動觸發 workflow」?

這是目前最容易搞混的地方,整理成規則:

### 完全不用手動觸發的情況
- 只改 `viewer.html` / `viewer.js` / `style.css` / `chat-config.js`,而且已經有
  **`deploy_only.yml`** 這個 workflow(push 到 `online_study/**` 就自動部署)。
  → push 上去、等 Action 跑完,重新整理頁面(必要時 Ctrl+Shift+R 強制重新整理)即可。

### 需要手動觸發一次的情況
- 修改了 `scripts/generate_from_url.py` 裡**跟 `index.html` 產生邏輯有關**的部分
  (例如收合、封存、格狀檢視這類清單頁功能)。
  → **已經修好這個問題**:`deploy_only.yml` 現在多了一步
  `python scripts/generate_from_url.py --rebuild-index`,每次 push 都會用「目前腳本的邏輯」
  重新產生 `index.html`,不用再手動觸發假刪除了。
- 如果你看到清單頁沒有反映新功能,先檢查 Actions 頁面「部署複習頁面(不出題)」
  這個 workflow 有沒有真的跑完、有沒有失敗。

### 一定要手動觸發、沒有自動化的情況
- **真的要出一支新影片的題目** → 觸發「產生線上複習頁面」,填網址。
- **真的要刪除某支影片的複習頁面** → 觸發「刪除線上複習頁面」,填 video_id。
  （這兩個是「真的要做事」的動作,本來就該手動觸發,不適合自動化。）

---

## 4. 自我檢查機制(這次新加的)

`generate_from_url.py` 跟 `study_pipeline.py` 現在都會在啟動 / 執行時自動檢查:

```
✅ 【自我檢查】共用靜態檔案(viewer.html/viewer.js/style.css/chat-config.js)都存在
```

或

```
⚠️ 【自我檢查】online_study/ 資料夾裡缺少這些共用靜態檔案:viewer.html, style.css
```

**目的**:過去發生過好幾次「漏傳靜態檔案 → 複習頁面 404 / 功能沒作用 → 卻沒有任何
錯誤訊息」的狀況,靠你自己發現、我再回頭猜原因。現在只要跑一次腳本(不管本機
還是雲端),就會主動告訴你缺了什麼,不用再靠肉眼比對。

這個檢查目前只會**印出警告**,不會讓流程失敗——因為就算暫時缺檔案,出題本身
還是能正常進行、資料不會遺失,只是複習頁面顯示會不完整。

另外新增了 `--rebuild-index` 這個模式:

```
python scripts/generate_from_url.py --rebuild-index
```

不需要 `GEMINI_API_KEY`,純粹重新產生 `index.html` + 跑一次自我檢查,`deploy_only.yml`
每次 push 都會自動跑這個,確保清單頁永遠反映「目前 repo 裡腳本的最新邏輯」。

---

## 5. 需要的 GitHub Secrets 一覽

| Secret 名稱 | 必要性 | 用途 |
|---|---|---|
| `GEMINI_API_KEY` | 必要 | 呼叫 Gemini 出題 |
| `YOUTUBE_API_KEY` | 選填,播放清單需要 | 讀取播放清單內容(YouTube Data API v3,免費) |
| `MY_CHANNEL_ID` | 選填 | 自動偵測是不是自己的頻道,只有自己的影片才自動開逐字稿功能 |

`chat-config.js` 裡的 AI 對話金鑰**不是** GitHub Secret,是直接寫在那個檔案裡的
（因為要給瀏覽器前端用),記得那把要設定 HTTP referrer 限制,只允許你的網站網域呼叫。

---

## 6. 已知限制 / 目前的設計取捨

- **本機版跟雲端版程式碼重複** — 前面提過,新增功能要兩邊都改,目前沒有更好的解法
  (強行共用會讓桌機工具變回「一堆檔案」,違背你要單一檔案的需求)。
- **封存功能用瀏覽器 `localStorage`** — 換裝置或無痕模式看不到封存狀態,這是刻意的
  輕量化取捨。如果之後想要「真正封存、跨裝置同步」,需要改成寫回 `manifest.json`
  的版本(類似刪除功能,需要跑 workflow),目前還沒做。
- **AI 對話框的金鑰是公開曝露的** — 已用 HTTP referrer 限制降低風險,但終究不是
  零風險設計,只適合免費、低額度的使用情境。
- **播放清單一次最多處理 30 支影片**(`MAX_PLAYLIST_VIDEOS`),避免單次 workflow
  跑太久。清單更長的話,重複貼同一個網址會自動跳過已處理過的部分,分批處理。
- **YouTube 播放清單 API 有每日 10,000 額度**,對個人用量幾乎用不完,不用擔心。
- **完全沒有自動化測試(CI test)**,每次改動的驗證都靠實際點開頁面測試。
  如果之後想要更穩定,可以考慮加一支簡單的 `pytest`,對 `build_srt`、
  `extract_video_id_from_url` 這類純邏輯函式做基本測試。

---

## 7. 疑難排解速查

| 症狀 | 最可能原因 | 解法 |
|---|---|---|
| 複習頁面 404 | 沒把 viewer.html 等靜態檔案加進 repo | 檢查 online_study/ 資料夾,看自我檢查警告 |
| 改了樣式/程式碼,F5 沒變化 | 部署還沒觸發,或瀏覽器快取 | 確認 Actions 有跑完;Ctrl+Shift+R 強制重新整理 |
| 清單頁沒反映新功能(收合/封存等) | index.html 是「執行時產生」,不是純靜態 | 現在 deploy_only.yml 會自動 `--rebuild-index`,理論上不用再手動處理 |
| 影片播放器顯示「無法播放」 | 影片擁有者關閉了嵌入播放 | 正常現象,跳轉按鈕會自動改開新分頁到 YouTube |
| 播放清單抓不到影片 / 403 | YOUTUBE_API_KEY 沒設定或設定錯誤 | 檢查 Secret 是否存在、API 是否已啟用 |
| Gemini 出題失敗(429/503) | 限流或模型過載 | 已有自動重試機制,多等一下;仍失敗代表當日額度用完 |
