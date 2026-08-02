# -*- coding: utf-8 -*-
import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import generate_from_url as gen  # noqa: E402


class TestExtractVideoId:
    def test_standard_watch_url(self):
        assert gen.extract_video_id_from_url(
            "https://www.youtube.com/watch?v=xCfFw3K5D8A"
        ) == "xCfFw3K5D8A"

    def test_url_with_playlist_params(self):
        assert gen.extract_video_id_from_url(
            "https://www.youtube.com/watch?v=xCfFw3K5D8A&list=PLxxx&index=1"
        ) == "xCfFw3K5D8A"

    def test_short_url(self):
        assert gen.extract_video_id_from_url(
            "https://youtu.be/xCfFw3K5D8A"
        ) == "xCfFw3K5D8A"

    def test_invalid_url_returns_none(self):
        assert gen.extract_video_id_from_url("https://example.com/") is None


class TestExtractPlaylistId:
    def test_playlist_url(self):
        assert gen.extract_playlist_id_from_url(
            "https://www.youtube.com/playlist?list=PLY8qtbcbQHDjTzjBB4z3OYw9jGKxVio6H"
        ) == "PLY8qtbcbQHDjTzjBB4z3OYw9jGKxVio6H"

    def test_watch_url_with_list_param(self):
        assert gen.extract_playlist_id_from_url(
            "https://www.youtube.com/watch?v=xCfFw3K5D8A&list=PLxxxx&index=1"
        ) == "PLxxxx"

    def test_no_list_param_returns_none(self):
        assert gen.extract_playlist_id_from_url(
            "https://www.youtube.com/watch?v=xCfFw3K5D8A"
        ) is None


class TestTimestampToSeconds:
    def test_mm_ss(self):
        assert gen.timestamp_to_seconds("05:12") == 312

    def test_hh_mm_ss(self):
        assert gen.timestamp_to_seconds("01:05:12") == 3912

    def test_none_input(self):
        assert gen.timestamp_to_seconds(None) is None

    def test_invalid_format(self):
        assert gen.timestamp_to_seconds("not a timestamp") is None


class TestBuildSrt:
    def test_basic_conversion(self):
        segments = [
            {"start": "00:00", "end": "00:04", "text": "第一段"},
            {"start": "00:04", "end": "00:09", "text": "第二段"},
        ]
        srt = gen.build_srt(segments)
        assert "1\n00:00:00,000 --> 00:00:04,000\n第一段" in srt
        assert "2\n00:00:04,000 --> 00:00:09,000\n第二段" in srt

    def test_skips_empty_text(self):
        segments = [
            {"start": "00:00", "end": "00:04", "text": ""},
            {"start": "00:04", "end": "00:09", "text": "有內容"},
        ]
        srt = gen.build_srt(segments)
        assert srt.count("-->") == 1
        assert "有內容" in srt

    def test_missing_end_gets_fallback_duration(self):
        segments = [{"start": "00:00", "end": None, "text": "測試"}]
        srt = gen.build_srt(segments)
        assert "00:00:00,000 --> 00:00:03,000" in srt

    def test_empty_list(self):
        assert gen.build_srt([]) == ""

    def test_none_input(self):
        assert gen.build_srt(None) == ""


class TestStripJsonFences:
    def test_removes_json_fence(self):
        text = '```json\n{"a": 1}\n```'
        assert gen.strip_json_fences(text) == '{"a": 1}'

    def test_removes_plain_fence(self):
        text = '```\n{"a": 1}\n```'
        assert gen.strip_json_fences(text) == '{"a": 1}'

    def test_no_fence_unchanged(self):
        text = '{"a": 1}'
        assert gen.strip_json_fences(text) == '{"a": 1}'


class TestBuildQuizPrompt:
    def test_without_transcript_excludes_field(self):
        prompt = gen.build_quiz_prompt(with_transcript=False)
        assert '"transcript"' not in prompt
        # 確認其餘的 JSON 範例大括號沒有被誤傷
        assert "{{c1::關鍵字}}" in prompt

    def test_with_transcript_includes_field(self):
        prompt = gen.build_quiz_prompt(with_transcript=True)
        assert '"transcript"' in prompt

    def test_valid_python_string_replace_no_format_errors(self):
        # 這個測試專門防止之前發生過的 str.format() 跟JSON大括號衝突的bug再度出現
        try:
            gen.build_quiz_prompt(with_transcript=True)
            gen.build_quiz_prompt(with_transcript=False)
        except (KeyError, IndexError) as e:
            raise AssertionError(f"build_quiz_prompt 不應該因為大括號衝突而出錯: {e}")


class TestWriteVideoDataAndManifest(object):
    def test_write_video_data_creates_json(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gen, "ONLINE_STUDY_DIR", str(tmp_path))
        monkeypatch.setattr(gen, "DATA_DIR", str(tmp_path / "data"))
        os.makedirs(gen.DATA_DIR, exist_ok=True)

        quiz_data = {"subject": "測試科目", "mc_questions": [], "cloze_items": []}
        path = gen.write_video_data("VIDTEST01", "https://www.youtube.com/watch?v=VIDTEST01", quiz_data)

        assert os.path.exists(path)
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
        assert saved["video_id"] == "VIDTEST01"
        assert saved["has_transcript"] is False

    def test_write_video_data_with_transcript_creates_srt(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gen, "ONLINE_STUDY_DIR", str(tmp_path))
        monkeypatch.setattr(gen, "DATA_DIR", str(tmp_path / "data"))
        os.makedirs(gen.DATA_DIR, exist_ok=True)

        quiz_data = {
            "subject": "測試科目",
            "mc_questions": [],
            "cloze_items": [],
            "transcript": [{"start": "00:00", "end": "00:03", "text": "測試逐字稿"}],
        }
        gen.write_video_data("VIDTEST02", "https://www.youtube.com/watch?v=VIDTEST02", quiz_data)

        srt_path = os.path.join(gen.DATA_DIR, "VIDTEST02.srt")
        assert os.path.exists(srt_path)
        with open(srt_path, encoding="utf-8") as f:
            assert "測試逐字稿" in f.read()


class TestCheckRequiredAssets:
    def test_reports_all_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gen, "ONLINE_STUDY_DIR", str(tmp_path))
        missing = gen.check_required_assets()
        assert set(missing) == set(gen.REQUIRED_ASSETS)

    def test_reports_none_missing_when_present(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gen, "ONLINE_STUDY_DIR", str(tmp_path))
        for name in gen.REQUIRED_ASSETS:
            (tmp_path / name).write_text("x", encoding="utf-8")
        missing = gen.check_required_assets()
        assert missing == []
