# -*- coding: utf-8 -*-
"""
雲端版出題腳本(給 GitHub Actions 用)
=====================================
只做「模式4:線上快速模式」那件事——不下載影片,直接把 YouTube 網址
丟給 Gemini 出題,產生一個可以邊看影片邊對照題目的網頁。

這支腳本設計成在 GitHub Actions 裡執行,所以:
  - API key 一律從環境變數 GEMINI_API_KEY 讀取(不寫死在程式碼裡)
  - 執行完會額外重建 online_study/index.html,把所有已產生的頁面列成清單,
    這樣你在手機上只要記住/收藏「一個」網址,就能看到全部歷史紀錄。
  - 會自動在 online_study/ 裡建立 .nojekyll,避免 GitHub Pages 用 Jekyll
    處理這些檔案(Jekyll 的樣板引擎 Liquid 看到 HTML/JS 裡的 {{ }} 會誤判成
    樣板語法,可能導致單一頁面建置失敗、整頁消失)。

用法(本機測試用,平常你不需要手動打這行,Actions 會自動呼叫):
    set GEMINI_API_KEY=你的key   (Windows)
    python scripts/generate_from_url.py "https://www.youtube.com/watch?v=xxxxxxxxxxx"
"""

import os
import re
import sys
import json
import glob
import time
import datetime
import html as html_lib
import urllib.request
import urllib.parse
import urllib.error

from google import genai
from google.genai import types

# ==========================================
# 基本設定
# ==========================================
API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
# 注意:這裡刻意不強制要求 API_KEY 一定要存在——
# --delete 跟 --rebuild-index 這兩個模式根本不需要呼叫 Gemini,
# 檢查挪到真正要呼叫 Gemini 的地方(generate_quiz_from_youtube_url)才做,
# 這樣「部署複習頁面(不出題)」這種不需要 Gemini 的 workflow 就不用被迫帶這把金鑰。

# YouTube Data API v3 金鑰,只有在處理「播放清單」網址時才需要用到,
# 單支影片模式不需要,所以這裡不強制要求一定要有,留到真的要用時才檢查。
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "").strip()

# 你自己的 YouTube 頻道 ID(選填)。填了之後,程式會自動判斷影片是不是
# 你自己頻道的,只有自己的影片才會自動加開逐字稿功能;不是的話就跳過,
# 不用每次手動選。去 YouTube Studio → 設定 → 頻道 → 進階設定 可以查到頻道 ID。
MY_CHANNEL_ID = os.environ.get("MY_CHANNEL_ID", "").strip()
MAX_PLAYLIST_VIDEOS = 30  # 單次workflow最多處理幾支影片,避免清單太長跑到超時

client = genai.Client(api_key=API_KEY or "missing-key-placeholder")
MODEL_NAME = "gemini-3-flash-preview"
MAX_RETRIES_ON_TRANSIENT = 3  # 429限流 / 503過載 共用的重試次數上限

ONLINE_STUDY_DIR = os.path.normpath("./online_study")
os.makedirs(ONLINE_STUDY_DIR, exist_ok=True)

