import os
import sys
import re
import json
from datetime import datetime
from youtube_transcript_api import YouTubeTranscriptApi
from google import genai

def extract_video_id(url):
    """從 YouTube 網址提取 Video ID"""
    pattern = r"(?:v=|\/)([0-9A-Za-z_-]{11})"
    match = re.search(pattern, url)
    if match:
        return match.group(1)
    raise ValueError("無效的 YouTube 網址")

def get_transcript(video_id):
    """取得影片字幕"""
    try:
        # 使用 YouTubeTranscriptApi 正確語法
        try:
            transcript_list = YouTubeTranscriptApi.get_transcript(video_id, languages=['zh-TW', 'zh-Hant', 'zh-CN', 'zh-Hans', 'en'])
        except AttributeError:
            # 相容部分套件版本的另一種呼叫方式
            ytt = YouTubeTranscriptApi()
            transcript_list = ytt.fetch(video_id, languages=['zh-TW', 'zh-Hant', 'zh-CN', 'zh-Hans', 'en'])
            
        text = " ".join([item['text'] for item in transcript_list])
        return text
    except Exception as e:
        print(f"無法取得字幕: {e}")
        return None

def generate_quiz_with_gemini(transcript_text):
    """使用 Gemini API 根據字幕產生題目與對應時間點 (秒)"""
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    
    prompt = f"""
    你是一個專業的複習考題設計師。請根據以下影片逐字稿，設計 3~5 題選擇題。
    每題請包含：
    1. 題目內容
    2. 四個選項 (A, B, C, D)
    3. 正確答案
    4. 詳細解析
    5. 該題目答案出現的大約時間點 (秒數，例如 85)

    請嚴格依照下列 JSON 格式輸出，不要包含任何 markdown 標記（如 ```json）：
    {{
        "title": "影片主題標題",
        "quizzes": [
            {{
                "id": 1,
                "question": "問題內容？",
                "options": ["A. 選項一", "B. 選項二", "C. 選項三", "D. 選項四"],
                "answer": "B. 選項二",
                "explanation": "解析說明...",
                "timestamp": 85
            }}
        ]
    }}

    影片逐字稿：
    {transcript_text[:10000]}
    """
    
    # 修正模型名稱為 gemini-1.5-flash
    response = client.models.generate_content(
        model='gemini-1.5-flash',
        contents=prompt
    )
    
    # 清理非 JSON 格式字元
    cleaned_text = response.text.replace('```json', '').replace('```', '').strip()
    return json.loads(cleaned_text)

