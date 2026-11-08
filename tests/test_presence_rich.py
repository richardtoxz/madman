from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import worker  # noqa: F401  (põe worker/lib no sys.path)
import discord

from worker.presence.manager import PresenceManager


def _client(custom_status=None, proxy=None):
    async def default_proxy(application_id, *urls):
        return [f"https://media.discordapp.net/external/{abs(hash(url)) % 1000}/x.png" for url in urls]

    return SimpleNamespace(
        settings=SimpleNamespace(custom_activity=custom_status),
        change_presence=AsyncMock(),
        http=SimpleNamespace(get_app_assets=AsyncMock(return_value=[{"name": "madman_logo", "id": 77}])),
        proxy_external_application_assets=AsyncMock(side_effect=proxy or default_proxy),
    )


CUSTOM = {
    "type": "watching",
    "name": "Filmes",
    "details": "Interestelar",
    "state": "Parte 2",
    "timer": "since_save",
    "large_image": "https://img.example/big.png",
    "large_text": "Poster",
    "buttons": [{"label": "Site", "url": "https://example.com"}],
}


def _activities(client):
    return client.change_presence.await_args.kwargs["activities"]


class RichPresenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_unchanged(self):
        client = _client()
        presence = PresenceManager(client)
        await presence.set_main()
        activity = _activities(client)[0]
        self.assertEqual(activity.name, "Madman")
        self.assertEqual(activity.details, "Madman Services")
        status = presence.status()
        self.assertTrue(status["enabled"])
        self.assertIsNone(status["custom"])
        self.assertEqual(status["live"]["source"], "default")
        self.assertEqual(
            status["live"]["large_image"], f"https://cdn.discordapp.com/app-assets/{presence.config.APP_ID}/77.png"
        )

    async def test_custom_replaces_main(self):
        client = _client()
        presence = PresenceManager(client)
        result = await presence.configure(True, CUSTOM)
        self.assertTrue(result["ok"])
        activity = _activities(client)[0]
        self.assertEqual(activity.type, discord.ActivityType.watching)
        self.assertEqual(activity.name, "Filmes")
        self.assertEqual(activity.details, "Interestelar")
        self.assertEqual(activity.state, "Parte 2")
        self.assertTrue(activity.assets.to_dict()["large_image"].startswith("mp:external/"))
        self.assertEqual(activity.assets.large_text, "Poster")
        self.assertEqual(activity.to_dict()["buttons"], ["Site"])
        self.assertEqual(activity.to_dict()["metadata"], {"button_urls": ["https://example.com"]})
        self.assertIsNotNone(activity.start)
        self.assertEqual(result["live"]["source"], "custom")
        self.assertTrue(result["live"]["large_image"].startswith("https://media.discordapp.net/external/"))
        self.assertEqual(result["custom"]["large_image"], "https://img.example/big.png")
        self.assertEqual(result["custom"]["name"], "Filmes")

    async def test_disabled_sends_only_custom_status(self):
        status = discord.CustomActivity("estudando")
        client = _client(status)
        presence = PresenceManager(client)
        result = await presence.configure(False, None)
        self.assertEqual(_activities(client), [status])
        self.assertIsNone(result["live"])
        self.assertFalse(result["enabled"])

        client = _client()
        presence = PresenceManager(client)
        await presence.configure(False, CUSTOM)
        self.assertEqual(_activities(client), [])

    async def test_disabled_task_does_not_publish(self):
        client = _client()
        presence = PresenceManager(client)
        await presence.configure(False, None)
        client.change_presence.reset_mock()
        token = await presence.set_task("Limpando", "DM")
        await presence.update_detail("50%")
        await presence.complete_task(token)
        await presence.set_protection(True)
        client.change_presence.assert_not_awaited()

    async def test_task_overlays_custom_and_restores(self):
        client = _client()
        presence = PresenceManager(client)
        await presence.configure(True, CUSTOM)
        token = await presence.set_task("Limpando", "DM")
        self.assertEqual(_activities(client)[0].details, "Limpando")
        self.assertEqual(presence.status()["live"]["source"], "task")
        self.assertEqual(presence.status()["task"], "Limpando")
        await presence.complete_task(token)
        self.assertEqual(_activities(client)[0].name, "Filmes")
        self.assertIsNone(presence.status()["task"])

    async def test_enable_during_task_shows_task(self):
        client = _client()
        presence = PresenceManager(client)
        await presence.configure(False, None)
        await presence.set_task("Quest", "video")
        result = await presence.configure(True, CUSTOM)
        self.assertEqual(_activities(client)[0].details, "Quest")
        self.assertEqual(result["live"]["source"], "task")

    async def test_proxy_failure_publishes_without_image_and_warns(self):
        async def failing(application_id, *urls):
            raise RuntimeError("boom")

        client = _client(proxy=failing)
        presence = PresenceManager(client)
        result = await presence.configure(True, CUSTOM)
        activity = _activities(client)[0]
        self.assertNotIn("large_image", activity.assets.to_dict())
        self.assertEqual(activity.name, "Filmes")
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("Imagem grande", result["warnings"][0])
        self.assertIsNone(result["live"]["large_image"])
        self.assertIsNone(result["live"]["large_text"])

    async def test_proxy_is_cached_per_url(self):
        client = _client()
        presence = PresenceManager(client)
        await presence.configure(True, CUSTOM)
        await presence.configure(True, CUSTOM)
        await presence.refresh()
        self.assertEqual(client.proxy_external_application_assets.await_count, 1)

    async def test_status_live_reflects_source(self):
        client = _client()
        presence = PresenceManager(client)
        await presence.set_main()
        self.assertEqual(presence.status()["live"]["source"], "default")
        self.assertEqual(presence.status()["default"]["name"], "Madman")
        await presence.configure(True, CUSTOM)
        self.assertEqual(presence.status()["live"]["source"], "custom")
        await presence.configure(False, CUSTOM)
        self.assertIsNone(presence.status()["live"])
        self.assertEqual(presence.status()["custom"]["name"], "Filmes")

    async def test_timer_modes(self):
        client = _client()
        presence = PresenceManager(client)
        await presence.configure(True, {**CUSTOM, "timer": "none"})
        self.assertIsNone(_activities(client)[0].start)
        self.assertIsNone(presence.status()["live"]["started_at_ms"])
        await presence.configure(True, {**CUSTOM, "timer": "since_start"})
        self.assertEqual(presence.status()["live"]["started_at_ms"], int(presence._login_time.timestamp() * 1000))

    async def test_invalid_custom_raises_value_error(self):
        presence = PresenceManager(_client())
        with self.assertRaises(ValueError):
            await presence.configure(True, {"type": "x", "name": "y"})

    async def test_restore_default_clears_custom(self):
        client = _client()
        presence = PresenceManager(client)
        await presence.configure(True, CUSTOM)
        result = await presence.configure(True, None)
        self.assertIsNone(result["custom"])
        self.assertEqual(_activities(client)[0].name, "Madman")


class RichPresenceReviewFixTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_proxy_times_out_and_publishes_without_image(self):
        async def hanging(application_id, *urls):
            await asyncio.sleep(3600)

        client = _client(proxy=hanging)
        presence = PresenceManager(client)
        presence.PROXY_TIMEOUT = 0.05
        result = await asyncio.wait_for(presence.configure(True, CUSTOM), timeout=2)
        self.assertEqual(_activities(client)[0].name, "Filmes")
        self.assertNotIn("large_image", _activities(client)[0].assets.to_dict())
        self.assertIn("Imagem grande", result["warnings"][0])

    async def test_failed_proxy_is_not_retried_until_next_save(self):
        calls = []

        async def failing(application_id, *urls):
            calls.append(urls)
            raise RuntimeError("boom")

        client = _client(proxy=failing)
        presence = PresenceManager(client)
        await presence.configure(True, CUSTOM)
        token = await presence.set_task("Limpando")
        await presence.complete_task(token)
        await presence.set_protection(True)
        await presence.refresh()
        self.assertEqual(len(calls), 1)
        result = await presence.configure(True, CUSTOM)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(result["warnings"]), 1)

    async def test_seed_state_has_no_protection_suffix(self):
        presence = PresenceManager(_client())
        await presence.set_protection(True)
        status = presence.status()
        self.assertIn("Protecao Ativa", status["default"]["state"])
        self.assertEqual(
            status["seed"],
            {
                "type": "playing",
                "name": "Madman",
                "details": "Madman Services",
                "state": "aylo.me/to",
                "timer": "since_start",
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


class RichPresenceNewFieldsTests(unittest.IsolatedAsyncioTestCase):
    async def test_payload_carries_new_fields(self):
        client = _client()
        presence = PresenceManager(client)
        custom = {
            **CUSTOM,
            "status_display": "details",
            "details_url": "https://a.com/d",
            "state_url": "https://a.com/s",
            "large_url": "https://a.com/l",
            "party_current": 2,
            "party_max": 4,
            "platform": "ps5",
            "timer": "duration",
            "duration_minutes": 90,
        }
        result = await presence.configure(True, custom)
        payload = _activities(client)[0].to_dict()
        self.assertEqual(payload["status_display_type"], 2)
        self.assertEqual(payload["details_url"], "https://a.com/d")
        self.assertEqual(payload["state_url"], "https://a.com/s")
        self.assertEqual(payload["assets"]["large_url"], "https://a.com/l")
        self.assertEqual(payload["party"], {"size": [2, 4]})
        self.assertEqual(payload["platform"], "ps5")
        start, end = int(payload["timestamps"]["start"]), int(payload["timestamps"]["end"])
        self.assertEqual(end - start, 90 * 60 * 1000)
        live = result["live"]
        self.assertEqual(live["status_display"], "details")
        self.assertEqual(live["party_current"], 2)
        self.assertEqual(live["ends_at_ms"] - live["started_at_ms"], 90 * 60 * 1000)
        self.assertEqual(live["large_url"], "https://a.com/l")

    async def test_streaming_sends_url(self):
        client = _client()
        presence = PresenceManager(client)
        await presence.configure(True, {"type": "streaming", "name": "Live", "stream_url": "https://twitch.tv/canal"})
        payload = _activities(client)[0].to_dict()
        self.assertEqual(payload["type"], 1)
        self.assertEqual(payload["url"], "https://twitch.tv/canal")

    async def test_defaults_send_no_new_fields(self):
        client = _client()
        presence = PresenceManager(client)
        await presence.configure(True, {"type": "playing", "name": "X"})
        payload = _activities(client)[0].to_dict()
        for key in ("status_display_type", "details_url", "state_url", "party", "platform", "url", "timestamps"):
            self.assertNotIn(key, payload)


if __name__ == "__main__":
    unittest.main()
