# -*- coding: utf-8 -*-
"""
✈️ 航空考題 → Anki 自動化流程(單一檔案版)

執行方式:
    python study_pipeline.py

開頭會出現選單,問你今天要用哪種模式:
  1. 貼 YouTube 網址 → 自動下載並出題
  2. 掃描資料夾,自動處理全部還沒處理過的影音檔
  3. 掃描資料夾,自己勾選要處理哪幾支

需要的套件:
    pip install google-genai yt-dlp
另外需要系統安裝 ffmpeg(用來轉檔 .mkv / .m4a 等格式),並加入 PATH。
"""

import os
import re
import sys
import json
import time
import glob
import shutil
import socket
import datetime
import subprocess
import html as html_lib
from datetime import date

import yt_dlp
from google import genai
from google.genai import types

sys.stdout.reconfigure(encoding='utf-8')

# ==========================================
# 0. 即時進度顯示 + 自動存檔 log
# ==========================================
LOG_FILE_PATH = "./run_log.txt"
_log_file_handle = open(LOG_FILE_PATH, "a", encoding="utf-8")

def log(msg):
    """同時印到畫面(即時看得到)並寫進 run_log.txt(自動存檔,append 模式不會覆蓋舊紀錄)"""
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line, flush=True)
    _log_file_handle.write(line + "\n")
    _log_file_handle.flush()

_run_start_time = time.time()

def elapsed_str():
    s = int(time.time() - _run_start_time)
    m, s = divmod(s, 60)
    return f"{m}分{s}秒"

# ==========================================
# 1. 基本設定
# ==========================================
API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
if not API_KEY:
    print("❌ 找不到環境變數 GEMINI_API_KEY。")
    print("   請先在 CMD 裡設定:set GEMINI_API_KEY=你的金鑰")
    print("   或是設定「系統環境變數」,這樣就不用每次開新的 CMD 視窗都重設一次。")
    print("   ⚠️ 千萬不要把金鑰直接寫進這支程式碼裡,萬一這個資料夾被同步到雲端硬碟")
    print("      或不小心 commit 進 Git,金鑰就會外洩(這正是之前發生過的狀況)。")
    sys.exit(1)
client = genai.Client(api_key=API_KEY)

INPUT_DIR = os.path.normpath("./audio_files")        # 放下載好的影音檔案
OUTPUT_DIR = os.path.normpath("./generated_quizzes") # Anki TSV 儲存位置
CONVERTED_DIR = os.path.normpath("./converted")      # 轉檔 / 檔名清理後的暫存位置
QUOTA_FILE = "./quota_state.json"  # 每日配額紀錄檔

os.makedirs(INPUT_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(CONVERTED_DIR, exist_ok=True)

MODEL_NAME = "gemini-3-flash-preview"

DAILY_QUOTA_LIMIT = 18          # 每天最多允許幾次 generate_content(實際上限 20,留 2 次緩衝)
SECONDS_BETWEEN_REQUESTS = 15   # 每次 generate_content 之間至少間隔幾秒
MAX_RETRIES_ON_429 = 2          # 單一檔案遇到 429 時的重試次數

SUPPORTED_VIDEO_EXT = {'.mp4', '.mpeg', '.mov', '.avi', '.flv', '.mpg', '.webm', '.wmv', '.3gpp'}
SUPPORTED_AUDIO_EXT = {'.wav', '.mp3', '.aiff', '.aif', '.aac', '.ogg', '.flac'}
MIME_MAP = {
    '.mp4': 'video/mp4', '.mpeg': 'video/mpeg', '.mov': 'video/mov',
    '.avi': 'video/avi', '.flv': 'video/x-flv', '.mpg': 'video/mpg',
    '.webm': 'video/webm', '.wmv': 'video/wmv', '.3gpp': 'video/3gpp',
    '.wav': 'audio/wav', '.mp3': 'audio/mp3', '.aiff': 'audio/aiff',
    '.aif': 'audio/aiff', '.aac': 'audio/aac', '.ogg': 'audio/ogg',
    '.flac': 'audio/flac',
}

MC_TSV_PATH = os.path.join(OUTPUT_DIR, "mc_import.tsv")
CLOZE_TSV_PATH = os.path.join(OUTPUT_DIR, "cloze_import.tsv")
PROCESSED_LOG_PATH = os.path.join(OUTPUT_DIR, "processed_videos.json")

# 一啟動就先確保這兩個檔案存在(就算是空的),讓你隨時能在資料夾裡看到它們在哪裡
for _p in (MC_TSV_PATH, CLOZE_TSV_PATH):
    if not os.path.exists(_p):
        open(_p, "a", encoding="utf-8").close()

# ==========================================
# 2. Gemini 出題 System Prompt(只輸出 JSON)
# ==========================================
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
- explanation 欄位要簡潔(1-2句話),不要跟 zh_translation 重複
- timestamp 一律用 MM:SS 或 HH:MM:SS 格式的純文字,不要加中括號、不要加其他符號
- 【重要】所有題目、選項、解析、逐字稿內容,一律使用「純文字」,絕對不要使用 LaTeX 或 Markdown 數學語法(例如不要寫成 $V_1$、$V_{MCA}$、\\text{}、\\frac{}這種格式)。像 V1、VR、VMCA、V2 這類代號,直接寫成一般文字(例如「V1」或「V_MCA」都可以,但絕對不能包含 $ 符號),因為顯示的網頁不會渲染 LaTeX,帶 $ 符號會讓使用者看到一堆奇怪的原始符號,而不是正常的文字__TRANSCRIPT_RULES__
- 【重要】如果某段音訊聽不清楚、口齒不清、背景雜音干擾、或你對內容不夠確定,請直接跳過該段,不要用猜測或腦補的方式硬出題。寧可整體題數少一點,也不要出現似是而非、可能誤導使用者的錯誤內容
- 如果整支影片的音訊品質太差,導致能確實聽懂的內容不足以出滿規定的題數,就依實際能確定的內容出題即可,不用硬湊到上限
- 除了上述 JSON 物件之外,不要輸出任何文字
"""

# 逐字稿是「可選」附加項目,預設不開啟——原因是逐字稿要求 Gemini
# 把整支影片從頭到尾逐句轉出來,對 20 分鐘左右的影片就可能多產生
# 上百段內容,回應時間可能拉長到 5 分鐘以上。只有真的想修正自己
# 影片字幕時才需要開啟。
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
    """用簡單字串替換組出最終 prompt,避免JSON範例的大括號跟.format()語法衝突"""
    prompt = QUIZ_SYSTEM_PROMPT_BASE
    prompt = prompt.replace("__TRANSCRIPT_JSON_FIELD__", TRANSCRIPT_JSON_FIELD if with_transcript else "")
    prompt = prompt.replace("__TRANSCRIPT_RULES__", TRANSCRIPT_RULES if with_transcript else "")
    return prompt


# ==========================================
# 3. 每日配額狀態管理
# ==========================================
def load_quota_state():
    today_str = str(date.today())
    if os.path.exists(QUOTA_FILE):
        with open(QUOTA_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        if state.get("date") != today_str:
            state = {"date": today_str, "count": 0}
    else:
        state = {"date": today_str, "count": 0}
    return state

def save_quota_state(state):
    with open(QUOTA_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)

def bump_quota(state):
    state["count"] += 1
    save_quota_state(state)

# ==========================================
# 4. Anki TSV 匯出
# ==========================================
# mc_import.tsv 欄位順序:
#   1.題號  2.中文題目  3.中文選項(<br>接)  4.英文題目  5.英文選項(<br>接)  6.正解字母  7.科目  8.HTML解析
#   ⚠️ 這個順序尚未經過你的 Note Type 截圖確認,如果匯入時「欄位對應」跟預期不符,
#      麻煩比照 cloze 的方式截圖給我,我再對齊。
#
# cloze_import.tsv 欄位順序(已依照你的 Note Type 實際欄位順序修正):
#   1.文字(cloze,含 {{c1::}})  2.背面額外內容(YouTube時間戳記連結)  3.科目  4.原始題號
#   5.PDF(留空)  6.中文對照  7.解題(補充說明)  8.Align(留空)  9.標籤
#   ⚠️ 欄位名稱是「解題」不是「解析」(先前讀圖看錯字了,已依背面模板程式碼修正)
#
# 注意:欄位數與順序如果跟 Note Type 完全一致,Anki 匯入時會自動對應好,不用再手動選。

def load_processed_set():
    if os.path.exists(PROCESSED_LOG_PATH):
        with open(PROCESSED_LOG_PATH, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def save_processed_set(processed_set):
    with open(PROCESSED_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(processed_set), f, ensure_ascii=False, indent=2)

TITLES_MAP_PATH = "./video_titles.json"

def load_titles_map():
    if os.path.exists(TITLES_MAP_PATH):
        with open(TITLES_MAP_PATH, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}

def record_video_title(dedup_key, subject):
    """記錄某個本機檔案(用dedup_key識別)對應的Gemini出題科目名稱,供上傳YouTube時當標題用"""
    titles = load_titles_map()
    titles[dedup_key] = subject
    with open(TITLES_MAP_PATH, "w", encoding="utf-8") as f:
        json.dump(titles, f, ensure_ascii=False, indent=2)

def lookup_video_title(dedup_key, fallback):
    titles = load_titles_map()
    return titles.get(dedup_key) or fallback

def sanitize_tsv_field(text):
    """TSV 每一列必須是單行:清掉 tab 與換行,避免匯入時欄位錯位或被切成兩題"""
    if text is None:
        return ""
    text = str(text)
    text = text.replace('\t', ' ').replace('\r', ' ').replace('\n', ' ')
    return text.strip()

def strip_json_fences(text):
    """防呆:萬一 Gemini 還是加了 ```json 包裹,先剝掉再解析"""
    text = text.strip()
    text = re.sub(r'^```json\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'^```\s*', '', text)
    text = re.sub(r'```\s*$', '', text)
    return text.strip()

