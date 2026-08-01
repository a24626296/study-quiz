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
import xml.etree.ElementTree as ET

from google import genai
from google.genai import types

# ==========================================
# 基本設定
# ==========================================
API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
if not API_KEY:
    print("❌ 找不到環境變數 GEMINI_API_KEY,請確認 GitHub Secrets 有設定好。")
    sys.exit(1)

client = genai.Client(api_key=API_KEY)
MODEL_NAME = "gemini-3-flash-preview"
MAX_RETRIES_ON_TRANSIENT = 3  # 429限流 / 503過載 共用的重試次數上限

ONLINE_STUDY_DIR = os.path.normpath("./online_study")
os.makedirs(ONLINE_STUDY_DIR, exist_ok=True)

# 關鍵修正:避免 GitHub Pages 用 Jekyll 處理這個資料夾。
# Jekyll 的 Liquid 樣板引擎會把 HTML/JS 裡的 {{ ... }} 誤判成樣板語法,
# 一旦 Gemini 產生的克漏字格式跟預期的正規表示式沒有完全對上,殘留的
# {{c1::...}} 就可能讓 Jekyll 建置該頁面失敗,導致該頁「消失」變 404。
NOJEKYLL_PATH = os.path.join(ONLINE_STUDY_DIR, ".nojekyll")
if not os.path.exists(NOJEKYLL_PATH):
    open(NOJEKYLL_PATH, "a", encoding="utf-8").close()

QUIZ_SYSTEM_PROMPT = """
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
  ]
}

規則:
- mc_questions:請出 3~5 題觀念理解或故障邏輯單選題,適合「理解型」的知識點
- cloze_items:請出 4~8 個值得直接背誦的 Memory Item、限制數據(limitations)、定義或口訣,適合「記憶型」的知識點;每一則只挖一個重點(只用 {{c1::}},不要有 c2、c3)
- cloze_text 裡的挖空標記務必嚴格使用 {{c1::關鍵字}} 這個格式,前後不要加空格、不要用全形符號,只能有一組 c1
- explanation 欄位要簡潔(1-2句話),不要跟 zh_translation 重複
- timestamp 一律用 MM:SS 或 HH:MM:SS 格式的純文字,不要加中括號、不要加其他符號
- 除了上述 JSON 物件之外,不要輸出任何文字
"""


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


def fetch_playlist_video_ids(playlist_id):
    """
    讀取 YouTube 公開播放清單的 RSS 摘要,取出裡面的影片 ID 清單。

    刻意不用 yt-dlp:yt-dlp 在 GitHub Actions 這種雲端 IP 上常被 YouTube
    判定為機器人擋下來(需要瀏覽器 cookie 才能繞過,CI 環境沒有瀏覽器)。
    RSS 摘要是單純的一次網路請求,沒有這個問題。

    ⚠️ 已知限制:YouTube 的公開播放清單 RSS 通常只列出「最新的一部分」
    影片(實測大約 15 支上下),不保證涵蓋整份清單。清單較長、或需要
    抓到全部/較舊的影片時,建議改用桌面版 study_pipeline.py 的模式1
    (用 yt-dlp,在你自己電腦上執行,沒有雲端 IP 被擋的問題)。
    """
    feed_url = f"https://www.youtube.com/feeds/videos.xml?playlist_id={playlist_id}"
    req = urllib.request.Request(feed_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = resp.read()

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
    }
    root = ET.fromstring(data)
    video_ids = []
    for entry in root.findall("atom:entry", ns):
        vid_el = entry.find("yt:videoId", ns)
        if vid_el is not None and vid_el.text:
            video_ids.append(vid_el.text.strip())
    return video_ids


def strip_json_fences(text):
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


def generate_quiz_from_youtube_url(video_id):
    """直接把 YouTube 影片交給 Gemini 分析,不下載、不上傳檔案。附帶 429 / 503 重試。"""
    clean_url = f"https://www.youtube.com/watch?v={video_id}"
    prompt = f"{QUIZ_SYSTEM_PROMPT}\n影片網址:{clean_url}。請直接分析這支 YouTube 影片後出題。"
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
    if not timestamp:
        return None
    m = re.match(r'^\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\s*$', str(timestamp))
    if not m:
        return None
    hh, mm, ss = m.groups()
    hh = hh or "0"
    return int(hh) * 3600 + int(mm) * 60 + int(ss)


