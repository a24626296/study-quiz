# -*- coding: utf-8 -*-
"""
pytest 共用設定:在 import 兩支主程式之前,先把 yt_dlp / google.genai
這些外部套件模擬掉,這樣測試環境不需要真的裝這些套件、也不需要真的
API 金鑰,測試才能在任何環境(包含 CI)裡穩定跑。
"""

import os
import sys
import types

# 模擬 yt_dlp
if "yt_dlp" not in sys.modules:
    yt_dlp_mod = types.ModuleType("yt_dlp")

    class _FakeYoutubeDL:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, *a, **kw):
            return {}

    yt_dlp_mod.YoutubeDL = _FakeYoutubeDL
    sys.modules["yt_dlp"] = yt_dlp_mod

# 模擬 google.genai
if "google" not in sys.modules:
    google_mod = types.ModuleType("google")
    genai_mod = types.ModuleType("google.genai")

    class _FakeClient:
        def __init__(self, *a, **kw):
            self.models = types.SimpleNamespace(generate_content=lambda **kw: None)

    genai_mod.Client = _FakeClient

    class _FakeTypes:
        class Part:
            def __init__(self, *a, **kw):
                pass

        class FileData:
            def __init__(self, *a, **kw):
                pass

        class UploadFileConfig:
            def __init__(self, *a, **kw):
                pass

    sys.modules["google"] = google_mod
    sys.modules["google.genai"] = genai_mod
    sys.modules["google.genai.types"] = _FakeTypes()
    google_mod.genai = genai_mod

# 兩支主程式在 import 時需要看到這些環境變數才不會提早 sys.exit()
os.environ.setdefault("GEMINI_API_KEY", "test-dummy-key")