def timestamp_to_youtube_html_link(timestamp, video_id):
    """把 MM:SS / HH:MM:SS 轉成可點擊、會跳到指定秒數的 YouTube HTML 連結"""
    if not timestamp or not video_id:
        return ""
    m = re.match(r'^\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\s*$', str(timestamp))
    if not m:
        return ""
    hh, mm, ss = m.groups()
    hh = hh or "0"
    total_seconds = int(hh) * 3600 + int(mm) * 60 + int(ss)
    label = f"{hh}:{mm}:{ss}" if int(hh) > 0 else f"{mm}:{ss}"
    url = f"https://www.youtube.com/watch?v={video_id}&t={total_seconds}s"
    return f'<a href="{url}" target="_blank" style="color:#64B5F6;">🎬 {label}</a>'

def build_mc_explanation_html(explanation, timestamp_link):
    """深色主題解析欄位:#0F172A 背景、max-width 1050px"""
    body = sanitize_tsv_field(explanation)
    ts_html = f'<br><br>{timestamp_link}' if timestamp_link else ''
    return (
        '<div style="background-color:#0F172A;color:#e2e8f0;padding:14px 18px;'
        'border-radius:8px;max-width:1050px;line-height:1.6;font-size:15px;">'
        f'{body}{ts_html}</div>'
    )

def append_tsv_row(path, columns):
    line = "\t".join(sanitize_tsv_field(c) for c in columns)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def write_quiz_to_anki_tsv(quiz_data, base_id, video_id, subject_fallback):
    """把 Gemini 回傳的 JSON 轉成兩份 Anki TSV(mc_import.tsv / cloze_import.tsv)"""
    subject = quiz_data.get("subject") or subject_fallback
    mc_count = 0
    cz_count = 0

    for idx, q in enumerate(quiz_data.get("mc_questions", []), 1):
        qid = f"{base_id}-MC{idx:02d}"
        zh_options = "<br>".join(q.get("zh_options", []))
        en_options = "<br>".join(q.get("en_options", []))
        ts_link = timestamp_to_youtube_html_link(q.get("timestamp"), video_id)
        explanation_html = build_mc_explanation_html(q.get("explanation", ""), ts_link)
        append_tsv_row(MC_TSV_PATH, [
            qid,
            q.get("zh_question", ""),
            zh_options,
            q.get("en_question", ""),
            en_options,
            q.get("answer", ""),
            subject,
            explanation_html,
        ])
        mc_count += 1

    for idx, c in enumerate(quiz_data.get("cloze_items", []), 1):
        qid = f"{base_id}-CZ{idx:02d}"
        ts_link = timestamp_to_youtube_html_link(c.get("timestamp"), video_id)
        append_tsv_row(CLOZE_TSV_PATH, [
            c.get("cloze_text", ""),      # 1. 文字
            ts_link,                       # 2. 背面額外內容(時間戳記連結)
            subject,                       # 3. 科目
            qid,                           # 4. 原始題號
            "",                            # 5. PDF(影片來源沒有對應 PDF,留空)
            c.get("zh_translation", ""),   # 6. 中文對照
            c.get("explanation", ""),      # 7. 解題(補充說明)
            "",                            # 8. Align(留空)
            c.get("tags", ""),             # 9. 標籤
        ])
        cz_count += 1

    return mc_count, cz_count

# ==========================================
# 5. 檔名 / 編碼安全處理(修正 UnicodeEncodeError 的關鍵)
# ==========================================
def extract_youtube_id(filename):
    """從檔名自動提取 YouTube 11 位數的 Video ID"""
    match = re.search(r'\[([a-zA-Z0-9_-]{11})\]', filename)
    return match.group(1) if match else None

def sanitize_filename(filename):
    """替換檔名中的全形符號與非 ASCII 字元,避免 SDK 內部編碼錯誤(用於顯示名稱)"""
    replacements = {
        '｜': '_', '：': '_', '？': '_', '!': '_',
        '(': '_', ')': '_', '—': '_', '…': '_'
    }
    for char, repl in replacements.items():
        filename = filename.replace(char, repl)
    clean_name = re.sub(r'[^\x00-\x7F]+', '_', filename)
    return clean_name

def ensure_ascii_safe_upload_path(file_path):
    """
    關鍵修正:Gemini SDK 在組 HTTP 上傳請求時,某些環節會用系統預設(ascii)編碼
    去處理實際的檔案路徑/檔名,只要檔名含中文、全形符號或 emoji 就會噴
    UnicodeEncodeError。解法是上傳前把檔案複製成一份「純英數字」檔名的副本,
    真正拿去上傳的是這份安全複本,原始檔案完全不動。
    """
    ext = os.path.splitext(file_path)[1]
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    safe_base = sanitize_filename(base_name)
    safe_base = re.sub(r'[^A-Za-z0-9_\-]', '_', safe_base)
    safe_base = re.sub(r'_+', '_', safe_base).strip('_')
    if not safe_base:
        safe_base = "video"
    # 檔名過長也一併裁切,避免路徑過長的問題
    safe_base = safe_base[:80]

    target_path = os.path.join(CONVERTED_DIR, f"{safe_base}{ext}")

    if os.path.abspath(target_path) == os.path.abspath(file_path):
        return file_path

    if not os.path.exists(target_path):
        log(f"  🔤 檔名含特殊字元,建立安全複本供上傳:{os.path.basename(target_path)}")
        shutil.copy2(file_path, target_path)

    return target_path