def build_online_study_html(video_id, url, quiz_data):
    """產生一個單頁 HTML:左邊嵌入 YouTube 播放器,右邊列出題目,點時間戳記直接跳轉播放進度"""
    subject = quiz_data.get("subject", "")

    def q_block(idx, title, body_html, seconds, extra_class=""):
        seek_attr = f'onclick="seekTo({seconds})"' if seconds is not None else ""
        ts_badge = (
            f'<button class="ts-btn" {seek_attr}>🎬 跳轉</button>'
            if seconds is not None else ""
        )
        return f"""
        <div class="qcard {extra_class}">
          <div class="qcard-head"><span class="qnum">{title}</span>{ts_badge}</div>
          <div class="qcard-body">{body_html}</div>
        </div>"""

    def esc(text):
        return html_lib.escape(str(text or ""), quote=True)

    def hoverable(zh_text, en_text, css_class=""):
        return (
            f'<div class="hoverable {css_class}" data-zh="{esc(zh_text)}" data-en="{esc(en_text)}">'
            f'<span class="main-text"></span><span class="tooltip"></span></div>'
        )

    mc_html_parts = []
    for i, q in enumerate(quiz_data.get("mc_questions", []), 1):
        seconds = timestamp_to_seconds(q.get("timestamp"))
        zh_opts = q.get("zh_options", [])
        en_opts = q.get("en_options", [])
        options_html = ""
        for oi in range(max(len(zh_opts), len(en_opts))):
            zh_o = zh_opts[oi] if oi < len(zh_opts) else ""
            en_o = en_opts[oi] if oi < len(en_opts) else ""
            options_html += hoverable(zh_o, en_o, "q-option")
        question_html = hoverable(q.get("zh_question", ""), q.get("en_question", ""), "q-text")
        body = f"""
          {question_html}
          <div class="q-options">{options_html}</div>
          <details><summary>看答案與解析</summary>
            <div class="answer">正解:({q.get('answer', '')})</div>
            <div class="explain">{esc(q.get('explanation', ''))}</div>
          </details>
        """
        mc_html_parts.append(q_block(i, f"選擇題 {i}", body, seconds))

    cz_html_parts = []
    for i, c in enumerate(quiz_data.get("cloze_items", []), 1):
        seconds = timestamp_to_seconds(c.get("timestamp"))
        cloze_text = c.get("cloze_text", "")
        cloze_masked = re.sub(
            r'\{\{c1::(.*?)\}\}',
            r"""<span class="blank" onclick="this.classList.add('revealed')"><span class="blank-inner">\1</span></span>""",
            cloze_text
        )
        # 安全網:確保替換後不再殘留任何 {{ }},避免 Jekyll 誤判樣板語法
        cloze_masked = strip_stray_cloze_markup(cloze_masked)
        body = f"""
          <div class="q-text">{cloze_masked}</div>
          <details><summary>看中文對照與解析</summary>
            <div class="q-zh">{esc(c.get('zh_translation', ''))}</div>
            <div class="explain">{esc(c.get('explanation', ''))}</div>
          </details>
          <div class="tag">{esc(c.get('tags', ''))}</div>
        """
        cz_html_parts.append(q_block(i, f"背誦重點 {i}", body, seconds, extra_class="cloze-card"))

    html = f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{subject} - 線上複習</title>
