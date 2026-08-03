# -*- coding: utf-8 -*-
"""
同步檢查工具
============
本機版(study_pipeline.py)跟雲端版(scripts/generate_from_url.py)有一部分邏輯
「理論上應該完全一致」(例如逐字稿轉換、時間戳記解析、出題 prompt 的規則文字),
但兩支檔案是分開維護的,改一邊忘記改另一邊是這個架構下最容易出的錯。

這支工具用 Python 的 ast 模組,把兩邊「應該同步」的函式原始碼抓出來比對,
不一樣就報告出來,讓你在真的遇到「本機看起來對、雲端卻怪怪的」之前先發現。

用法:
    python check_sync.py
    (在 repo 根目錄執行,同時讀取 study_pipeline.py 跟 scripts/generate_from_url.py)
"""

import ast
import difflib
import sys
from pathlib import Path

# 這些函式 / 常數「理論上應該逐字一致」,新增功能時如果又是這種
# 純邏輯、不涉及環境差異(GUI選單 vs CI無頭執行)的部分,記得也加進這個清單。
SYNCED_NAMES = [
    "seconds_to_srt_time",
    "build_srt",
    "timestamp_to_seconds",
    "extract_video_id_from_url",
    "strip_json_fences",
]

# 這些是「常數字串」,一樣要求逐字一致
SYNCED_CONSTANTS = [
    "TRANSCRIPT_JSON_FIELD",
    "TRANSCRIPT_RULES",
]


def get_source_segments(file_path):
    """把檔案裡所有函式定義跟模組層級常數賦值,整理成 {名稱: 原始碼字串}"""
    source = Path(file_path).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(file_path))
    segments = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            segments[node.name] = ast.get_source_segment(source, node)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    segments[target.id] = ast.get_source_segment(source, node)
    return segments


def normalize(code):
    """比對前先把純粹排版差異(行尾空白、開頭縮排統一)去掉,避免誤報"""
    if code is None:
        return None
    lines = [line.rstrip() for line in code.strip().splitlines()]
    return "\n".join(lines)


def main():
    repo_root = Path(__file__).resolve().parent
    local_path = repo_root / "study_pipeline.py"
    cloud_path = repo_root / "scripts" / "generate_from_url.py"

    if not local_path.exists() or not cloud_path.exists():
        print(f"❌ 找不到檔案,請確認在 repo 根目錄執行這支工具。")
        print(f"   期待路徑:{local_path}")
        print(f"   期待路徑:{cloud_path}")
        sys.exit(1)

    local_segments = get_source_segments(local_path)
    cloud_segments = get_source_segments(cloud_path)

    all_checks = [(name, "function") for name in SYNCED_NAMES] + \
                 [(name, "constant") for name in SYNCED_CONSTANTS]

    problems = []
    for name, kind in all_checks:
        local_code = normalize(local_segments.get(name))
        cloud_code = normalize(cloud_segments.get(name))

        if local_code is None and cloud_code is None:
            problems.append(f"⚠️ {kind} `{name}`:兩邊都找不到,清單可能過時了(已經改名或刪除?)")
            continue
        if local_code is None:
            problems.append(f"❌ {kind} `{name}`:本機版(study_pipeline.py)找不到,雲端版有")
            continue
        if cloud_code is None:
            problems.append(f"❌ {kind} `{name}`:雲端版(generate_from_url.py)找不到,本機版有")
            continue
        if local_code != cloud_code:
            diff = "\n".join(difflib.unified_diff(
                local_code.splitlines(), cloud_code.splitlines(),
                fromfile=f"本機版:{name}", tofile=f"雲端版:{name}", lineterm=""
            ))
            problems.append(f"❌ {kind} `{name}`:兩邊內容不一致\n{diff}\n")

    print(f"🔍 檢查 {len(all_checks)} 個應同步項目...")
    if not problems:
        print("✅ 全部同步,本機版跟雲端版這幾個共用邏輯完全一致。")
        return

    print(f"\n發現 {len(problems)} 個問題:\n")
    for p in problems:
        print(p)
    sys.exit(1)


if __name__ == "__main__":
    main()
