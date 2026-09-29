import os
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

from bot import (
    DEFAULT_CHANNEL_IDS,
    DEFAULT_SAMPLE_SECONDS,
    DEFAULT_TELEGRAM_CHAT_ID,
    FALLBACK_SAMPLE_SECONDS,
    Caption,
    Finding,
    announce,
    caption_context,
    extract_codes,
    format_timestamp,
    latest_video,
    mark_channel_initialized,
    new_upload_after,
    send_notification,
    timestamp_seconds,
    is_authorized_telegram_message,
    is_plausible_code,
    telegram_command,
    transcript_event_windows,
)


class BotHelpersTests(unittest.TestCase):
    def test_default_sample_interval_reduces_ocr_workload(self):
        self.assertEqual(DEFAULT_SAMPLE_SECONDS, 1)
        self.assertEqual(FALLBACK_SAMPLE_SECONDS, 5)

    def test_default_channels_include_both_mat_armstrong_channels(self):
        self.assertEqual(
            DEFAULT_CHANNEL_IDS,
            ("UC2FZQeaJkpnO6IMkQglvPsg", "UCcLYOTz3ct6_lk9iLToxxAw"),
        )

    @patch("bot.urlopen")
    def test_notification_uses_default_chat_id_without_environment_override(self, mock_urlopen):
        mock_urlopen.return_value.__enter__.return_value.status = 200
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test-token"}, clear=True):
            send_notification("Test message")

        request = mock_urlopen.call_args.args[0]
        payload = parse_qs(request.data.decode("utf-8"))
        self.assertEqual(DEFAULT_TELEGRAM_CHAT_ID, "5594740512")
        self.assertEqual(payload["chat_id"], [DEFAULT_TELEGRAM_CHAT_ID])

    @patch("bot.send_notification")
    def test_announcements_keep_timestamp_without_youtube_link(self, mock_send):
        announce("MK2 test video", "video123", [Finding("HWBT123", 61.8, 2)])
        message = mock_send.call_args.args[0]
        self.assertIn("Timestamp: 00:01:01", message)
        self.assertNotIn("youtube.com", message)

    def test_telegram_commands_allow_bot_suffix(self):
        self.assertEqual(telegram_command("/scan@VoucherMonitorBot"), "/scan")
        self.assertEqual(telegram_command("hello"), None)

    def test_telegram_listener_only_accepts_configured_private_chat(self):
        message = {
            "from": {"id": 5594740512},
            "chat": {"id": 5594740512},
        }
        self.assertTrue(is_authorized_telegram_message(message, "5594740512"))
        message["from"]["id"] = 1234
        self.assertFalse(is_authorized_telegram_message(message, "5594740512"))

    def test_latest_video_uses_published_time_not_feed_position(self):
        videos = [
            {"id": "older", "title": "Older", "published": "2026-09-01T12:00:00+00:00"},
            {"id": "newer", "title": "Newer", "published": "2026-09-08T12:00:00+00:00"},
        ]
        self.assertEqual(latest_video(videos)["id"], "newer")

    def test_latest_video_requires_nonempty_feed(self):
        with self.assertRaisesRegex(RuntimeError, "No videos"):
            latest_video([])

    def test_wait_ignores_baseline_and_detects_a_new_latest_video(self):
        baseline = {
            "id": "current",
            "title": "EUROPE BANNED MY FERRARI I REBUILT",
            "published": "2026-09-27T17:00:00+00:00",
        }
        newer = {
            "id": "next",
            "title": "New MK2 upload",
            "published": "2026-10-04T17:00:00+00:00",
        }

        self.assertIsNone(new_upload_after("current", [baseline]))
        self.assertEqual(new_upload_after("current", [newer, baseline]), newer)
        self.assertIsNone(new_upload_after("current", []))

    def test_first_run_skips_backlog_but_keeps_latest_unprocessed(self):
        videos = [
            {"id": "older", "title": "Older", "published": "2026-09-01T12:00:00+00:00"},
            {"id": "newer", "title": "Newer", "published": "2026-09-08T12:00:00+00:00"},
        ]
        processed = set()
        initialized_channels = set()

        self.assertTrue(
            mark_channel_initialized("UC_CHANNEL", videos, processed, initialized_channels)
        )
        self.assertSetEqual(processed, {"older"})
        self.assertSetEqual(initialized_channels, {"UC_CHANNEL"})
        self.assertFalse(
            mark_channel_initialized("UC_CHANNEL", videos, processed, initialized_channels)
        )

    def test_timestamp_round_trip(self):
        self.assertEqual(timestamp_seconds("00:01:02.500"), 62.5)
        self.assertEqual(format_timestamp(3661.9), "01:01:01")

    def test_extracts_code_shaped_text_and_ignores_common_labels(self):
        self.assertEqual(extract_codes("£100 GIVEAWAY: MAT2K25 CODE"), {"MAT2K25"})

    def test_candidate_filter_rejects_noise_and_allows_screen_codes(self):
        captions = [Caption(100, 102, "An Amazon discount code is on the screen.")]
        self.assertTrue(is_plausible_code("WBT296", [], 120))
        self.assertTrue(is_plausible_code("HWBTSPY", captions, 108))
        self.assertFalse(is_plausible_code("DIMENSIONS", [], 100))
        self.assertFalse(is_plausible_code("HWBTSPY", [], 100))

    def test_caption_context_uses_nearby_relevant_cue(self):
        captions = [Caption(60, 63, "We have to open the bonnet for this one")]
        self.assertEqual(caption_context(captions, 65), "We have to open the bonnet for this one")
        self.assertEqual(caption_context(captions, 120), "")

    def test_transcript_rule_targets_later_matching_event(self):
        captions = [
            Caption(10, 12, "Every time we open the bonnet"),
            Caption(12, 14, "we give away a voucher code worth £100."),
            Caption(35, 38, "Let's open the bonnet again."),
        ]

        windows, rule = transcript_event_windows(captions)

        self.assertIn("Recurring giveaway rule", rule)
        self.assertIn("bonnet/hood", rule)
        self.assertEqual(len(windows), 2)
        self.assertEqual((windows[0].start, windows[0].end), (4, 26))
        self.assertEqual((windows[1].start, windows[1].end), (29, 50))
        self.assertIn("bonnet/hood", windows[0].labels)

    def test_code_screen_announcement_creates_window_without_rule(self):
        captions = [Caption(100, 102, "On screen now is an Amazon discount code.")]

        windows, summary = transcript_event_windows(captions)

        self.assertEqual((windows[0].start, windows[0].end), (94, 114))
        self.assertEqual(windows[0].labels, ("code-on-screen",))
        self.assertEqual(summary, "Transcript announces a code or voucher on screen.")

    def test_prize_value_without_rule_creates_window(self):
        captions = [Caption(100, 102, "Another £100 for the winner.")]
        windows, summary = transcript_event_windows(captions)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].labels, ("prize-mention",))
        self.assertEqual(summary, "Transcript contains prize or value mentions.")

    def test_plural_gift_voucher_caption_is_targeted(self):
        captions = [Caption(100, 102, "The gift vouchers are on the screen.")]

        windows, _ = transcript_event_windows(captions)

        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].labels, ("code-on-screen", "prize-mention"))

    def test_transcript_without_giveaway_rule_falls_back(self):
        captions = [Caption(35, 38, "Let's open the bonnet again.")]
        self.assertEqual(transcript_event_windows(captions), ([], ""))


if __name__ == "__main__":
    unittest.main()