DATA_DIR = os.path.join(ONLINE_STUDY_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# 關鍵修正:避免 GitHub Pages 用 Jekyll 處理這個資料夾。
# Jekyll 的 Liquid 樣板引擎會把 HTML/JS 裡的 {{ ... }} 誤判成樣板語法,
# 一旦 Gemini 產生的克漏字格式跟預期的正規表示式沒有完全對上,殘留的
# {{c1::...}} 就可能讓 Jekyll 建置該頁面失敗,導致該頁「消失」變 404。
NOJEKYLL_PATH = os.path.join(ONLINE_STUDY_DIR, ".nojekyll")
if not os.path.exists(NOJEKYLL_PATH):
    open(NOJEKYLL_PATH, "a", encoding="utf-8").close()

QUIZ_SYSTEM_PROMPT_BASE = """
你是一位資深的航空公司總檢定官與系統教官。
請詳細分析這段音訊內容,針對裡面的核心觀念與系統邏輯,製作一套考題。

【極重要】請「只」用下面的 JSON 格式輸出,不要有任何其他文字、不要用 ```json 或 ``` 包起來、不要加任何說明或註解,你的回應必須能被 json.loads() 直接解析:

{
  "subject": "科目名稱,例如:ATR 系統 - 空調系統",
  "mc_questions": [
    {
      "zh_question": "中文題目",
      "zh_options": ["(A) 中文選項A", "(B) 中文選項B", "(C) 中文選項C", "(D) 中文選項D"],
      "en_question": "English version of the question",
      "en_options": ["(A) English option A", "(B) English option B", "(C) English option C", "(D) English option D"],
      "answer": "C",
      "explanation": "詳細解析正確答案的原因,並說明系統運作邏輯(可中英夾雜,航空專有名詞盡量保留英文原文)",
      "timestamp": "14:25"
    }
  ],
  "cloze_items": [
    {
      "cloze_text": "一句值得背誦的陳述句(英文或中文皆可),把其中一個關鍵字或數據用 {{c1::關鍵字}} 標記",
      "zh_translation": "這句話的完整中文翻譯",
      "explanation": "簡短補充說明:為什麼是這個數據/規則、常見誤解、或相關的延伸觀念(1-2句話即可)",
      "tags": "簡短分類,例如 Engine / Hydraulics / Limitations / Memory-Item",
      "timestamp": "05:12"
    }
  ]__TRANSCRIPT_JSON_FIELD__
}

規則:
- mc_questions:請出 3~5 題觀念理解或故障邏輯單選題,適合「理解型」的知識點
- cloze_items:請出 4~8 個值得直接背誦的 Memory Item、限制數據(limitations)、定義或口訣,適合「記憶型」的知識點;每一則只挖一個重點(只用 {{c1::}},不要有 c2、c3)
- cloze_text 裡的挖空標記務必嚴格使用 {{c1::關鍵字}} 這個格式,前後不要加空格、不要用全形符號,只能有一組 c1
- explanation 欄位要簡潔(1-2句話),不要跟 zh_translation 重複
- timestamp 一律用 MM:SS 或 HH:MM:SS 格式的純文字,不要加中括號、不要加其他符號
- 【重要】所有題目、選項、解析、逐字稿內容,一律使用「純文字」,絕對不要使用 LaTeX 或 Markdown 數學語法(例如不要寫成 $V_1$、$V_{MCA}$、\\text{}、\\frac{}這種格式)。像 V1、VR、VMCA、V2 這類代號,直接寫成一般文字(例如「V1」或「V_MCA」都可以,但絕對不能包含 $ 符號),因為顯示的網頁不會渲染 LaTeX,帶 $ 符號會讓使用者看到一堆奇怪的原始符號,而不是正常的文字__TRANSCRIPT_RULES__
- 【重要】如果某段音訊聽不清楚、口齒不清、背景雜音干擾、或你對內容不夠確定,請直接跳過該段,不要用猜測或腦補的方式硬出題。寧可整體題數少一點,也不要出現似是而非、可能誤導使用者的錯誤內容
- 如果整支影片的音訊品質太差,導致能確實聽懂的內容不足以出滿 3 題選擇題或 4 則背誦重點,就依實際能確定的內容出題即可,不用硬湊到規定的數量上限
- 除了上述 JSON 物件之外,不要輸出任何文字
"""

# 逐字稿是「可選」附加項目,預設不開啟——原因是逐字稿要求 Gemini
# 把整支影片從頭到尾逐句轉出來,對 20 分鐘左右的影片就可能多產生
# 上百段內容,回應時間可能拉長到 5 分鐘以上。只有真的想修正自己
# 影片字幕時才需要開啟(用 --with-transcript 或 workflow 的 with_transcript 選項)。
TRANSCRIPT_JSON_FIELD = """,
  "transcript": [
    {
      "start": "00:00",
      "end": "00:04",
      "text": "這段時間範圍內實際講的內容,逐字稿形式,聽到什麼語言就寫什麼語言"
    }
  ]"""

TRANSCRIPT_RULES = """
- transcript:請把整支影片從頭到尾的逐字稿,依照實際講話的自然停頓切成一段一段(每段大約 3~8 秒,不要切太細也不要切太長),依序列出,涵蓋整支影片,不要只挑重點段落。這是要拿來取代 YouTube 現有字幕用的,所以要盡量完整、準確反映實際說出的內容,而不是摘要或改寫
- transcript 的準確度要求比出題內容更高,因為使用者會直接拿這份逐字稿取代原本的字幕:遇到 V1、VR、V2、VMCA 這類專業代號或術語時,請根據上下文判斷正確的專業寫法,不要照發音直接寫成同音的其他字詞或亂碼;如果實在無法確定某個詞是什麼,寧可保守地寫出你聽到的大概內容並標記,也不要完全省略或瞎猜替換成不相關的詞
- transcript 的每一段一樣適用「不確定就跳過」原則:如果某幾秒聽不清楚,可以把那幾秒的 text 留空字串,或用 "(聽不清楚)" 標記,不要用猜的填內容"""


def build_quiz_prompt(with_transcript=False):
    """
    用簡單字串替換組出最終 prompt(不用 str.format(),因為範本裡本身就有
    大量 JSON 範例用的大括號,跟 .format() 的佔位符語法會衝突)。
    """
    prompt = QUIZ_SYSTEM_PROMPT_BASE
    prompt = prompt.replace("__TRANSCRIPT_JSON_FIELD__", TRANSCRIPT_JSON_FIELD if with_transcript else "")
    prompt = prompt.replace("__TRANSCRIPT_RULES__", TRANSCRIPT_RULES if with_transcript else "")
    return prompt


def extract_video_id_from_url(url):
    """支援常見的 YouTube 網址格式,取出 11 碼影片 ID"""
    patterns = [
        r'(?:v=|/videos/|embed/|youtu\.be/|/v/|/e/)([a-zA-Z0-9_-]{11})',
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


def extract_playlist_id_from_url(url):
    """從網址取出播放清單 ID(list= 後面那串)"""
    m = re.search(r'[?&]list=([a-zA-Z0-9_-]+)', url)
    return m.group(1) if m else None


def get_video_channel_id(video_id):
    """查詢一支影片屬於哪個 YouTube 頻道,用於自動判斷是不是自己的影片"""
    if not YOUTUBE_API_KEY:
        return None
    params = {"part": "snippet", "id": video_id, "key": YOUTUBE_API_KEY}
    query = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"https://www.googleapis.com/youtube/v3/videos?{query}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        items = data.get("items", [])
        if items:
            return items[0].get("snippet", {}).get("channelId")
    except Exception:
        pass
    return None


def is_own_video(video_id):
    """判斷這支影片是不是 MY_CHANNEL_ID 設定的那個頻道所有"""
    if not MY_CHANNEL_ID:
        return False
    return get_video_channel_id(video_id) == MY_CHANNEL_ID


def fetch_playlist_video_ids(playlist_id):
    """
    使用官方 YouTube Data API v3 的 playlistItems.list 讀取播放清單裡的影片 ID。
    取代先前不穩定、常常 404 的 RSS 摘要做法。

    回傳 (video_ids, truncated):
      - video_ids:抓到的影片 ID 清單(最多 MAX_PLAYLIST_VIDEOS 支)
      - truncated:True 表示清單裡還有更多影片,但因為數量上限被截斷了
    """
    if not YOUTUBE_API_KEY:
        raise RuntimeError(
            "找不到環境變數 YOUTUBE_API_KEY(處理播放清單網址需要這把金鑰),"
            "請確認 GitHub Secrets 有設定好。"
        )

    video_ids = []
    page_token = None
    base_url = "https://www.googleapis.com/youtube/v3/playlistItems"

    while len(video_ids) < MAX_PLAYLIST_VIDEOS:
        params = {
            "part": "contentDetails",
            "playlistId": playlist_id,
            "maxResults": "50",
            "key": YOUTUBE_API_KEY,
        }
        if page_token:
            params["pageToken"] = page_token

        query = urllib.parse.urlencode(params)
        req = urllib.request.Request(f"{base_url}?{query}")

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            if e.code == 403:
                raise RuntimeError(
                    f"YouTube Data API 回傳 403(可能是金鑰限制設錯、API 未啟用,"
                    f"或當日 10,000 額度用完):{body[:300]}"
                )
            raise RuntimeError(f"YouTube Data API 回傳 HTTP {e.code}:{body[:300]}")

        for item in data.get("items", []):
            vid = item.get("contentDetails", {}).get("videoId")
            if vid:
                video_ids.append(vid)

        page_token = data.get("nextPageToken")
        if not page_token:
            break

    truncated = bool(page_token) and len(video_ids) >= MAX_PLAYLIST_VIDEOS
    return video_ids[:MAX_PLAYLIST_VIDEOS], truncated


def strip_json_fences(text):
    """防呆:萬一 Gemini 還是加了 ```json 包裹,先剝掉再解析"""
    text = text.strip()
    text = re.sub(r'^```json\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'^```\s*', '', text)
    text = re.sub(r'```\s*$', '', text)
    return text.strip()


def strip_stray_cloze_markup(text):
    """
    安全網:萬一 Gemini 吐出的 JSON 裡有某個欄位不小心殘留了沒被我們
    regex 吃掉的 {{ ... }} 片段(例如格式跟預期不完全一樣),這裡統一
    再過濾一次,避免任何一個 {{ }} 流入最終 HTML,觸發 Jekyll 誤判。
    只處理明顯是 cloze 標記殘留的狀況,一般文字不受影響。
    """
    return re.sub(r'\{\{c1::(.*?)\}\}', r'\1', text)


def generate_quiz_from_youtube_url(video_id, with_transcript=False):
    """直接把 YouTube 影片交給 Gemini 分析,不下載、不上傳檔案。附帶 429 / 503 重試。"""
    if not API_KEY:
        raise RuntimeError("找不到環境變數 GEMINI_API_KEY,請確認 GitHub Secrets 有設定好。")

    clean_url = f"https://www.youtube.com/watch?v={video_id}"
    prompt_template = build_quiz_prompt(with_transcript=with_transcript)
    prompt = f"{prompt_template}\n影片網址:{clean_url}。請直接分析這支 YouTube 影片後出題。"
    video_part = types.Part(file_data=types.FileData(file_uri=clean_url))

    attempt = 0
    while True:
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=[video_part, prompt],
            )
            break
        except Exception as e:
            err_str = str(e)
            is_rate_limit = "429" in err_str or "RESOURCE_EXHAUSTED" in err_str
            is_overloaded = "503" in err_str or "UNAVAILABLE" in err_str
            if (is_rate_limit or is_overloaded) and attempt < MAX_RETRIES_ON_TRANSIENT:
                wait_s = 20 * (attempt + 1)
                reason = "429 限流" if is_rate_limit else "503 模型過載"
                print(f"⚠️ 觸發 {reason},等待 {wait_s} 秒後重試(第 {attempt + 1} 次)...")
                time.sleep(wait_s)
                attempt += 1
                continue
            raise

    if not response or not response.text:
        raise RuntimeError("無法取得 Gemini 回應。")
    raw_text = strip_json_fences(response.text)
    return json.loads(raw_text)


def timestamp_to_seconds(timestamp):
    """把 MM:SS / HH:MM:SS 轉成總秒數,轉不了回傳 None"""
    if not timestamp:
        return None
    m = re.match(r'^\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\s*$', str(timestamp))
    if not m:
        return None
    hh, mm, ss = m.groups()
    hh = hh or "0"
    return int(hh) * 3600 + int(mm) * 60 + int(ss)


def seconds_to_srt_time(total_seconds):
    """把總秒數轉成 SRT 格式的時間碼:HH:MM:SS,mmm"""
    if total_seconds is None:
        total_seconds = 0
    total_seconds = max(0, int(total_seconds))
    hh = total_seconds // 3600
    mm = (total_seconds % 3600) // 60
    ss = total_seconds % 60
    return f"{hh:02d}:{mm:02d}:{ss:02d},000"


def build_srt(segments):
    """把 [{'start':'00:00','end':'00:04','text':'...'}] 轉成標準 .srt 格式的字幕檔內容"""
    lines = []
    idx = 0
    for seg in segments or []:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        start_sec = timestamp_to_seconds(seg.get("start"))
        end_sec = timestamp_to_seconds(seg.get("end"))
        if start_sec is None:
            continue
        if end_sec is None or end_sec <= start_sec:
            end_sec = start_sec + 3  # 保底給3秒長度,避免end缺漏或格式錯誤
        idx += 1
        lines.append(str(idx))
        lines.append(f"{seconds_to_srt_time(start_sec)} --> {seconds_to_srt_time(end_sec)}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines)


def write_video_data(video_id, url, quiz_data):
    """
    把 Gemini 回傳的 quiz_data 存成 data/{video_id}.json,不再產生完整 HTML。

    畫面渲染完全交給共用的 viewer.html + viewer.js + style.css 處理
    (資料/樣板分離,類似 Anki 正面/背面 Code 的概念)。以後要改按鈕、
    改樣式,只要改那三個共用檔案,所有影片頁面重新整理就會套用新樣板,
    完全不用重新出題、也不用一支一支影片重新產生。

    如果 Gemini 有回傳 transcript(逐字稿片段),額外存成同名的 .srt 檔案,
    方便你下載後去 YouTube Studio 手動替換掉原本品質不佳的字幕。
    """
    transcript_segments = quiz_data.pop("transcript", None)
    has_transcript = False
    if transcript_segments:
        srt_content = build_srt(transcript_segments)
        if srt_content.strip():
            srt_path = os.path.join(DATA_DIR, f"{video_id}.srt")
            with open(srt_path, "w", encoding="utf-8") as f:
                f.write(srt_content)
            has_transcript = True

    data = dict(quiz_data)
    data["video_id"] = video_id
    data["url"] = url or f"https://www.youtube.com/watch?v={video_id}"
    data["has_transcript"] = has_transcript

    data_path = os.path.join(DATA_DIR, f"{video_id}.json")
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data_path


MANIFEST_PATH = os.path.join(ONLINE_STUDY_DIR, "manifest.json")


def load_manifest():
    if os.path.exists(MANIFEST_PATH):
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}


def save_manifest(manifest):
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def record_manifest(video_id, subject, url):
    """把這支影片的科目名稱、原始網址、產生時間記錄進 manifest.json"""
    manifest = load_manifest()
    manifest[video_id] = {
        "subject": subject or video_id,
        "url": url or f"https://www.youtube.com/watch?v={video_id}",
        "created": datetime.datetime.utcnow().isoformat(timespec="seconds"),
    }
    save_manifest(manifest)


REQUIRED_ASSETS = ["viewer.html", "viewer.js", "style.css", "chat-config.js"]


def check_required_assets():
    """
    基本自我檢查:確認 viewer.html / viewer.js / style.css / chat-config.js
    這幾個共用靜態檔案都存在。這幾個檔案是手動加進 repo 的,不是 Python
    自動產生的,漏加會導致複習頁面 404、或某些功能(如收合、封存)沒作用
    卻沒有任何錯誤訊息——過去我們就因為漏傳這幾個檔案卡了好幾次。
    這裡只用「印出警告」不會讓 workflow 失敗,因為就算暫時缺檔案,
    出題本身還是能正常進行、資料不會遺失,只是複習頁面顯示會不完整。
    """
    missing = [name for name in REQUIRED_ASSETS if not os.path.exists(os.path.join(ONLINE_STUDY_DIR, name))]
    if missing:
        print(f"⚠️ 【自我檢查】online_study/ 資料夾裡缺少這些共用靜態檔案:{', '.join(missing)}")
        print("   複習頁面可能會 404 或部分功能(收合/封存/語言切換等)沒有作用。")
        print("   請把這些檔案加進 repo 的 online_study/ 資料夾(通常是我之前提供給你的檔案)。")
    else:
        print("✅ 【自我檢查】共用靜態檔案(viewer.html/viewer.js/style.css/chat-config.js)都存在")
    return missing


def build_index_html():
    """
    重建複習清單首頁,顯示科目名稱、原始YouTube連結、產生時間。
    連結指向共用的 viewer.html?id=xxx(不再是每支影片各自的html檔案)。

    刪除按鈕:因為 GitHub Pages 是純靜態網站,網頁本身沒有後端可以真的
    刪除檔案。這裡的做法是——按下刪除後,自動複製影片 ID 並開啟
    「刪除線上複習頁面」這個 GitHub Action 的頁面,你只要貼上 ID、
    按 Run workflow,該影片就會被移除並重新部署。不是真正一鍵刪除,
    但已經是靜態網站能做到最接近的方式。
    """
    manifest = load_manifest()
    files = glob.glob(os.path.join(DATA_DIR, "*.json"))

    entries = []
    for f in files:
        vid = os.path.splitext(os.path.basename(f))[0]
        meta = manifest.get(vid, {})
        if isinstance(meta, str):  # 相容舊版 manifest(只存時間字串)
            meta = {"subject": vid, "url": f"https://www.youtube.com/watch?v={vid}", "created": meta}
        entries.append({
            "video_id": vid,
            "subject": meta.get("subject") or vid,
            "url": meta.get("url") or f"https://www.youtube.com/watch?v={vid}",
            "created": meta.get("created") or "",
        })
    entries.sort(key=lambda e: e["created"], reverse=True)

    items_html = ""
    for e in entries:
        display_time = e["created"].replace("T", " ")[:16] if e["created"] else "(時間未知)"
        items_html += f"""
        <div class="item" data-id="{html_lib.escape(e['video_id'])}">
          <input type="checkbox" class="archive-checkbox" title="標記為已學完(暫時隱藏,不會刪除)"
                 onchange="toggleArchive('{html_lib.escape(e['video_id'])}', this.checked)">
          <a class="item-link" href="./viewer.html?id={html_lib.escape(e['video_id'])}">
            <img src="https://i.ytimg.com/vi/{e['video_id']}/mqdefault.jpg" loading="lazy">
            <div class="meta">
              <div class="subject">{html_lib.escape(e['subject'])}</div>
              <div class="date">{html_lib.escape(display_time)}</div>
              <div class="vid">ID: {html_lib.escape(e['video_id'])}</div>
            </div>
          </a>
          <div class="item-actions">
            <a class="yt-link" href="{html_lib.escape(e['url'])}" target="_blank" rel="noopener">▶ 原始影片</a>
            <button class="del-btn" onclick="deleteVideo('{html_lib.escape(e['video_id'])}')">🗑 刪除</button>
          </div>
        </div>"""

    index_html = f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>複習清單</title>
<link rel="stylesheet" href="./style.css">
</head>
<body style="padding:20px;">
<div class="list-header">
  <h1 style="margin:0;">📚 複習清單({len(entries)})</h1>
  <div class="header-actions">
    <button class="archive-toggle-btn" id="archive-toggle-btn" onclick="toggleShowArchived()">📦 顯示已封存 (0)</button>
    <button class="view-mode-btn" id="view-mode-btn" onclick="toggleViewMode()">🔲 格狀檢視</button>
    <button class="collapse-all-btn" id="collapse-toggle-btn" onclick="toggleCollapseAll()">📁 全部收合</button>
  </div>
</div>
<div id="item-list" class="item-list">
{items_html if items_html else '<p class="empty">目前還沒有產生任何複習頁面。</p>'}
</div>

<script>
var allCollapsed = false;
var ARCHIVE_KEY = 'archivedVideos';
var showArchived = false;

function loadArchivedSet() {{
  try {{
    return new Set(JSON.parse(localStorage.getItem(ARCHIVE_KEY) || '[]'));
  }} catch (e) {{
    return new Set();
  }}
}}

function saveArchivedSet(setObj) {{
  try {{ localStorage.setItem(ARCHIVE_KEY, JSON.stringify(Array.from(setObj))); }} catch (e) {{}}
}}

function updateArchiveButtonLabel(count) {{
  var btn = document.getElementById('archive-toggle-btn');
  btn.textContent = (showArchived ? '📂 隱藏已封存' : '📦 顯示已封存') + ' (' + count + ')';
}}

function applyArchivedState() {{
  var archived = loadArchivedSet();
  document.querySelectorAll('.item').forEach(function(el) {{
    var id = el.dataset.id;
    var isArchived = archived.has(id);
    el.classList.toggle('archived', isArchived);
    var cb = el.querySelector('.archive-checkbox');
    if (cb) cb.checked = isArchived;
  }});
  document.getElementById('item-list').classList.toggle('show-archived', showArchived);
  updateArchiveButtonLabel(archived.size);
}}

function toggleArchive(id, checked) {{
  var archived = loadArchivedSet();
  if (checked) {{ archived.add(id); }} else {{ archived.delete(id); }}
  saveArchivedSet(archived);
  applyArchivedState();
}}

function toggleShowArchived() {{
  showArchived = !showArchived;
  applyArchivedState();
}}

applyArchivedState();

function toggleCollapseAll() {{
  allCollapsed = !allCollapsed;
  document.querySelectorAll('.item').forEach(function(el) {{
    el.classList.toggle('collapsed', allCollapsed);
  }});
  document.getElementById('collapse-toggle-btn').textContent = allCollapsed ? '📂 全部展開' : '📁 全部收合';
}}

function toggleViewMode() {{
  var container = document.getElementById('item-list');
  var isGrid = container.classList.toggle('view-grid');
  try {{ localStorage.setItem('viewMode', isGrid ? 'grid' : 'list'); }} catch (e) {{}}
  document.getElementById('view-mode-btn').textContent = isGrid ? '📃 清單檢視' : '🔲 格狀檢視';
}}

try {{
  if (localStorage.getItem('viewMode') === 'grid') {{
    document.getElementById('item-list').classList.add('view-grid');
    document.getElementById('view-mode-btn').textContent = '📃 清單檢視';
  }}
}} catch (e) {{}}

function deleteVideo(id) {{
  var pathParts = window.location.pathname.split('/').filter(Boolean);
  var repoName = pathParts[0] || '';
  var username = window.location.hostname.split('.')[0];
  var actionsUrl = 'https://github.com/' + username + '/' + repoName + '/actions/workflows/delete_video.yml';

  var doOpen = function() {{
    alert('已複製影片 ID:' + id + '\\n\\n即將開啟「刪除線上複習頁面」的 Action 頁面,\\n貼上這個 ID 到 video_id 欄位,按 Run workflow 即可刪除。');
    window.open(actionsUrl, '_blank');
  }};

  if (navigator.clipboard && navigator.clipboard.writeText) {{
    navigator.clipboard.writeText(id).then(doOpen).catch(doOpen);
  }} else {{
    doOpen();
  }}
}}
</script>
</body>
</html>"""

    with open(os.path.join(ONLINE_STUDY_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(index_html)


def delete_video(video_id):
    """
    刪除指定 video_id 的複習資料(data/{id}.json + manifest 紀錄),並重建 index.html。
    這是原本獨立的 delete_video.py 合併進來的邏輯,現在只用 --delete 參數區分。
    """
    manifest = load_manifest()
    existed_in_manifest = video_id in manifest
    manifest.pop(video_id, None)
    save_manifest(manifest)

    data_path = os.path.join(DATA_DIR, f"{video_id}.json")
    existed_file = os.path.exists(data_path)
    if existed_file:
        os.remove(data_path)

    if not existed_in_manifest and not existed_file:
        print(f"⚠️ 找不到 video_id「{video_id}」的複習資料或 manifest 紀錄,可能已經被刪除過了。")
    else:
        print(f"🗑️ 已刪除:{video_id}(data: {'有' if existed_file else '無'}, manifest: {'有' if existed_in_manifest else '無'})")

    build_index_html()
    print("✅ 已更新 index.html 清單頁")


def process_one_video(video_id, source_url, with_transcript=False):
    """處理單一影片:分析出題、寫入JSON資料、記錄manifest。回傳是否成功。"""
    clean_url = f"https://www.youtube.com/watch?v={video_id}"

    auto_own = is_own_video(video_id)
    effective_with_transcript = with_transcript or auto_own
    if auto_own and not with_transcript:
        print(f"🔍 偵測到這是你自己頻道的影片,自動加開逐字稿功能")

    print(f"🌐 分析中:{clean_url}" + ("(含逐字稿,會比較慢)" if effective_with_transcript else ""))
    try:
        quiz_data = generate_quiz_from_youtube_url(video_id, with_transcript=effective_with_transcript)
    except Exception as e:
        print(f"❌ 這支影片處理失敗,略過繼續下一支:{type(e).__name__}: {e}")
        return False

    data_path = write_video_data(video_id, source_url, quiz_data)
    print(f"✅ 已產生:{data_path}")

    record_manifest(video_id, quiz_data.get("subject", ""), clean_url)
    return True


def main():
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("❌ 沒有收到參數。用法:\n"
              "  產生考題(單支影片):python generate_from_url.py \"https://www.youtube.com/watch?v=xxxxxxxxxxx\"\n"
              "  產生考題(播放清單):python generate_from_url.py \"https://www.youtube.com/playlist?list=xxxxxxxxxxx\"\n"
              "  刪除頁面:python generate_from_url.py --delete xxxxxxxxxxx\n"
              "  只重建清單頁(不呼叫Gemini):python generate_from_url.py --rebuild-index\n"
              "  加上 --with-transcript 或環境變數 WITH_TRANSCRIPT=1 可額外產生修正逐字稿(較慢)")
        sys.exit(1)

    # --rebuild-index 模式:只重新產生 index.html + 檢查必要的共用靜態檔案存不存在,
    # 不呼叫 Gemini、不需要 GEMINI_API_KEY。用途:每次「部署複習頁面」的 workflow
    # 執行時都會跑這個,確保 index.html 永遠反映目前腳本版本的最新邏輯,
    # 不用再靠「觸發假刪除」這種變通方式強迫重建。
    if sys.argv[1] == "--rebuild-index":
        check_required_assets()
        build_index_html()
        print("✅ 已重建 index.html 清單頁(未呼叫 Gemini)")
        return

    # --delete 模式:刪除指定影片,不需要呼叫 Gemini
    if sys.argv[1] == "--delete":
        if len(sys.argv) < 3 or not sys.argv[2].strip():
            print("❌ 沒有收到要刪除的 video_id 參數。")
            sys.exit(1)
        delete_video(sys.argv[2].strip())
        return

    # 逐字稿是可選功能(預設關閉,因為會讓每支影片明顯變慢):
    # 可以用 --with-transcript 參數,或環境變數 WITH_TRANSCRIPT=1 開啟
    args = sys.argv[1:]
    with_transcript = "--with-transcript" in args
    if with_transcript:
        args = [a for a in args if a != "--with-transcript"]
    if os.environ.get("WITH_TRANSCRIPT", "").strip().lower() in ("1", "true", "yes"):
        with_transcript = True

    if not args or not args[0].strip():
        print("❌ 沒有收到網址參數。")
        sys.exit(1)

    url = args[0].strip()
    video_id = extract_video_id_from_url(url)

    check_required_assets()  # 自我檢查:提早發現漏傳的共用靜態檔案

    # 單支影片模式
    if video_id:
        ok = process_one_video(video_id, url, with_transcript=with_transcript)
        build_index_html()
        print("✅ 已更新 index.html 清單頁")
        if not ok:
            sys.exit(1)
        return

    # 播放清單模式(網址裡有 list=,但沒有單支影片的 v=)
    playlist_id = extract_playlist_id_from_url(url)
    if playlist_id:
        print("📋 偵測到播放清單網址,正在讀取清單內容(YouTube Data API v3)...")
        try:
            video_ids, truncated = fetch_playlist_video_ids(playlist_id)
        except Exception as e:
            print(f"❌ 無法讀取播放清單內容:{type(e).__name__}: {e}")
            sys.exit(1)

        if not video_ids:
            print("⚠️ 讀不到任何影片,可能是清單為空、設為私人,或播放清單 ID 不正確。")
            sys.exit(1)

        print(f"📋 共讀到 {len(video_ids)} 支影片。")
        if truncated:
            print(f"   ⚠️ 這份清單影片數量超過單次上限({MAX_PLAYLIST_VIDEOS} 支),"
                  f"只處理前 {MAX_PLAYLIST_VIDEOS} 支,其餘的可以之後再貼一次網址繼續處理"
                  f"(已處理過的影片會自動跳過)。")

        success_count = 0
        for idx, vid in enumerate(video_ids, 1):
            print(f"---- [{idx}/{len(video_ids)}] ----")
            if process_one_video(vid, f"https://www.youtube.com/watch?v={vid}", with_transcript=with_transcript):
                success_count += 1
            time.sleep(5)  # 稍微錯開,避免瞬間對 Gemini API 打太多請求

        build_index_html()
        print(f"✅ 播放清單處理完成:{success_count}/{len(video_ids)} 支成功,已更新 index.html 清單頁")
        if success_count == 0:
            sys.exit(1)
        return

    print("❌ 無法從網址判斷出 YouTube 影片 ID 或播放清單 ID,請確認網址格式。")
    sys.exit(1)


if __name__ == "__main__":
    main()