def is_transient_error(e):
    """判斷是否為可重試的暫時性錯誤:429 限流,或網路瞬斷"""
    err_str = str(e)
    if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
        return "rate_limit"
    transient_keywords = [
        "10054", "ConnectionReset", "ReadError", "RemoteDisconnected",
        "ConnectionError", "Timeout", "ECONNRESET", "BrokenPipeError",
    ]
    if any(kw in err_str or kw in type(e).__name__ for kw in transient_keywords):
        return "network"
    return None

def ensure_ffmpeg_available():
    if shutil.which("ffmpeg") is None:
        log("⚠️ 找不到 ffmpeg,無法自動轉檔 .mkv / .m4a 等不支援格式。")
        log("   請先安裝 ffmpeg 並加入系統 PATH:https://www.gyan.dev/ffmpeg/builds/")
        return False
    return True

def convert_to_supported_format(file_path):
    """
    若檔案格式不在 Gemini 支援清單內(例如 .mkv, .m4a),
    用 ffmpeg 轉成 mp4(有影像)或 aac(純音訊),輸出到 CONVERTED_DIR。
    回傳新的可用檔案路徑;若本來就支援,直接回傳原路徑。
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext in SUPPORTED_VIDEO_EXT or ext in SUPPORTED_AUDIO_EXT:
        return file_path

    base_name = os.path.splitext(os.path.basename(file_path))[0]
    safe_base_name = sanitize_filename(base_name)
    safe_base_name = re.sub(r'[^A-Za-z0-9_\-]', '_', safe_base_name) or "video"

    if not ensure_ffmpeg_available():
        return None

    audio_only_ext = {'.m4a', '.wma', '.opus'}
    if ext in audio_only_ext:
        target_path = os.path.join(CONVERTED_DIR, f"{safe_base_name}.aac")
        cmd = ["ffmpeg", "-y", "-i", file_path, "-vn", "-c:a", "aac", target_path]
    else:
        target_path = os.path.join(CONVERTED_DIR, f"{safe_base_name}.mp4")
        cmd = ["ffmpeg", "-y", "-i", file_path, "-c:v", "libx264", "-c:a", "aac", target_path]

    if os.path.exists(target_path):
        log(f"  ♻️ 已存在轉檔結果,重複使用:{target_path}")
        return target_path

    log(f"  🔄 偵測到不支援格式 ({ext}),正在用 ffmpeg 轉檔...")
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        log(f"  ✅ 轉檔完成:{target_path}")
        return target_path
    except subprocess.CalledProcessError as e:
        log(f"  ❌ ffmpeg 轉檔失敗:{e.stderr.decode(errors='ignore')[:500]}")
        return None

# ==========================================
# 6. 單一影音檔處理流程
# ==========================================
def process_single_audio(file_path, quota_state, force=False, video_id_override=None):
    original_filename = os.path.basename(file_path)
    safe_base_name = re.sub(r'[\\/*?:"<>|]', "", os.path.splitext(original_filename)[0])
    video_id = video_id_override or extract_youtube_id(original_filename)

    processed_set = load_processed_set()
    dedup_key = video_id or safe_base_name

    if dedup_key in processed_set and not force:
        log(f"⏩ 檔名【{original_filename}】已匯出過 Anki TSV,自動跳過。(如需強制重新產生,請用手動選擇模式並選擇強制重跑)")
        return "SKIPPED"

    if quota_state["count"] >= DAILY_QUOTA_LIMIT:
        log(f"🛑 今日配額已達上限({quota_state['count']}/{DAILY_QUOTA_LIMIT}),停止本次批次,明天再繼續。")
        return "QUOTA_EXCEEDED"

    # 格式轉換(mkv / m4a 等不支援格式)
    usable_path = convert_to_supported_format(file_path)
    if usable_path is None:
        log(f"  ⏭️ 檔案【{original_filename}】無法轉換為支援格式,跳過。")
        return "CONVERT_FAILED"

    # 檔名編碼安全處理(修正中文/全形符號導致的 UnicodeEncodeError)
    usable_path = ensure_ascii_safe_upload_path(usable_path)

    ext = os.path.splitext(usable_path)[1].lower()
    mime_type = MIME_MAP.get(ext)

    if video_id:
        log(f"\n🎧 開始處理檔案:{original_filename} (偵測到 YouTube ID: {video_id})")
    else:
        log(f"\n🎧 開始處理檔案:{original_filename} (未在檔名偵測到 [VideoID],時間戳記將不會變成連結)")

    uploaded_file = None

    try:
        safe_display_name = sanitize_filename(original_filename)
        upload_config = types.UploadFileConfig(
            display_name=safe_display_name,
            mime_type=mime_type,
        )

        upload_attempt = 0
        while True:
            try:
                log("  📤 上傳影音至 Gemini API...")
                uploaded_file = client.files.upload(file=usable_path, config=upload_config)
                break
            except Exception as e:
                if is_transient_error(e) == "network" and upload_attempt < MAX_RETRIES_ON_429:
                    wait_s = 20 * (upload_attempt + 1)
                    log(f"  ⏳ 上傳時網路瞬斷,等待 {wait_s} 秒後重試(第 {upload_attempt + 1} 次)...")
                    time.sleep(wait_s)
                    upload_attempt += 1
                    continue
                else:
                    raise

        poll_count = 0
        while uploaded_file.state.name == "PROCESSING":
            poll_count += 1
            if poll_count == 1 or poll_count % 6 == 0:
                log(f"  ⏳ 伺服器影音處理中,已等待約 {poll_count * 5} 秒...")
            time.sleep(5)
            uploaded_file = client.files.get(name=uploaded_file.name)

        if uploaded_file.state.name == "FAILED":
            raise ValueError("Gemini 處理該影音檔案失敗(檔案本身可能仍不受支援)。")

        log("  🤖 正在生成考題(MC + Cloze JSON)...")
        prompt = f"{QUIZ_SYSTEM_PROMPT}\n檔名為:{original_filename}。請閱讀並聆聽影音內容後出題。"

        attempt = 0
        while True:
            try:
                response = client.models.generate_content(
                    model=MODEL_NAME,
                    contents=[uploaded_file, prompt]
                )
                bump_quota(quota_state)
                break
            except Exception as e:
                error_type = is_transient_error(e)
                if error_type == "rate_limit" and attempt < MAX_RETRIES_ON_429:
                    wait_s = 30 * (attempt + 1)
                    log(f"  ⏳ 觸發 429 限流,等待 {wait_s} 秒後重試(第 {attempt + 1} 次)...")
                    time.sleep(wait_s)
                    attempt += 1
                    continue
                elif error_type == "rate_limit":
                    log("  🛑 多次重試仍被限流,判定今日配額已耗盡,停止批次。")
                    quota_state["count"] = DAILY_QUOTA_LIMIT
                    save_quota_state(quota_state)
                    return "QUOTA_EXCEEDED"
                elif error_type == "network" and attempt < MAX_RETRIES_ON_429:
                    wait_s = 20 * (attempt + 1)
                    log(f"  ⏳ 生成時網路瞬斷,等待 {wait_s} 秒後重試(第 {attempt + 1} 次)...")
                    time.sleep(wait_s)
                    attempt += 1
                    continue
                else:
                    raise

        if not response or not response.text:
            raise RuntimeError("無法成功取得 API 回應。")

        raw_text = strip_json_fences(response.text)
        try:
            quiz_data = json.loads(raw_text)
        except json.JSONDecodeError as e:
            fail_dump_path = os.path.join(OUTPUT_DIR, f"FAILED_JSON_{safe_base_name}.txt")
            with open(fail_dump_path, "w", encoding="utf-8") as f:
                f.write(response.text)
            raise RuntimeError(f"Gemini 回傳的內容不是合法 JSON,原始內容已存到 {fail_dump_path}(錯誤:{e})")

        base_id = video_id or sanitize_filename(safe_base_name)
        mc_count, cz_count = write_quiz_to_anki_tsv(
            quiz_data, base_id, video_id, subject_fallback=safe_base_name
        )
        record_video_title(dedup_key, quiz_data.get("subject", "") or safe_base_name)

        processed_set.add(dedup_key)
        save_processed_set(processed_set)

        log(f"  🎉 已寫入 Anki TSV:{mc_count} 題選擇題 → {MC_TSV_PATH}")
        log(f"  🎉 已寫入 Anki TSV:{cz_count} 則克漏字 → {CLOZE_TSV_PATH}")
        return "SUCCESS"

    except Exception as e:
        log(f"  ❌ 處理檔案【{original_filename}】時發生錯誤: {type(e).__name__}: {e}")
        return "ERROR"

    finally:
        if uploaded_file:
            try:
                client.files.delete(name=uploaded_file.name)
                log("  🧹 已清理 Gemini 雲端暫存檔。")
            except Exception:
                pass

# ==========================================
# 7. 資料夾掃描 + 批次處理
# ==========================================
AUDIO_GLOB_PATTERNS = [
    '*.mp3', '*.m4a', '*.wav', '*.aac', '*.ogg', '*.flac', '*.aiff',
    '*.mkv', '*.webm', '*.mp4', '*.mov', '*.avi', '*.wmv', '*.flv',
    '*.MKV', '*.WEBM', '*.MP4', '*.MOV', '*.AVI', '*.WMV', '*.FLV', '*.M4A',
]

def discover_audio_files():
    """掃描目前目錄與 INPUT_DIR,回傳排序後的影音檔案清單(不含重複)"""
    audio_files = []
    search_paths = [".", INPUT_DIR]
    for sp in search_paths:
        for ext in AUDIO_GLOB_PATTERNS:
            audio_files.extend(glob.glob(os.path.join(sp, ext)))
    return sorted(set(audio_files))

def find_files_missing_video_id(file_paths):
    """回傳檔名裡沒有偵測到 11 碼 YouTube ID 的檔案清單"""
    return [f for f in file_paths if extract_youtube_id(os.path.basename(f)) is None]

def prompt_for_missing_video_ids(file_paths):
    """
    ID 檢查機制:對於檔名裡沒有 YouTube ID 的檔案,主動詢問是否要手動補上,
    避免像之前那樣「不知不覺」就少了時間戳記連結。
    回傳 {file_path: video_id} 的字典,只包含使用者有手動補上的部分。
    """
    missing = find_files_missing_video_id(file_paths)
    if not missing:
        return {}

    print(f"\n⚠️ 以下 {len(missing)} 個檔案的檔名裡沒有偵測到 YouTube 11 碼 ID,時間戳記連結不會產生:")
    for f in missing:
        print(f"  - {f}")

    choice = input("\n要現在手動補上正確的 YouTube ID 嗎?(y = 逐一輸入 / n = 跳過,不加連結):").strip().lower()
    overrides = {}
    if choice != "y":
        return overrides

    for f in missing:
        vid = input(f"「{os.path.basename(f)}」的 YouTube 影片 ID(11碼,直接 Enter 跳過這個檔案):").strip()
        if not vid:
            continue
        if re.match(r'^[a-zA-Z0-9_-]{11}$', vid):
            overrides[f] = vid
        else:
            print("  ⚠️ 看起來不是合法的 11 碼 ID,已略過,這個檔案將不會有時間戳記連結。")
    return overrides

def run_batch(audio_files, quota_state=None, force=False, video_id_overrides=None):
    """對一批影音檔案依序處理,附帶配額檢查、ETA 估算、請求間隔控制"""
    if not audio_files:
        log("⚠️ 沒有要處理的影音檔案。")
        return

    video_id_overrides = video_id_overrides or {}

    if quota_state is None:
        quota_state = load_quota_state()

    log(f"🚀 共 {len(audio_files)} 個影音檔案,準備開始批次生成 Anki 卡片...")
    log(f"📊 今日已使用配額:{quota_state['count']} / {DAILY_QUOTA_LIMIT}\n")

    total_files = len(audio_files)
    per_file_durations = []

    for idx, file_path in enumerate(audio_files, 1):
        file_start_time = time.time()

        if per_file_durations:
            avg_duration = sum(per_file_durations) / len(per_file_durations)
            remaining = total_files - idx + 1
            est_seconds = int(avg_duration * remaining)
            est_m, est_s = divmod(est_seconds, 60)
            eta_str = f",預估剩餘約 {est_m}分{est_s}秒"
        else:
            eta_str = "(第一個檔案,尚無法估算剩餘時間)"

        log(f"----------------------------------------")
        log(f"進度:[{idx}/{total_files}]{eta_str}｜總耗時 {elapsed_str()}")
        result = process_single_audio(
            file_path, quota_state, force=force,
            video_id_override=video_id_overrides.get(file_path)
        )

        file_duration = time.time() - file_start_time
        per_file_durations.append(file_duration)
        log(f"⏱️ 此檔案耗時 {int(file_duration)} 秒")

        if result == "QUOTA_EXCEEDED":
            break

        if result == "SUCCESS":
            time.sleep(SECONDS_BETWEEN_REQUESTS)
        else:
            time.sleep(3)

    log(f"\n📊 今日已使用配額:{quota_state['count']} / {DAILY_QUOTA_LIMIT}")
    log(f"🕒 本次批次總耗時:{elapsed_str()}")
    log(f"\n✨ 本次批次處理完成!")
    log(f"   選擇題 TSV:{os.path.abspath(MC_TSV_PATH)}")
    log(f"   克漏字 TSV:{os.path.abspath(CLOZE_TSV_PATH)}")
    log("   (若因配額用盡中途停止,下次重新執行同一批檔案即可自動從未完成的部分繼續。)")

# ==========================================
# 8. YouTube 下載(yt-dlp)
# ==========================================
URL_LIST_FILE = "./video_urls.txt"
DOWNLOAD_ARCHIVE_FILE = "./download_archive.txt"
COOKIES_FROM_BROWSER = 'chrome'  # 可改成 'edge' 或 'firefox'

def load_urls():
    if not os.path.exists(URL_LIST_FILE):
        with open(URL_LIST_FILE, "w", encoding="utf-8") as f:
            f.write("# 把 YouTube 網址貼在這裡,一行一個\n# 開頭是 # 的行會被忽略\n# 播放清單網址也可以直接貼,會自動展開成個別影片\n")
        log(f"⚠️ 找不到 {URL_LIST_FILE},已幫你建立一份空白範本,請填入網址後重新執行。")
        return []

    urls = []
    with open(URL_LIST_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    return urls

def expand_playlist_if_needed(url):
    """如果網址是播放清單,展開成裡面每一支影片各自的網址;否則原樣回傳。"""
    if "list=" in url and "watch?v=" not in url:
        log("  📋 偵測到播放清單網址,正在展開為個別影片清單...")
        ydl_opts = {
            'extract_flat': True,
            'skip_download': True,
            'quiet': True,
            'no_warnings': True,
            'cookiesfrombrowser': (COOKIES_FROM_BROWSER,),
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                entries = info.get('entries', []) if info else []
                video_urls = [
                    f"https://www.youtube.com/watch?v={e['id']}"
                    for e in entries if e and e.get('id')
                ]
                log(f"  📋 展開完成,共 {len(video_urls)} 支影片。")
                return video_urls
        except Exception as e:
            log(f"  ❌ 展開播放清單失敗:{type(e).__name__}: {e}")
            return []
    return [url]

def expand_all(urls):
    all_video_urls = []
    for u in urls:
        all_video_urls.extend(expand_playlist_if_needed(u))
    return all_video_urls

def download_video(url):
    """下載單一影片,回傳下載完成後的路徑;失敗回傳 None;已下載過回傳 'ALREADY_DOWNLOADED'"""
    outtmpl = os.path.join(INPUT_DIR, "%(title)s [%(id)s].%(ext)s")

    def progress_hook(d):
        if d['status'] == 'finished':
            log("  ⬇️ 下載串流完成,準備合併/後製...")

    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/bestvideo+bestaudio/best',
        'merge_output_format': 'mp4',
        'outtmpl': outtmpl,
        'download_archive': DOWNLOAD_ARCHIVE_FILE,
        'progress_hooks': [progress_hook],
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': True,
        'noplaylist': True,
        'cookiesfrombrowser': (COOKIES_FROM_BROWSER,),
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                log("  ⏩ 這支影片先前已下載過,略過下載。")
                return "ALREADY_DOWNLOADED"
            if 'requested_downloads' in info and info['requested_downloads']:
                return info['requested_downloads'][0]['filepath']
            return ydl.prepare_filename(info)
    except Exception as e:
        log(f"  ❌ 下載失敗:{type(e).__name__}: {e}")
        return None

def run_youtube_batch(urls):
    """對一批 YouTube 網址依序:下載 → 立刻出題"""
    if not urls:
        log("⚠️ 沒有任何網址可以處理。")
        return

    quota_state = load_quota_state()
    log(f"🚀 共 {len(urls)} 個網址,準備開始「下載 → 出題」全自動流程...")
    log(f"📊 今日已使用配額:{quota_state['count']} / {DAILY_QUOTA_LIMIT}\n")

    for idx, url in enumerate(urls, 1):
        log(f"----------------------------------------")
        log(f"進度:[{idx}/{len(urls)}]｜總耗時 {elapsed_str()}")
        log(f"🔗 網址:{url}")

        if quota_state["count"] >= DAILY_QUOTA_LIMIT:
            log("🛑 今日配額已達上限,停止本次流程,明天再繼續。")
            break

        log("  📥 開始下載...")
        result = download_video(url)

        if result is None:
            log("  ⏭️ 下載失敗,跳過這支影片,繼續下一支。")
            continue
        if result == "ALREADY_DOWNLOADED":
            continue

        log(f"  ✅ 下載完成:{result}")
        quiz_result = process_single_audio(result, quota_state)

        if quiz_result == "QUOTA_EXCEEDED":
            break
        time.sleep(3)

    log(f"\n📊 今日已使用配額:{quota_state['count']} / {DAILY_QUOTA_LIMIT}")
    log(f"🕒 本次流程總耗時:{elapsed_str()}")
    log("\n✨ 全自動流程處理完成!")
    log(f"   選擇題 TSV:{os.path.abspath(MC_TSV_PATH)}")
    log(f"   克漏字 TSV:{os.path.abspath(CLOZE_TSV_PATH)}")

# ==========================================
# 8b. 線上快速模式(不下載,直接分析 YouTube 網址)
# ==========================================
# 原理:Gemini API 支援直接把「公開的 YouTube 網址」丟進 contents 裡分析,
# 不需要下載、也不需要上傳檔案,伺服器端會自己去抓影片內容。
# ⚠️ 這是相對新的功能,實測時如果遇到 API 錯誤,可能是帳號/地區/影片長度限制,
#    屆時我們再依實際錯誤訊息調整。
ONLINE_STUDY_DIR = os.path.normpath("./online_study")
os.makedirs(ONLINE_STUDY_DIR, exist_ok=True)

DATA_DIR = os.path.join(ONLINE_STUDY_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# 你自己的 YouTube 頻道 ID(選填)。填了之後,程式會自動判斷影片是不是
# 你自己頻道的,只有自己的影片才會自動問你要不要開逐字稿功能。
# 去 YouTube Studio → 設定 → 頻道 → 進階設定 可以查到頻道 ID。
MY_CHANNEL_ID = ""

def get_video_channel_id(video_id):
    """
    用 yt-dlp 查詢一支影片屬於哪個 YouTube 頻道(只抓中繼資料,不下載影片本身)。
    本機版不需要額外的 YouTube Data API 金鑰,因為 yt-dlp 已經裝好了。
    """
    try:
        ydl_opts = {'quiet': True, 'no_warnings': True, 'skip_download': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
            return info.get('channel_id') or info.get('uploader_id')
    except Exception:
        return None

def is_own_video(video_id):
    """判斷這支影片是不是 MY_CHANNEL_ID 設定的那個頻道所有"""
    if not MY_CHANNEL_ID:
        return False
    return get_video_channel_id(video_id) == MY_CHANNEL_ID

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

def generate_quiz_from_youtube_url(video_id, with_transcript=False):
    """
    直接把 YouTube 影片交給 Gemini 分析,不下載、不上傳檔案。
    這裡刻意只用乾淨的 https://www.youtube.com/watch?v=ID 格式,
    不帶播放清單(list=)、index= 等參數 —— 帶了額外參數 Gemini 會認不出
    這是一支 YouTube 影片,反而把它當成一般網頁抓,導致 400 錯誤。
    """
    clean_url = f"https://www.youtube.com/watch?v={video_id}"
    prompt_template = build_quiz_prompt(with_transcript=with_transcript)
    prompt = f"{prompt_template}\n影片網址:{clean_url}。請直接分析這支 YouTube 影片後出題。"
    video_part = types.Part(file_data=types.FileData(file_uri=clean_url))
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=[video_part, prompt],
    )
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

FIXED_LOCAL_SERVER_PORT = 8899  # 固定連接埠,方便你把網址加入書籤重複使用

def find_free_port():
    """找一個目前沒有被佔用的本機連接埠(當固定 port 被別的程式佔用時的備案)"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]

