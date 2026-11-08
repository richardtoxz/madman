from __future__ import annotations

import unittest
from types import SimpleNamespace

from worker.tracker.card import activity_items, build_card, device_items, guild_tag, name_style, profile_visuals
from worker.tracker.manager import TrackerManager
from worker.tracker.support import CustomStatus

import discord  # noqa: E402  (worker/__init__ prepara o caminho da biblioteca)


def _client(user=None, channel=None):
    return SimpleNamespace(
        get_user=lambda _id: user,
        get_channel=lambda _id: channel,
    )


def _tracker(client, **state):
    tracker = TrackerManager(client)
    tracker.enabled = True
    tracker.target_id = 123456789012345678
    for key, value in state.items():
        setattr(tracker.state, key, value)
    return tracker


class TrackerCardTests(unittest.TestCase):
    def test_activity_items_skip_custom_status_and_clip_text(self):
        playing = SimpleNamespace(type=SimpleNamespace(name="playing"), name="Jogo " + "x" * 200, details="Partida")
        custom = discord.CustomActivity(name="oi")
        items = activity_items(SimpleNamespace(activities=[custom, playing]))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "playing")
        self.assertEqual(len(items[0]["name"]), 100)
        self.assertEqual(items[0]["detail"], "Partida")

    def test_detected_game_and_rich_presence_merge_into_the_richest_one(self):
        plain = SimpleNamespace(
            type=SimpleNamespace(name="playing"),
            name="League of Legends",
            details=None,
            state=None,
            assets=None,
            timestamps=SimpleNamespace(start=None, end=None),
            party=None,
        )
        rich = SimpleNamespace(
            type=SimpleNamespace(name="playing"),
            name="League of Legends",
            details="Ranqueada Flex",
            state=None,
            assets=SimpleNamespace(large_image=SimpleNamespace(url="https://cdn.discordapp.com/x.png")),
            timestamps=SimpleNamespace(start=None, end=None),
            party=None,
        )
        for order in ([plain, rich], [rich, plain]):
            items = activity_items(SimpleNamespace(activities=order))
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["detail"], "Ranqueada Flex")
            self.assertEqual(items[0]["image_url"], "https://cdn.discordapp.com/x.png")

    def test_different_games_are_kept(self):
        make = lambda name: SimpleNamespace(
            type=SimpleNamespace(name="playing"),
            name=name,
            details=None,
            state=None,
            assets=None,
            timestamps=SimpleNamespace(start=None, end=None),
            party=None,
        )
        items = activity_items(SimpleNamespace(activities=[make("Valorant"), make("Spotify Party")]))
        self.assertEqual([item["name"] for item in items], ["Valorant", "Spotify Party"])

    def test_unknown_activity_type_is_other(self):
        item = SimpleNamespace(type=SimpleNamespace(name="hang"), name="Sala", details=None)
        self.assertEqual(activity_items(SimpleNamespace(activities=[item]))[0]["type"], "other")

    def test_device_items_ignore_offline(self):
        subject = SimpleNamespace(
            desktop_status="online", mobile_status="offline", web_status="idle", embedded_status=None
        )
        self.assertEqual(
            device_items(subject),
            ({"device": "desktop", "status": "online"}, {"device": "web", "status": "idle"}),
        )

    def test_card_is_none_while_stopped(self):
        tracker = TrackerManager(_client())
        self.assertIsNone(build_card(tracker))

    def test_card_uses_latest_snapshot_even_after_ttl(self):
        user = SimpleNamespace(
            name="alvaro",
            global_name="Alvaro",
            display_avatar=SimpleNamespace(
                with_size=lambda _n: SimpleNamespace(url="https://cdn.discordapp.com/a.png")
            ),
        )
        channel = SimpleNamespace(name="Geral", guild=SimpleNamespace(name="Servidor"))
        tracker = _tracker(_client(user, channel), in_voice_channel_id=42)
        tracker.state.guild_presence = {
            1: {
                "status": "offline",
                "updated_at": 10.0,
                "custom_known": False,
                "activity_items": (),
                "device_items": (),
            },
            2: {
                "status": "dnd",
                "updated_at": 20.0,
                "custom_known": True,
                "custom_status": CustomStatus("focado", "custom:9", "<:x:9>"),
                "activity_items": ({"type": "playing", "name": "Jogo", "detail": ""},),
                "device_items": ({"device": "desktop", "status": "dnd"},),
            },
        }
        card = build_card(tracker)
        self.assertEqual(card["id"], 123456789012345678)
        self.assertEqual(card["username"], "alvaro")
        self.assertEqual(card["display_name"], "Alvaro")
        self.assertEqual(card["avatar_url"], "https://cdn.discordapp.com/a.png")
        self.assertEqual(card["status"], "dnd")
        self.assertEqual(card["custom_status"], {"text": "focado", "emoji": ""})
        self.assertEqual(card["activities"][0]["name"], "Jogo")
        self.assertEqual(card["voice"], {"channel": "Geral", "guild": "Servidor"})

    def test_card_without_cached_user_or_presence_is_unknown(self):
        card = build_card(_tracker(_client()))
        self.assertEqual(card["status"], "unknown")
        self.assertIsNone(card["username"])
        self.assertEqual(card["activities"], [])
        self.assertIsNone(card["voice"])

    def test_empty_custom_status_is_dropped(self):
        tracker = _tracker(_client(), custom_status=CustomStatus("", "", ""))
        self.assertIsNone(build_card(tracker)["custom_status"])

    def test_identity_falls_back_to_fetched_profile_when_user_is_not_cached(self):
        tracker = _tracker(_client())
        tracker.state.profile_card = {
            "username": "kim_shay",
            "display_name": "Kim Shay",
            "avatar_url": "https://cdn.discordapp.com/a.png",
            "guild_tag": "HU3",
        }
        card = build_card(tracker)
        self.assertEqual((card["username"], card["display_name"], card["guild_tag"]), ("kim_shay", "Kim Shay", "HU3"))
        self.assertEqual(card["avatar_url"], "https://cdn.discordapp.com/a.png")

    def test_invisible_maps_to_offline(self):
        tracker = _tracker(_client(), status="invisible")
        self.assertEqual(build_card(tracker)["status"], "offline")

    def test_profile_visuals_collects_theme_banner_badges_and_pronouns(self):
        colour = lambda value: SimpleNamespace(value=value)
        profile = SimpleNamespace(
            banner=SimpleNamespace(with_size=lambda _n: SimpleNamespace(url="https://cdn.discordapp.com/b.png")),
            accent_colour=colour(0xFF66AA),
            bio="oi " * 200,
            metadata=SimpleNamespace(theme_colors=(colour(0x331122), colour(0xCC4488)), pronouns="ela/dela"),
            badges=[SimpleNamespace(description="Ativo", url="https://cdn.discordapp.com/badge-icons/x.png")],
        )
        visuals = profile_visuals(profile)
        self.assertEqual(visuals["banner_url"], "https://cdn.discordapp.com/b.png")
        self.assertEqual(visuals["accent"], "#ff66aa")
        self.assertEqual(visuals["theme"], ["#331122", "#cc4488"])
        self.assertEqual(visuals["pronouns"], "ela/dela")
        self.assertEqual(len(visuals["bio"]), 220)
        self.assertEqual(
            visuals["badges"], [{"description": "Ativo", "url": "https://cdn.discordapp.com/badge-icons/x.png"}]
        )

    def test_profile_visuals_tolerates_empty_profile(self):
        visuals = profile_visuals(SimpleNamespace())
        self.assertIsNone(visuals["banner_url"])
        self.assertIsNone(visuals["theme"])
        self.assertEqual(visuals["badges"], [])

    def test_name_style_and_guild_tag(self):
        colour = lambda value: SimpleNamespace(value=value)
        user = SimpleNamespace(
            display_name_style=SimpleNamespace(
                effect=SimpleNamespace(name="gradient"), colors=[colour(0xFF0000), colour(0x00FF00)]
            ),
            primary_guild=SimpleNamespace(identity_enabled=True, tag="HU3"),
        )
        self.assertEqual(name_style(user), {"effect": "gradient", "colors": ["#ff0000", "#00ff00"]})
        self.assertEqual(guild_tag(user), "HU3")
        self.assertIsNone(name_style(SimpleNamespace(display_name_style=None)))
        self.assertIsNone(guild_tag(SimpleNamespace(primary_guild=SimpleNamespace(identity_enabled=False, tag="X"))))

    def test_activity_items_include_art_timer_and_party(self):
        from datetime import datetime, timezone

        started = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
        game = SimpleNamespace(
            type=SimpleNamespace(name="playing"),
            name="League of Legends",
            details="Ranqueada Flex",
            state="Em partida",
            assets=SimpleNamespace(large_image=SimpleNamespace(url="https://media.discordapp.net/x.png")),
            timestamps=SimpleNamespace(start=started, end=None),
            party=SimpleNamespace(current_size=2, max_size=5),
        )
        item = activity_items(SimpleNamespace(activities=[game]))[0]
        self.assertEqual(item["image_url"], "https://media.discordapp.net/x.png")
        self.assertEqual(item["started_at"], int(started.timestamp() * 1000))
        self.assertEqual(item["party"], "2 de 5")
        self.assertEqual(item["state"], "Em partida")


if __name__ == "__main__":
    unittest.main()