<style>
  body {{ background:#0f172a; color:#e2e8f0; font-family:"Microsoft JhengHei",Arial,sans-serif; margin:0; }}
  .layout {{ display:flex; gap:20px; padding:20px; align-items:flex-start; flex-wrap:wrap; }}
  .player-col {{ position:sticky; top:20px; flex:0 0 480px; }}
  .player-col iframe {{ width:480px; height:270px; border:0; border-radius:8px; }}
  .subject {{ font-size:18px; font-weight:bold; margin:12px 0; color:#72ef95; }}
  .list-col {{ flex:1; min-width:320px; }}
  .qcard {{ background:#1e293b; border-radius:8px; padding:14px 16px; margin-bottom:14px; }}
  .cloze-card {{ border-left:4px solid #ff6b6b; }}
  .qcard-head {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; }}
  .qnum {{ color:#64B5F6; font-weight:bold; }}
  .ts-btn {{ background:#2196F3; color:#fff; border:none; padding:4px 10px; border-radius:4px; cursor:pointer; font-size:12px; }}
  .ts-btn:hover {{ background:#1769aa; }}
  .q-text {{ font-size:16px; line-height:1.6; margin-bottom:6px; }}
  .q-options {{ color:#cbd5e1; font-size:14px; line-height:1.8; margin-bottom:6px; }}
  .q-zh {{ color:#94a3b8; font-size:13px; margin:6px 0; }}
  .explain {{ color:#cbd5e1; font-size:13px; margin-top:6px; }}
  .tag {{ display:inline-block; margin-top:8px; background:rgba(250,82,82,0.15); color:#ff6b6b; padding:2px 8px; border-radius:4px; font-size:12px; }}
  details {{ margin-top:8px; }}
  summary {{ cursor:pointer; color:#64B5F6; font-size:13px; }}
  .answer {{ margin-top:8px; color:#72ef95; font-weight:bold; }}
  .blank {{ background:rgba(255,236,153,0.3); border-radius:3px; padding:1px 4px; cursor:pointer; }}
  .blank .blank-inner {{ visibility:hidden; }}
  .blank.revealed {{ background:rgba(255,236,153,0.7); }}
  .blank.revealed .blank-inner {{ visibility:visible; color:#d9480f; font-weight:bold; }}
  .blank:not(.revealed) .blank-inner::before {{ content:"﹍﹍﹍"; visibility:visible; color:#facc15; }}
  h2 {{ margin:16px 20px 0; }}

  .lang-toggle {{ display:flex; align-items:center; gap:8px; margin:0 20px 16px; font-size:13px; color:#94a3b8; }}
  .lang-btn {{ background:#1e293b; color:#cbd5e1; border:1px solid #334155; padding:5px 12px; border-radius:6px; cursor:pointer; font-size:13px; }}
  .lang-btn.active {{ background:#2196F3; color:#fff; border-color:#2196F3; }}

  .hoverable {{ position:relative; display:block; border-bottom:1px dotted #64B5F6; cursor:help; width:fit-content; }}
  .q-options .hoverable {{ margin-bottom:4px; }}
  .hoverable .tooltip {{
    display:none; position:absolute; left:0; top:100%; margin-top:6px;
    background:#0f172a; border:1px solid #64B5F6; color:#e2e8f0;
    padding:6px 10px; border-radius:6px; font-size:13px; white-space:normal;
    max-width:380px; z-index:50; box-shadow:0 4px 10px rgba(0,0,0,.4);
  }}
  .hoverable:hover .tooltip {{ display:block; }}
  a.back {{ display:inline-block; margin:16px 20px 0; color:#64B5F6; font-size:13px; text-decoration:none; }}
</style>
</head>
<body>
<a class="back" href="./index.html">← 回複習清單</a>
<div class="lang-toggle">
  顯示語言:
  <button id="btn-zh" class="lang-btn active" onclick="setLang('zh')">中文為主</button>
  <button id="btn-en" class="lang-btn" onclick="setLang('en')">English 為主</button>
  <span style="opacity:0.7;">(滑鼠移到題目/選項上可看另一種語言)</span>
</div>
<div class="layout">
  <div class="player-col">
    <div class="subject">{subject}</div>
    <div id="player"></div>
  </div>
  <div class="list-col">
    <h3>📝 選擇題</h3>
    {''.join(mc_html_parts) if mc_html_parts else '<p>(無)</p>'}
    <h3>🧠 背誦重點(點空格看答案)</h3>
    {''.join(cz_html_parts) if cz_html_parts else '<p>(無)</p>'}
  </div>
</div>

<script src="https://www.youtube.com/iframe_api"></script>
<script>
  var player;
  var primaryLang = 'zh';
  var embedBlocked = false;
  var VIDEO_ID = '{video_id}';

  function onYouTubeIframeAPIReady() {{
    player = new YT.Player('player', {{
      height: '270',
      width: '480',
      videoId: VIDEO_ID,
      playerVars: {{ 'playsinline': 1, 'origin': window.location.origin }},
      events: {{ 'onError': onPlayerError }}
    }});
  }}
  function onPlayerError(event) {{
    // 101 / 150 = 影片擁有者禁止嵌入播放,100 = 影片不存在或設為私人,5 = HTML5播放器錯誤
    if ([101, 150, 100, 5].indexOf(event.data) !== -1) {{
      embedBlocked = true;
    }}
  }}
  function seekTo(seconds) {{
    if (embedBlocked || !player || !player.seekTo) {{
      window.open('https://www.youtube.com/watch?v=' + VIDEO_ID + '&t=' + seconds + 's', '_blank');
      return;
    }}
    player.seekTo(seconds, true);
    player.playVideo();
  }}

  function applyLang() {{
    var otherLang = primaryLang === 'zh' ? 'en' : 'zh';
    document.querySelectorAll('.hoverable').forEach(function(el) {{
      var mainEl = el.querySelector('.main-text');
      var tipEl = el.querySelector('.tooltip');
      mainEl.textContent = el.dataset[primaryLang] || '';
      tipEl.textContent = el.dataset[otherLang] || '';
    }});
  }}

  function setLang(lang) {{
    primaryLang = lang;
    document.getElementById('btn-zh').classList.toggle('active', lang === 'zh');
    document.getElementById('btn-en').classList.toggle('active', lang === 'en');
    applyLang();
  }}

  document.addEventListener('DOMContentLoaded', applyLang);
</script>
</body>
</html>"""
    return html


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


def build_index_html():
    """
    重建複習清單首頁,顯示科目名稱、原始YouTube連結、產生時間。

    刪除按鈕:因為 GitHub Pages 是純靜態網站,網頁本身沒有後端可以真的
    刪除檔案。這裡的做法是——按下刪除後,自動複製影片 ID 並開啟
    「刪除線上複習頁面」這個 GitHub Action 的頁面,你只要貼上 ID、
    按 Run workflow,該影片就會被移除並重新部署。不是真正一鍵刪除,
    但已經是靜態網站能做到最接近的方式。
    """
    manifest = load_manifest()
    files = [
        f for f in glob.glob(os.path.join(ONLINE_STUDY_DIR, "*.html"))
        if os.path.basename(f) != "index.html"
    ]

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
        <div class="item">
          <a class="item-link" href="./{html_lib.escape(e['video_id'])}.html">
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
<style>
  body {{ background:#0f172a; color:#e2e8f0; font-family:"Microsoft JhengHei",Arial,sans-serif; margin:0; padding:20px; }}
  h1 {{ font-size:20px; margin-bottom:16px; }}
  .item {{ display:flex; justify-content:space-between; align-items:center; gap:12px; background:#1e293b;
           border-radius:8px; padding:10px; margin-bottom:10px; }}
  .item-link {{ display:flex; gap:12px; align-items:center; text-decoration:none; color:#e2e8f0; flex:1; min-width:0; }}
  .item-link img {{ width:120px; border-radius:6px; flex-shrink:0; }}
  .meta {{ min-width:0; }}
  .meta .subject {{ font-size:15px; font-weight:bold; color:#e2e8f0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
  .meta .date {{ font-size:13px; color:#94a3b8; margin-top:2px; }}
  .meta .vid {{ font-size:12px; color:#72ef95; margin-top:4px; }}
  .item-actions {{ display:flex; flex-direction:column; gap:6px; flex-shrink:0; }}
  .yt-link {{ font-size:12px; color:#64B5F6; text-decoration:none; white-space:nowrap; }}
  .yt-link:hover {{ text-decoration:underline; }}
  .del-btn {{ background:#3f1d1d; color:#ff6b6b; border:1px solid #ff6b6b; border-radius:6px; padding:4px 10px; font-size:12px; cursor:pointer; }}
  .del-btn:hover {{ background:#ff6b6b; color:#fff; }}
  .empty {{ color:#94a3b8; }}
</style>
</head>
<body>
<h1>📚 複習清單({len(entries)})</h1>
{items_html if items_html else '<p class="empty">目前還沒有產生任何複習頁面。</p>'}

<script>
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
    刪除指定 video_id 的複習頁面(html + manifest 紀錄),並重建 index.html。
    這是原本獨立的 delete_video.py 合併進來的邏輯,現在只用 --delete 參數區分。
    """
    manifest = load_manifest()
    existed_in_manifest = video_id in manifest
    manifest.pop(video_id, None)
    save_manifest(manifest)

    html_path = os.path.join(ONLINE_STUDY_DIR, f"{video_id}.html")
    existed_file = os.path.exists(html_path)
    if existed_file:
        os.remove(html_path)

    if not existed_in_manifest and not existed_file:
        print(f"⚠️ 找不到 video_id「{video_id}」的複習頁面或 manifest 紀錄,可能已經被刪除過了。")
    else:
        print(f"🗑️ 已刪除:{video_id}(html: {'有' if existed_file else '無'}, manifest: {'有' if existed_in_manifest else '無'})")

    build_index_html()
    print("✅ 已更新 index.html 清單頁")


def process_one_video(video_id, source_url):
    """處理單一影片:分析出題、寫入html、記錄manifest。回傳是否成功。"""
    clean_url = f"https://www.youtube.com/watch?v={video_id}"
    print(f"🌐 分析中:{clean_url}")
    try:
        quiz_data = generate_quiz_from_youtube_url(video_id)
    except Exception as e:
        print(f"❌ 這支影片處理失敗,略過繼續下一支:{type(e).__name__}: {e}")
        return False

    html_out = build_online_study_html(video_id, source_url, quiz_data)
    out_path = os.path.join(ONLINE_STUDY_DIR, f"{video_id}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_out)
    print(f"✅ 已產生:{out_path}")

    record_manifest(video_id, quiz_data.get("subject", ""), clean_url)
    return True


def main():
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("❌ 沒有收到參數。用法:\n"
              "  產生考題(單支影片):python generate_from_url.py \"https://www.youtube.com/watch?v=xxxxxxxxxxx\"\n"
              "  產生考題(播放清單):python generate_from_url.py \"https://www.youtube.com/playlist?list=xxxxxxxxxxx\"\n"
              "  刪除頁面:python generate_from_url.py --delete xxxxxxxxxxx")
        sys.exit(1)

    # --delete 模式:刪除指定影片,不需要呼叫 Gemini
    if sys.argv[1] == "--delete":
        if len(sys.argv) < 3 or not sys.argv[2].strip():
            print("❌ 沒有收到要刪除的 video_id 參數。")
            sys.exit(1)
        delete_video(sys.argv[2].strip())
        return

    url = sys.argv[1].strip()
    video_id = extract_video_id_from_url(url)

    # 單支影片模式
    if video_id:
        ok = process_one_video(video_id, url)
        build_index_html()
        print("✅ 已更新 index.html 清單頁")
        if not ok:
            sys.exit(1)
        return

    # 播放清單模式(網址裡有 list=,但沒有單支影片的 v=)
    playlist_id = extract_playlist_id_from_url(url)
    if playlist_id:
        print("📋 偵測到播放清單網址,正在讀取清單內容(RSS 摘要)...")
        try:
            video_ids = fetch_playlist_video_ids(playlist_id)
        except Exception as e:
            print(f"❌ 無法讀取播放清單內容:{type(e).__name__}: {e}")
            print("   (這個清單可能不支援 RSS 讀取,或暫時連不上。建議改用桌面版")
            print("    study_pipeline.py 的模式1處理整份清單。)")
            sys.exit(1)

        if not video_ids:
            print("⚠️ 讀不到任何影片,可能是清單為空、設為私人,或超出 RSS 可列出的範圍。")
            sys.exit(1)

        print(f"📋 共讀到 {len(video_ids)} 支影片(YouTube 播放清單 RSS 通常只列出最新約15支,")
        print("   較舊或超出範圍的影片可能抓不到,如需完整清單建議改用桌面版處理)。")

        success_count = 0
        for idx, vid in enumerate(video_ids, 1):
            print(f"---- [{idx}/{len(video_ids)}] ----")
            if process_one_video(vid, f"https://www.youtube.com/watch?v={vid}"):
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
