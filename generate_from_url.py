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

用法(本機測試用,平常你不需要手動打這行,Actions 會自動呼叫):
    set GEMINI_API_KEY=你的key   (Windows)
    python scripts/generate_from_url.py "https://www.youtube.com/watch?v=xxxxxxxxxxx"
"""

import os
import re
import sys
import json
import glob
import datetime
import html as html_lib

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

ONLINE_STUDY_DIR = os.path.normpath("./online_study")
os.makedirs(ONLINE_STUDY_DIR, exist_ok=True)

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


def strip_json_fences(text):
    text = text.strip()
    text = re.sub(r'^```json\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'^```\s*', '', text)
    text = re.sub(r'```\s*$', '', text)
    return text.strip()


def generate_quiz_from_youtube_url(video_id):
    """直接把 YouTube 影片交給 Gemini 分析,不下載、不上傳檔案。"""
    clean_url = f"https://www.youtube.com/watch?v={video_id}"
    prompt = f"{QUIZ_SYSTEM_PROMPT}\n影片網址:{clean_url}。請直接分析這支 YouTube 影片後出題。"
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
            <div class="explain">{q.get('explanation', '')}</div>
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
        body = f"""
          <div class="q-text">{cloze_masked}</div>
          <details><summary>看中文對照與解析</summary>
            <div class="q-zh">{c.get('zh_translation', '')}</div>
            <div class="explain">{c.get('explanation', '')}</div>
          </details>
          <div class="tag">{c.get('tags', '')}</div>
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

  function onYouTubeIframeAPIReady() {{
    player = new YT.Player('player', {{
      height: '270',
      width: '480',
      videoId: '{video_id}',
      playerVars: {{ 'playsinline': 1, 'origin': window.location.origin }}
    }});
  }}
  function seekTo(seconds) {{
    if (player && player.seekTo) {{
      player.seekTo(seconds, true);
      player.playVideo();
    }}
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


def build_index_html():
    """掃描 online_study 資料夾裡所有 *.html(排除 index.html 自己),
    依修改時間新到舊排序,產生一個手機好點的清單首頁。"""
    files = [
        f for f in glob.glob(os.path.join(ONLINE_STUDY_DIR, "*.html"))
        if os.path.basename(f) != "index.html"
    ]
    files.sort(key=os.path.getmtime, reverse=True)

    items_html = ""
    for f in files:
        video_id = os.path.splitext(os.path.basename(f))[0]
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(f)).strftime("%Y-%m-%d %H:%M")
        items_html += f"""
        <a class="item" href="./{html_lib.escape(os.path.basename(f))}">
          <img src="https://i.ytimg.com/vi/{video_id}/mqdefault.jpg" loading="lazy">
          <div class="meta">
            <div class="date">{mtime}</div>
            <div class="vid">{video_id}</div>
          </div>
        </a>"""

    index_html = f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>複習清單</title>
<style>
  body {{ background:#0f172a; color:#e2e8f0; font-family:"Microsoft JhengHei",Arial,sans-serif; margin:0; padding:20px; }}
  h1 {{ font-size:20px; margin-bottom:16px; }}
  .item {{ display:flex; gap:12px; align-items:center; background:#1e293b; border-radius:8px;
           padding:10px; margin-bottom:10px; text-decoration:none; color:#e2e8f0; }}
  .item img {{ width:120px; border-radius:6px; flex-shrink:0; }}
  .meta .date {{ font-size:13px; color:#94a3b8; }}
  .meta .vid {{ font-size:14px; color:#72ef95; margin-top:4px; }}
  .empty {{ color:#94a3b8; }}
</style>
</head>
<body>
<h1>📚 複習清單({len(files)})</h1>
{items_html if items_html else '<p class="empty">目前還沒有產生任何複習頁面。</p>'}
</body>
</html>"""

    with open(os.path.join(ONLINE_STUDY_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(index_html)


def main():
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("❌ 沒有收到 YouTube 網址參數。")
        sys.exit(1)

    url = sys.argv[1].strip()
    video_id = extract_video_id_from_url(url)
    if not video_id:
        print("❌ 無法從網址判斷出 YouTube 影片 ID,請確認網址格式。")
        sys.exit(1)

    print(f"🌐 分析中:https://www.youtube.com/watch?v={video_id}")
    quiz_data = generate_quiz_from_youtube_url(video_id)

    html_out = build_online_study_html(video_id, url, quiz_data)
    out_path = os.path.join(ONLINE_STUDY_DIR, f"{video_id}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_out)
    print(f"✅ 已產生:{out_path}")

    build_index_html()
    print("✅ 已更新 index.html 清單頁")


if __name__ == "__main__":
    main()
