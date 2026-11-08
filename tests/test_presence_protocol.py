from __future__ import annotations

import unittest

from presence_protocol import (
    ACTIVITY_TYPES,
    PLATFORMS,
    STATUS_DISPLAYS,
    TIMERS,
    validate_custom,
    validate_rich_presence_payload,
)


class ValidateCustomTests(unittest.TestCase):
    def test_minimal_custom_fills_defaults(self):
        custom = validate_custom({"type": "playing", "name": "X"})
        self.assertEqual(
            custom,
            {
                "type": "playing",
                "name": "X",
                "details": None,
                "state": None,
                "timer": "none",
                "large_image": None,
                "large_text": None,
                "small_image": None,
                "small_text": None,
                "buttons": [],
                "status_display": "name",
                "details_url": None,
                "state_url": None,
                "large_url": None,
                "small_url": None,
                "stream_url": None,
                "duration_minutes": None,
                "party_current": None,
                "party_max": None,
                "platform": None,
            },
        )

    def test_known_types_and_timers(self):
        self.assertEqual(ACTIVITY_TYPES, ("playing", "streaming", "listening", "watching", "competing"))
        self.assertEqual(TIMERS, ("none", "since_start", "since_save", "duration"))
        self.assertEqual(STATUS_DISPLAYS, ("name", "state", "details"))
        self.assertEqual(PLATFORMS, ("desktop", "xbox", "ps4", "ps5", "android", "ios"))

    def test_strips_and_empties_become_none(self):
        custom = validate_custom({"type": "watching", "name": "  Filme  ", "details": "   ", "state": " fim "})
        self.assertEqual(custom["name"], "Filme")
        self.assertIsNone(custom["details"])
        self.assertEqual(custom["state"], "fim")

    def test_rejects_non_dict_and_unknown_key(self):
        with self.assertRaises(ValueError):
            validate_custom("x")
        with self.assertRaises(ValueError):
            validate_custom({"type": "playing", "name": "X", "application_id": 1})

    def test_rejects_bad_type_and_timer(self):
        with self.assertRaises(ValueError):
            validate_custom({"type": "hang", "name": "X"})
        with self.assertRaises(ValueError):
            validate_custom({"type": "playing", "name": "X", "timer": "forever"})

    def test_name_required_and_length(self):
        with self.assertRaises(ValueError):
            validate_custom({"type": "playing", "name": "   "})
        with self.assertRaises(ValueError):
            validate_custom({"type": "playing", "name": 5})
        with self.assertRaises(ValueError):
            validate_custom({"type": "playing", "name": "a" * 129})
        self.assertEqual(validate_custom({"type": "playing", "name": "a" * 128})["name"], "a" * 128)

    def test_text_fields_length(self):
        with self.assertRaises(ValueError):
            validate_custom({"type": "playing", "name": "X", "details": "d" * 129})

    def test_urls_must_be_https(self):
        for url in ("http://a.com/x.png", "javascript:alert(1)", "https://" + "a" * 505):
            with self.subTest(url=url[:30]), self.assertRaises(ValueError):
                validate_custom({"type": "playing", "name": "X", "large_image": url})
        ok = validate_custom({"type": "playing", "name": "X", "large_image": "https://a.com/x.png"})
        self.assertEqual(ok["large_image"], "https://a.com/x.png")

    def test_buttons_limits(self):
        button = {"label": "Site", "url": "https://a.com"}
        self.assertEqual(
            validate_custom({"type": "playing", "name": "X", "buttons": [button, button]})["buttons"],
            [button, button],
        )
        bad = [
            [button, button, button],
            [{"label": "b" * 33, "url": "https://a.com"}],
            [{"label": " ", "url": "https://a.com"}],
            [{"label": "ok", "url": "http://a.com"}],
            [{"label": "ok", "url": "https://a.com", "extra": 1}],
            "x",
        ]
        for buttons in bad:
            with self.subTest(buttons=buttons), self.assertRaises(ValueError):
                validate_custom({"type": "playing", "name": "X", "buttons": buttons})

    def test_texts_without_image_are_dropped(self):
        custom = validate_custom({"type": "playing", "name": "X", "large_text": "oi", "small_text": "ola"})
        self.assertIsNone(custom["large_text"])
        self.assertIsNone(custom["small_text"])