def build_quiz_html(video_id, quiz_data):
    """產生單一影片的測驗頁面 HTML"""
    title = quiz_data.get("title", "影片複習測驗")
    quizzes = quiz_data.get("quizzes", [])
    
    quizzes_html = ""
    for q in quizzes:
        options_html = "".join([f"<li>{opt}</li>" for opt in q.get("options", [])])
        seconds = q.get("timestamp", 0)
        
        quizzes_html += f"""
        <div class="quiz-card" id="quiz-{q['id']}">
            <div class="quiz-header">
                <h3>選擇題 {q['id']}</h3>
                <div class="btn-group">
                    <button class="jump-btn" onclick="jumpToTime({seconds})">
                        🎬 本頁跳轉 ({seconds}秒)
                    </button>
                    <a class="yt-link-btn" href="[https://www.youtube.com/watch?v=](https://www.youtube.com/watch?v=){video_id}&t={seconds}s" target="_blank" title="在新分頁打開 YouTube 並跳至 {seconds} 秒">
                        ↗️ YT開啟
                    </a>
                </div>
            </div>
            <p class="question">{q['question']}</p>
            <ul class="options">
                {options_html}
            </ul>
            <details class="answer-box">
                <summary>👉 看答案與解析</summary>
                <div class="answer-content">
                    <p><strong>正確答案：</strong> {q['answer']}</p>
                    <p><strong>解析：</strong> {q['explanation']}</p>
                </div>
            </details>
        </div>
        """

    html_content = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        body {{
            background-color: #0f172a;
            color: #f8fafc;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            margin: 0;
            padding: 20px;
        }}
        .top-nav {{
            margin-bottom: 20px;
        }}
        .top-nav a {{
            color: #38bdf8;
            text-decoration: none;
            font-weight: bold;
        }}
        .container {{
            display: flex;
            flex-wrap: wrap;
            gap: 20px;
            max-width: 1400px;
            margin: 0 auto;
        }}
        .video-section {{
            flex: 1 1 450px;
            position: sticky;
            top: 20px;
            height: fit-content;
        }}
        .video-container {{
            position: relative;
            padding-bottom: 56.25%;
            height: 0;
            overflow: hidden;
            border-radius: 12px;
            background: #000;
        }}
        .video-container iframe {{
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
        }}
        .fallback-notice {{
            margin-top: 12px;
            padding: 10px;
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 8px;
            text-align: center;
            font-size: 14px;
        }}
        .fallback-notice a {{
            color: #38bdf8;
            text-decoration: underline;
        }}
        .quiz-section {{
            flex: 2 1 600px;
        }}
        .quiz-card {{
            background: #1e293b;
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
            border: 1px solid #334155;
        }}
        .quiz-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .btn-group {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .jump-btn {{
            background: #0284c7;
            color: white;
            border: none;
            padding: 6px 12px;
            border-radius: 6px;
            cursor: pointer;
            font-weight: bold;
            font-size: 13px;
        }}
        .jump-btn:hover {{
            background: #0369a1;
        }}
        .yt-link-btn {{
            background: #334155;
            color: #38bdf8;
            text-decoration: none;
            padding: 6px 10px;
            border-radius: 6px;
            font-size: 13px;
            font-weight: bold;
            border: 1px solid #475569;
        }}
        .yt-link-btn:hover {{
            background: #475569;
            color: #7dd3fc;
        }}
        .options {{
            list-style: none;
            padding-left: 0;
        }}
        .options li {{
            background: #334155;
            margin: 8px 0;
            padding: 10px 14px;
            border-radius: 6px;
        }}
        .answer-box {{
            margin-top: 12px;
            cursor: pointer;
            color: #38bdf8;
        }}
        .answer-content {{
            background: #0f172a;
            padding: 12px;
            border-radius: 6px;
            margin-top: 8px;
            color: #f8fafc;
        }}
    </style>
</head>
<body>
    <div class="top-nav">
        <a href="index.html">← 回複習清單</a>
    </div>
    <h2>{title}</h2>
    <div class="container">
        <div class="video-section">
            <div class="video-container">
                <iframe id="yt-player" 
                        src="[https://www.youtube.com/embed/](https://www.youtube.com/embed/){video_id}?enablejsapi=1" 
                        title="YouTube video player" 
                        frameborder="0" 
                        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" 
                        allowfullscreen>
                </iframe>
            </div>
            <div class="fallback-notice">
                ⚠️ 若上方內嵌播放器顯示「無法播放」，請 
                <a id="external-link" href="[https://www.youtube.com/watch?v=](https://www.youtube.com/watch?v=){video_id}" target="_blank">
                    點此前往 YouTube 原頁面觀看 ↗
                </a>
            </div>
        </div>
        <div class="quiz-section">
            {quizzes_html}
        </div>
    </div>

    <script>
        function jumpToTime(seconds) {{
            const iframe = document.getElementById('yt-player');
            if (iframe && iframe.contentWindow) {{
                iframe.contentWindow.postMessage(JSON.stringify({{
                    'event': 'command',
                    'func': 'seekTo',
                    'args': [seconds, true]
                }}), '*');
                
                iframe.contentWindow.postMessage(JSON.stringify({{
                    'event': 'command',
                    'func': 'playVideo',
                    'args': []
                }}), '*');
            }}
        }}
    </script>
</body>
</html>
"""
    return html_content

def update_index_html(video_id, title):
    """更新總清單 index.html"""
    index_path = "online_study/index.html"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    item_html = f"""
    <div class="card">
        <a href="{video_id}.html">
            <img src="[https://img.youtube.com/vi/](https://img.youtube.com/vi/){video_id}/mqdefault.jpg" alt="thumbnail">
            <div class="card-info">
                <h3>{title}</h3>
                <p>{now_str}</p>
            </div>
        </a>
    </div>
    """
    
    full_index = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>複習清單</title>
    <style>
        body {{ background: #0f172a; color: #fff; font-family: sans-serif; padding: 20px; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 20px; margin-top: 20px; }}
        .card {{ background: #1e293b; border-radius: 8px; overflow: hidden; border: 1px solid #334155; }}
        .card a {{ color: white; text-decoration: none; }}
        .card img {{ width: 100%; display: block; }}
        .card-info {{ padding: 12px; }}
        .card-info h3 {{ margin: 0 0 8px 0; font-size: 16px; }}
        .card-info p {{ margin: 0; color: #94a3b8; font-size: 12px; }}
    </style>
</head>
<body>
    <h1>📚 複習清單</h1>
    <div class="grid">
        {item_html}
    </div>
</body>
</html>
"""
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(full_index)

def main():
    if len(sys.argv) < 2:
        print("請提供 YouTube 網址")
        sys.exit(1)
        
    url = sys.argv[1]
    video_id = extract_video_id(url)
    print(f"解析 Video ID: {video_id}")
    
    os.makedirs("online_study", exist_ok=True)
    
    transcript = get_transcript(video_id)
    if not transcript:
        print("無法取得字幕，改用通用標題出題")
        transcript = f"影片 ID: {video_id}"
        
    print("正在請求 Gemini API 出題...")
    quiz_data = generate_quiz_with_gemini(transcript)
    
    # 產生單頁 HTML
    quiz_html = build_quiz_html(video_id, quiz_data)
    with open(f"online_study/{video_id}.html", "w", encoding="utf-8") as f:
        f.write(quiz_html)
        
    # 更新 index.html
    update_index_html(video_id, quiz_data.get("title", video_id))
    print("出題完成！頁面已儲存至 online_study/")

if __name__ == "__main__":
    main()