def is_port_serving_our_folder(port):
    """檢查這個 port 是不是有東西在監聽(用來判斷伺服器是否還活著)"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False

def ensure_local_http_server():
    """
    啟動(或重複使用)一個本機 HTTP 伺服器來服務 online_study 資料夾。

    關鍵原因:YouTube 的嵌入播放器不接受用 file:// 直接開啟的頁面
    (會顯示「錯誤 153,影片播放器設定錯誤」),因為 file:// 沒有合法的來源網域。
    改用 http://localhost:PORT 開啟就能正常運作。

    優先使用固定的 8899 port,這樣網址每次都一樣,可以直接加入瀏覽器書籤
    反覆練習;只有當 8899 被別的程式佔用時,才會退回隨機挑一個 port。

    用「獨立子行程」啟動伺服器,即使關掉 study_pipeline.py,伺服器依然會
    繼續在背景執行;下次要重看之前產生的頁面,只要伺服器還活著就不用重新產生。
    """
    port_file = os.path.join(ONLINE_STUDY_DIR, ".server_port")

    if os.path.exists(port_file):
        with open(port_file, "r", encoding="utf-8") as f:
            try:
                existing_port = int(f.read().strip())
            except ValueError:
                existing_port = None
        if existing_port and is_port_serving_our_folder(existing_port):
            return existing_port  # 伺服器還活著,直接重複使用

    if not is_port_serving_our_folder(FIXED_LOCAL_SERVER_PORT):
        port = FIXED_LOCAL_SERVER_PORT
    else:
        port = find_free_port()

    with open(port_file, "w", encoding="utf-8") as f:
        f.write(str(port))

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "--serve-online-study", str(port)],
        creationflags=creationflags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1)  # 給伺服器一點時間啟動起來
    return port

def run_online_study_server(port):
    """
    自訂的本機小型伺服器(取代單純的 http.server),多支援一個刪除 API:
    POST /api/delete?id=VIDEOID → 刪除該影片的複習資料(data/{id}.json)與 manifest 紀錄。
    """
    import http.server
    import functools
    from urllib.parse import urlparse, parse_qs

    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_POST(self):
            parsed = urlparse(self.path)
            if parsed.path == "/api/delete":
                params = parse_qs(parsed.query)
                video_id = (params.get("id") or [""])[0].strip()
                ok = bool(video_id) and delete_online_study_entry(video_id)
                self.send_response(200 if ok else 404)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"success": ok}).encode("utf-8"))
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            pass  # 不用把每個靜態檔案請求都印到主控台,太洗版

    HandlerWithDir = functools.partial(Handler, directory=ONLINE_STUDY_DIR)
    server = http.server.ThreadingHTTPServer(("", port), HandlerWithDir)
    server.serve_forever()

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
    畫面渲染交給共用的 viewer.html + viewer.js + style.css 處理(跟雲端版
    共用同一套資產,資料/樣板分離,類似 Anki 正面/背面 Code 的概念)。

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


def record_manifest_entry(video_id, subject, url):
    """記錄這支影片的科目名稱、原始網址、產生時間,供複習清單顯示與排序用"""
    manifest = load_manifest()
    manifest[video_id] = {
        "subject": subject or video_id,
        "url": url or f"https://www.youtube.com/watch?v={video_id}",
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    save_manifest(manifest)

def delete_online_study_entry(video_id):
    """刪除單一線上複習資料:刪data/json、清manifest紀錄、重建index.html。回傳是否成功刪除。"""
    manifest = load_manifest()
    existed = video_id in manifest
    manifest.pop(video_id, None)
    save_manifest(manifest)
    data_path = os.path.join(DATA_DIR, f"{video_id}.json")
    if os.path.exists(data_path):
        os.remove(data_path)
        existed = True
    build_index_html()
    return existed

def esc_html(text):
    return html_lib.escape(str(text or ""), quote=True)

REQUIRED_ASSETS = ["viewer.html", "viewer.js", "style.css", "chat-config.js", "edit-mode.js"]

def check_required_assets():
    """
    基本自我檢查:確認 viewer.html / viewer.js / style.css / chat-config.js
    這幾個共用靜態檔案都存在。這幾個檔案是手動放進 online_study 資料夾的,
    不是 Python 自動產生的,漏放會導致複習頁面打不開、或某些功能沒作用
    卻沒有任何錯誤訊息。
    """
    missing = [name for name in REQUIRED_ASSETS if not os.path.exists(os.path.join(ONLINE_STUDY_DIR, name))]
    if missing:
        log(f"⚠️ 【自我檢查】online_study/ 資料夾裡缺少這些共用靜態檔案:{', '.join(missing)}")
        log("   複習頁面可能打不開、或部分功能(收合/封存/語言切換等)沒有作用。")
    else:
        log("✅ 【自我檢查】共用靜態檔案(viewer.html/viewer.js/style.css/chat-config.js)都存在")
    return missing

def build_index_html():
    """
    重建複習清單首頁,顯示科目名稱、原始YouTube連結、產生時間,
    並附上刪除按鈕(本機版有小型伺服器可以真的處理刪除動作)。
    連結指向共用的 viewer.html?id=xxx。
    """
    manifest = load_manifest()
    files = glob.glob(os.path.join(DATA_DIR, "*.json"))

    entries = []
    for f in files:
        vid = os.path.splitext(os.path.basename(f))[0]
        meta = manifest.get(vid, {})
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
        <div class="item" data-id="{esc_html(e['video_id'])}">
          <input type="checkbox" class="archive-checkbox" title="標記為已學完(暫時隱藏,不會刪除)"
                 onchange="toggleArchive('{esc_html(e['video_id'])}', this.checked)">
          <a class="item-link" href="./viewer.html?id={esc_html(e['video_id'])}">
            <img src="https://i.ytimg.com/vi/{e['video_id']}/mqdefault.jpg" loading="lazy">
            <div class="meta">
              <div class="subject" data-editkey="idx::{esc_html(e['video_id'])}::subject">{esc_html(e['subject'])}</div>
              <div class="date" data-editkey="idx::{esc_html(e['video_id'])}::date">{esc_html(display_time)}</div>
              <div class="vid">ID: {esc_html(e['video_id'])}</div>
            </div>
          </a>
          <div class="item-actions">
            <a class="yt-link" href="{esc_html(e['url'])}" target="_blank" rel="noopener">▶ 原始影片</a>
            <button class="del-btn" onclick="deleteVideo('{esc_html(e['video_id'])}')">🗑 刪除</button>
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
    <button class="edit-reset-btn" onclick="clearTextEdits()" title="還原這頁所有手動修改過的文字">↺ 還原文字修改</button>
  </div>
</div>
<div class="edit-hint" style="margin:4px 0 12px;">💡 提示:雙擊科目名稱或日期文字可以直接修改內容。</div>
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

async function deleteVideo(id) {{
  if (!confirm('確定要刪除「' + id + '」這支影片的複習頁面嗎?')) return;
  try {{
    const res = await fetch('/api/delete?id=' + encodeURIComponent(id), {{ method: 'POST' }});
    if (res.ok) {{
      location.reload();
    }} else {{
      alert('刪除失敗,伺服器回應錯誤。');
    }}
  }} catch (e) {{
    alert('刪除失敗:' + e);
  }}
}}
</script>
<script src="./edit-mode.js"></script>
</body>
</html>"""

    with open(os.path.join(ONLINE_STUDY_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(index_html)

def run_online_quick_mode():
    print("\n這個模式不會下載影片,直接分析線上的 YouTube 網址,產生一個網頁讓你邊看影片邊對照題目。")
    url = input("請貼上要分析的 YouTube 網址:").strip()
    if not url:
        log("⚠️ 沒有輸入網址,回到主選單。")
        return

    video_id = extract_video_id_from_url(url)
    if not video_id:
        log("⚠️ 無法從網址判斷出 YouTube 影片 ID,請確認網址格式(例如 https://www.youtube.com/watch?v=...)。")
        return

    quota_state = load_quota_state()
    if quota_state["count"] >= DAILY_QUOTA_LIMIT:
        log(f"🛑 今日配額已達上限({quota_state['count']}/{DAILY_QUOTA_LIMIT}),明天再試。")
        return

    auto_own = is_own_video(video_id)
    if MY_CHANNEL_ID:
        hint = "偵測結果:像是你自己的影片,建議開" if auto_own else "偵測結果:不是你設定的頻道,建議不用開"
    else:
        hint = "尚未設定 MY_CHANNEL_ID,無法自動偵測"
    default_answer = "y" if auto_own else "n"
    transcript_choice = input(
        f"要順便產生修正逐字稿(.srt)嗎?會明顯變慢(長影片可能多花好幾分鐘),"
        f"只有想修正自己影片字幕時才需要。{hint}"
        f"(y = 要 / n = 不要 / 直接 Enter = 採用偵測結果[{default_answer}]):"
    ).strip().lower()
    with_transcript = (transcript_choice == "y") if transcript_choice else auto_own

    log(f"🌐 直接分析線上影片(不下載):https://www.youtube.com/watch?v={video_id}" +
        ("(含逐字稿,會比較慢)" if with_transcript else ""))
    try:
        quiz_data = generate_quiz_from_youtube_url(video_id, with_transcript=with_transcript)
        bump_quota(quota_state)
    except json.JSONDecodeError as e:
        log(f"❌ Gemini 回傳的內容不是合法 JSON:{e}")
        return
    except Exception as e:
        log(f"❌ 線上分析失敗:{type(e).__name__}: {e}")
        log("   (這個功能依賴 Gemini 直接讀取 YouTube 網址,若影片非公開或過長,可能會失敗;")
        log("    失敗的話可以改用模式 1 下載後再處理。)")
        return

    data_path = write_video_data(video_id, url, quiz_data)

    subject = quiz_data.get("subject", "")
    clean_url = f"https://www.youtube.com/watch?v={video_id}"
    record_manifest_entry(video_id, subject, clean_url)
    build_index_html()

    port = ensure_local_http_server()
    local_url = f"http://localhost:{port}/viewer.html?id={video_id}"

    log(f"✅ 已產生線上複習頁面(資料:{data_path}):{local_url}")
    try:
        import webbrowser
        webbrowser.open(local_url)
    except Exception:
        pass

# ==========================================
# 8c. YouTube 上傳功能(OAuth 授權,上傳影片+字幕+加入播放清單)
# ==========================================
# 跟前面用 YOUTUBE_API_KEY 的唯讀查詢不同,上傳影片/字幕/加入播放清單是會
# 「寫入」你頻道的動作,YouTube 不允許單純用 API Key 做這件事,一定要走
# OAuth 使用者授權(你親自登入同意一次,之後重複使用,不用每次都登入)。
#
# 設定步驟(第一次使用前):
# 1. Google Cloud Console → 同一個專案(或另建) → APIs & Services → 憑證
# 2. 建立憑證 → OAuth 用戶端 ID → 應用程式類型選「電腦版應用程式」
# 3. 下載 JSON,改名成 client_secret.json,放在跟 study_pipeline.py 同一個資料夾
# 4. 需要額外安裝套件:pip install google-auth-oauthlib google-api-python-client
YOUTUBE_UPLOAD_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]
CLIENT_SECRET_PATH = "./client_secret.json"
UPLOAD_TOKEN_PATH = "./upload_token.json"

def get_youtube_upload_service():
    """建立有上傳權限的 YouTube API 連線,第一次會跳瀏覽器要你登入同意授權"""
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        raise RuntimeError(
            "缺少必要套件,請先執行:\n"
            "  pip install google-auth-oauthlib google-api-python-client"
        )

    creds = None
    if os.path.exists(UPLOAD_TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(UPLOAD_TOKEN_PATH, YOUTUBE_UPLOAD_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CLIENT_SECRET_PATH):
                raise RuntimeError(
                    f"找不到 {CLIENT_SECRET_PATH}。\n"
                    "   請先去 Google Cloud Console 建立「OAuth 用戶端 ID」(應用程式類型選電腦版應用程式),\n"
                    "   下載 JSON 後改名成 client_secret.json,放在這支程式同一個資料夾。"
                )
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_PATH, YOUTUBE_UPLOAD_SCOPES)
            log("  🔐 需要授權,即將開啟瀏覽器,請登入你的 YouTube 帳號並同意授權...")
            creds = flow.run_local_server(port=0)
        with open(UPLOAD_TOKEN_PATH, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    return build("youtube", "v3", credentials=creds)

def upload_video_to_youtube(service, file_path, title, description="", privacy_status="unlisted", tags=None):
    """上傳一支影片,回傳新影片的 video_id。用 resumable upload,大檔案也能穩定上傳。"""
    from googleapiclient.http import MediaFileUpload

    body = {
        "snippet": {
            "title": (title or os.path.basename(file_path))[:100],  # YouTube標題上限100字
            "description": (description or "")[:5000],
            "tags": tags or [],
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(file_path, chunksize=-1, resumable=True)
    request = service.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            log(f"    ⬆️ 上傳進度:{int(status.progress() * 100)}%")
    return response["id"]

def add_video_to_playlist(service, video_id, playlist_id):
    """把影片加進指定播放清單"""
    body = {
        "snippet": {
            "playlistId": playlist_id,
            "resourceId": {"kind": "youtube#video", "videoId": video_id},
        }
    }
    service.playlistItems().insert(part="snippet", body=body).execute()

def upload_captions_to_video(service, video_id, srt_path, language="zh-Hant", name="AI 修正字幕"):
    """上傳字幕檔(.srt)到指定影片"""
    from googleapiclient.http import MediaFileUpload

    body = {
        "snippet": {
            "videoId": video_id,
            "language": language,
            "name": name,
            "isDraft": False,
        }
    }
    media = MediaFileUpload(srt_path, mimetype="application/octet-stream")
    service.captions().insert(part="snippet", body=body, media_body=media).execute()

def mode_youtube_upload():
    """
    批次上傳模式:掃描 audio_files/ 資料夾,選好一批影片跟一個播放清單,
    一次自動跑完全部上傳(影片標題用出題時的科目名稱,有對應.srt字幕的話也一併上傳)。
    """
    files = discover_audio_files()
    if not files:
        log(f"⚠️ 在目前目錄與 {INPUT_DIR} 都沒有找到任何影音檔案。")
        return

    print(f"\n📂 找到 {len(files)} 個影音檔案:\n")
    for idx, f in enumerate(files, 1):
        print(f"  [{idx}] {f}")

    print("\n請輸入要上傳的編號,可以用逗號或範圍,例如:1,3,5-7")
    sel_str = input("你的選擇:").strip()
    selected_indices = parse_selection(sel_str, len(files))
    if not selected_indices:
        log("⚠️ 沒有選到任何合法的編號,回到主選單。")
        return
    selected_files = [files[i - 1] for i in selected_indices]

    print(f"\n已選擇 {len(selected_files)} 個檔案:")
    for f in selected_files:
        print(f"  - {f}")

    playlist_id = input(
        "\n要加入哪個播放清單?(去 YouTube 播放清單網址複製 list= 後面那串 ID,"
        "不需要加清單直接按 Enter):"
    ).strip()

    confirm = input(f"\n即將以「不公開(Unlisted)」上傳這 {len(selected_files)} 支影片,確定嗎?(y/n):").strip().lower()
    if confirm != "y":
        log("已取消,回到主選單。")
        return

    try:
        service = get_youtube_upload_service()
    except Exception as e:
        log(f"❌ 無法建立 YouTube 上傳連線:{e}")
        return

    success_count = 0
    for idx, file_path in enumerate(selected_files, 1):
        original_filename = os.path.basename(file_path)
        safe_base_name = re.sub(r'[\\/*?:"<>|]', "", os.path.splitext(original_filename)[0])
        video_id_in_name = extract_youtube_id(original_filename)
        dedup_key = video_id_in_name or safe_base_name

        title = lookup_video_title(dedup_key, fallback=safe_base_name)

        log(f"---- [{idx}/{len(selected_files)}] {original_filename} ----")
        log(f"  📝 標題:{title}")

        try:
            new_video_id = upload_video_to_youtube(
                service, file_path, title=title, privacy_status="unlisted"
            )
            log(f"  ✅ 已上傳,YouTube 影片 ID:{new_video_id}")
        except Exception as e:
            log(f"  ❌ 上傳失敗,略過繼續下一支:{type(e).__name__}: {e}")
            continue

        if playlist_id:
            try:
                add_video_to_playlist(service, new_video_id, playlist_id)
                log(f"  ✅ 已加入播放清單")
            except Exception as e:
                log(f"  ⚠️ 加入播放清單失敗(影片本身已上傳成功):{e}")

        srt_candidate = os.path.join(DATA_DIR, f"{dedup_key}.srt")
        if os.path.exists(srt_candidate):
            try:
                upload_captions_to_video(service, new_video_id, srt_candidate)
                log(f"  ✅ 已上傳修正字幕")
            except Exception as e:
                log(f"  ⚠️ 字幕上傳失敗(影片本身已上傳成功):{e}")

        success_count += 1
        time.sleep(2)  # 避免瞬間對API打太多請求

    log(f"\n✨ 批次上傳完成:{success_count}/{len(selected_files)} 支成功。")
def print_banner():
    print("=" * 50)
    print("  ✈️  航空考題 → Anki 自動化流程")
    print("=" * 50)
    print(f"  選擇題 TSV 位置:{os.path.abspath(MC_TSV_PATH)}")
    print(f"  克漏字 TSV 位置:{os.path.abspath(CLOZE_TSV_PATH)}")
    check_required_assets()
    try:
        port = ensure_local_http_server()
        print(f"  線上複習頁面清單:http://localhost:{port}/  (可以加入書籤,反覆複習用)")
    except Exception as e:
        print(f"  ⚠️ 本機複習伺服器啟動失敗:{type(e).__name__}: {e}")
    print("=" * 50)

def choose_mode():
    print("\n請選擇今天要用哪種模式:\n")
    print("  [1] 我要貼 YouTube 網址,自動下載並出題")
    print("  [2] 掃描資料夾,自動處理全部還沒做過的影音檔")
    print("  [3] 掃描資料夾,我自己勾選要處理哪幾支")
    print("  [4] 線上快速模式(不下載,直接分析YouTube網址,產生可點擊時間戳記的網頁)")
    print("  [5] 上傳影片到 YouTube(可選加入播放清單、上傳修正字幕)")
    print("  [0] 離開\n")

    while True:
        choice = input("請輸入選項數字(1 / 2 / 3 / 4 / 5 / 0):").strip()
        if choice in ("0", "1", "2", "3", "4", "5"):
            return choice
        print("  ⚠️ 請輸入 0、1、2、3、4 或 5。")

def mode_youtube():
    print("\n網址來源:")
    print("  [1] 現場貼網址(每行一個,貼完後輸入空白行結束)")
    print("  [2] 沿用 video_urls.txt 裡已經寫好的網址\n")

    sub_choice = input("請輸入選項數字(1 / 2):").strip()

    if sub_choice == "1":
        print("請貼上網址,一行一個,貼完後直接按 Enter(空白行)結束輸入:")
        urls = []
        while True:
            line = input().strip()
            if not line:
                break
            urls.append(line)
        if not urls:
            log("⚠️ 沒有輸入任何網址,回到主選單。")
            return
    else:
        urls = load_urls()
        if not urls:
            log("⚠️ video_urls.txt 裡沒有任何網址,請填入後再選這個模式,回到主選單。")
            return

    all_video_urls = expand_all(urls)
    if not all_video_urls:
        log("⚠️ 展開後沒有任何可下載的影片網址,回到主選單。")
        return

    run_youtube_batch(all_video_urls)

def mode_scan_all():
    audio_files = discover_audio_files()
    if not audio_files:
        log(f"⚠️ 在目前目錄與 {INPUT_DIR} 都沒有找到任何影音檔案。")
        return
    log(f"📂 掃描到 {len(audio_files)} 個影音檔案,即將全部送去出題(已處理過的會自動跳過)。")
    overrides = prompt_for_missing_video_ids(audio_files)
    run_batch(audio_files, video_id_overrides=overrides)

def parse_selection(sel_str, max_index):
    """解析像 "1,3,5-7,10" 的輸入,回傳排序、去重、合法範圍內的編號清單"""
    indices = set()
    for part in sel_str.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            try:
                a, b = part.split("-")
                a, b = int(a.strip()), int(b.strip())
                indices.update(range(min(a, b), max(a, b) + 1))
            except ValueError:
                print(f"  ⚠️ 無法解析範圍「{part}」,已略過。")
        else:
            try:
                indices.add(int(part))
            except ValueError:
                print(f"  ⚠️ 無法解析編號「{part}」,已略過。")
    return sorted(i for i in indices if 1 <= i <= max_index)

def mode_manual_select():
    audio_files = discover_audio_files()
    if not audio_files:
        log(f"⚠️ 在目前目錄與 {INPUT_DIR} 都沒有找到任何影音檔案。")
        return

    print(f"\n📂 找到 {len(audio_files)} 個影音檔案:\n")
    for idx, f in enumerate(audio_files, 1):
        no_id_mark = "  ⚠️ 無 VideoID" if extract_youtube_id(os.path.basename(f)) is None else ""
        print(f"  [{idx}] {f}{no_id_mark}")

    print("\n請輸入要處理的編號,可以用逗號或範圍,例如:1,3,5-7")
    sel_str = input("你的選擇:").strip()

    selected_indices = parse_selection(sel_str, len(audio_files))
    if not selected_indices:
        log("⚠️ 沒有選到任何合法的編號,回到主選單。")
        return

    selected_files = [audio_files[i - 1] for i in selected_indices]

    print(f"\n已選擇 {len(selected_files)} 個檔案:")
    for f in selected_files:
        print(f"  - {f}")

    overrides = prompt_for_missing_video_ids(selected_files)

    force_choice = input(
        "\n如果這些檔案裡有「已經處理過」的,要強制重新產生一次嗎?"
        "(y = 強制重跑 / n = 已處理過的自動跳過,預設 n):"
    ).strip().lower()
    force = (force_choice == "y")

    confirm = input("\n確認開始處理嗎?(y/n):").strip().lower()
    if confirm != "y":
        log("已取消,回到主選單。")
        return

    run_batch(selected_files, force=force, video_id_overrides=overrides)

def main():
    print_banner()
    while True:
        choice = choose_mode()
        if choice == "0":
            print("掰掰,祝考試順利!✈️")
            break
        elif choice == "1":
            mode_youtube()
        elif choice == "2":
            mode_scan_all()
        elif choice == "3":
            mode_manual_select()
        elif choice == "4":
            run_online_quick_mode()
        elif choice == "5":
            mode_youtube_upload()

        again = input("\n要回到主選單再做一次嗎?(y/n):").strip().lower()
        if again != "y":
            print("掰掰,祝考試順利!✈️")
            break

if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--serve-online-study":
        run_online_study_server(int(sys.argv[2]))
    else:
        main()