class NewFieldsTests(unittest.TestCase):
    BASE = {"type": "watching", "name": "X"}

    def _ok(self, **extra):
        return validate_custom({**self.BASE, **extra})

    def _bad(self, **extra):
        with self.assertRaises(ValueError):
            validate_custom({**self.BASE, **extra})

    def test_status_display(self):
        self.assertEqual(self._ok(status_display="details")["status_display"], "details")
        self._bad(status_display="emoji")

    def test_text_and_image_links(self):
        custom = self._ok(details="D", details_url="https://a.com/d", state="S", state_url="https://a.com/s")
        self.assertEqual((custom["details_url"], custom["state_url"]), ("https://a.com/d", "https://a.com/s"))
        self._bad(details_url="http://a.com")
        self._bad(state_url="https://" + "a" * 250)
        dropped = self._ok(details_url="https://a.com/d", large_url="https://a.com/l")
        self.assertIsNone(dropped["details_url"])
        self.assertIsNone(dropped["large_url"])
        with_image = self._ok(large_image="https://a.com/x.png", large_url="https://a.com/l")
        self.assertEqual(with_image["large_url"], "https://a.com/l")

    def test_streaming_requires_twitch_or_youtube(self):
        custom = validate_custom({"type": "streaming", "name": "Live", "stream_url": "https://www.twitch.tv/canal"})
        self.assertEqual(custom["stream_url"], "https://www.twitch.tv/canal")
        validate_custom({"type": "streaming", "name": "Live", "stream_url": "https://youtube.com/watch?v=1"})
        for url in (None, "https://example.com/live", "http://twitch.tv/x", "https://twitch.tv.evil.com/x"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_custom({"type": "streaming", "name": "Live", "stream_url": url})
        self.assertIsNone(self._ok(stream_url="https://twitch.tv/x")["stream_url"])

    def test_duration_timer(self):
        custom = self._ok(timer="duration", duration_minutes=90)
        self.assertEqual(custom["duration_minutes"], 90)
        for minutes in (None, 0, 1441, "30", True, 1.5):
            with self.subTest(minutes=minutes):
                self._bad(timer="duration", duration_minutes=minutes)
        self.assertIsNone(self._ok(timer="none", duration_minutes=30)["duration_minutes"])

    def test_party(self):
        custom = self._ok(party_current=2, party_max=4)
        self.assertEqual((custom["party_current"], custom["party_max"]), (2, 4))
        for current, maximum in ((2, None), (None, 4), (5, 4), (0, 4), (1, 100), ("1", 2), (True, 2)):
            with self.subTest(current=current, maximum=maximum):
                self._bad(party_current=current, party_max=maximum)

    def test_platform(self):
        self.assertEqual(self._ok(platform="ps5")["platform"], "ps5")
        self.assertIsNone(self._ok(platform=None)["platform"])
        self._bad(platform="nintendo")


class ValidatePayloadTests(unittest.TestCase):
    def test_payload_requires_bool_enabled(self):
        self.assertEqual(validate_rich_presence_payload({"enabled": "x"})[0], False)
        self.assertEqual(validate_rich_presence_payload({})[0], False)
        self.assertEqual(validate_rich_presence_payload("x")[0], False)

    def test_payload_accepts_null_custom(self):
        ok, payload = validate_rich_presence_payload({"enabled": False, "custom": None})
        self.assertTrue(ok)
        self.assertEqual(payload, {"enabled": False, "custom": None})
        ok, payload = validate_rich_presence_payload({"enabled": True})
        self.assertEqual(payload, {"enabled": True, "custom": None})

    def test_payload_validates_custom(self):
        ok, payload = validate_rich_presence_payload({"enabled": True, "custom": {"type": "playing", "name": "X"}})
        self.assertTrue(ok)
        self.assertEqual(payload["custom"]["name"], "X")
        ok, error = validate_rich_presence_payload({"enabled": True, "custom": {"type": "x", "name": "X"}})
        self.assertFalse(ok)
        self.assertEqual(error["code"], "INVALID_RICH_PRESENCE")
        self.assertIn("Tipo", error["message"])

    def test_payload_rejects_unknown_key(self):
        self.assertFalse(validate_rich_presence_payload({"enabled": True, "x": 1})[0])


if __name__ == "__main__":
    unittest.main()
