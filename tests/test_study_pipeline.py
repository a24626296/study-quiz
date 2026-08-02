# -*- coding: utf-8 -*-
import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import study_pipeline as sp  # noqa: E402


class TestTimestampToSeconds:
    def test_mm_ss(self):
        assert sp.timestamp_to_seconds("05:12") == 312

    def test_hh_mm_ss(self):
        assert sp.timestamp_to_seconds("01:05:12") == 3912

    def test_none_input(self):
        assert sp.timestamp_to_seconds(None) is None


class TestBuildSrt:
    def test_basic_conversion(self):
        segments = [{"start": "00:00", "end": "00:04", "text": "第一段"}]
        srt = sp.build_srt(segments)
        assert "00:00:00,000 --> 00:00:04,000" in srt
        assert "第一段" in srt

    def test_skips_empty_text(self):
        segments = [{"start": "00:00", "end": "00:04", "text": "   "}]
        assert sp.build_srt(segments) == ""


class TestBuildQuizPrompt:
    def test_without_transcript_excludes_field(self):
        prompt = sp.build_quiz_prompt(with_transcript=False)
        assert '"transcript"' not in prompt
        assert "{{c1::關鍵字}}" in prompt

    def test_with_transcript_includes_field(self):
        prompt = sp.build_quiz_prompt(with_transcript=True)
        assert '"transcript"' in prompt


class TestParseSelection:
    def test_single_numbers(self):
        assert sp.parse_selection("1,3,5", 10) == [1, 3, 5]

    def test_range(self):
        assert sp.parse_selection("5-7", 10) == [5, 6, 7]

    def test_mixed(self):
        assert sp.parse_selection("1,3,5-7,10", 10) == [1, 3, 5, 6, 7, 10]

    def test_out_of_range_filtered(self):
        assert sp.parse_selection("1,99", 10) == [1]

    def test_dedup_and_sort(self):
        assert sp.parse_selection("3,1,3,2", 10) == [1, 2, 3]

    def test_invalid_tokens_ignored(self):
        assert sp.parse_selection("1,abc,3", 10) == [1, 3]


class TestSanitizeFilename:
    def test_replaces_fullwidth_chars(self):
        result = sp.sanitize_filename("Video：Title｜Test")
        assert "：" not in result
        assert "｜" not in result

    def test_strips_non_ascii(self):
        result = sp.sanitize_filename("中文標題test")
        assert result.replace("_", "").isascii()


class TestExtractYoutubeId:
    def test_finds_id_in_brackets(self):
        assert sp.extract_youtube_id("Some Video [dQw4w9WgXcQ].webm") == "dQw4w9WgXcQ"

    def test_no_id_returns_none(self):
        assert sp.extract_youtube_id("Some Video.webm") is None


class TestWriteVideoDataAndAssets:
    def test_write_video_data_creates_json(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sp, "ONLINE_STUDY_DIR", str(tmp_path))
        monkeypatch.setattr(sp, "DATA_DIR", str(tmp_path / "data"))
        os.makedirs(sp.DATA_DIR, exist_ok=True)

        quiz_data = {"subject": "測試科目", "mc_questions": [], "cloze_items": []}
        path = sp.write_video_data("VIDTEST01", "https://www.youtube.com/watch?v=VIDTEST01", quiz_data)

        assert os.path.exists(path)
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
        assert saved["video_id"] == "VIDTEST01"

    def test_check_required_assets_detects_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sp, "ONLINE_STUDY_DIR", str(tmp_path))
        missing = sp.check_required_assets()
        assert set(missing) == set(sp.REQUIRED_ASSETS)
