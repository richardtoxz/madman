from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import unittest

# Painel e worker têm pacotes discord diferentes: o painel roda em subprocesso.
_CODE = textwrap.dedent("""
    import asyncio
    import sys
    from types import SimpleNamespace as NS
    from unittest.mock import AsyncMock

    from panel.presence.handlers import handle_rich_presence
    from panel.presence.modal import (
        _NEW_FIELD_DEFAULTS,
        RichButtonsModal,
        RichImagesModal,
        RichPresenceView,
        RichExtrasModal,
        RichLinksModal,
        RichTextsModal,
        build_rich_presence_embed,
    )

    DEFAULT = {"source": "default", "type": "playing", "name": "Madman", "details": "Madman Services",
               "state": "aylo.me/to", "large_image": None, "large_text": None, "small_image": None,
               "small_text": None, "buttons": [], "started_at_ms": 1}
    CUSTOM = {"type": "watching", "name": "Filmes", "details": "Duna", "state": None, "timer": "none",
              "large_image": None, "large_text": None, "small_image": None, "small_text": None, "buttons": []}
    STATUS = {"ok": True, "enabled": True, "custom": CUSTOM, "default": DEFAULT,
              "live": {**DEFAULT, **CUSTOM, "source": "custom"}, "task": None}

    def interaction():
        return NS(
            user=NS(id=123),
            response=NS(defer=AsyncMock(), send_message=AsyncMock(), send_modal=AsyncMock()),
            followup=NS(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )

    def manager(result=None, authenticated=True):
        return NS(
            is_authenticated=lambda uid: authenticated,
            rich_presence_status=AsyncMock(return_value=STATUS),
            rich_presence_set=AsyncMock(return_value=result or {**STATUS, "ok": True}),
        )

    def embed_text(embed):
        parts = [embed.title or "", embed.description or ""]
        parts += [f"{f.name} {f.value}" for f in embed.fields]
        return " ".join(parts)

    def text_input(value):
        return NS(value=value)

    async def main(scenario):
        it = interaction()
        if scenario == "not_authenticated":
            mgr = manager(authenticated=False)
            await handle_rich_presence(it, mgr, 123)
            it.response.send_message.assert_awaited_once()
            assert "autenticado" in it.response.send_message.await_args.args[0]
            mgr.rich_presence_status.assert_not_awaited()
        elif scenario == "opens_with_embed":
            mgr = manager()
            await handle_rich_presence(it, mgr, 123)
            it.response.defer.assert_awaited_once_with(ephemeral=True)
            kwargs = it.followup.send.await_args.kwargs
            text = embed_text(kwargs["embed"])
            assert "Filmes" in text and "Duna" in text and "Assistindo" in text, text
            assert isinstance(kwargs["view"], RichPresenceView)
            assert kwargs["ephemeral"] is True
        elif scenario == "toggle_sends_inverted_enabled":
            mgr = manager()
            view = RichPresenceView(mgr, 123, STATUS)
            await view._toggle(it)
            mgr.rich_presence_set.assert_awaited_once_with(123, False, CUSTOM)
            it.edit_original_response.assert_awaited_once()
        elif scenario == "restore_sends_null_custom":
            mgr = manager()
            view = RichPresenceView(mgr, 123, STATUS)
            await view._restore(it)
            mgr.rich_presence_set.assert_awaited_once_with(123, True, None)
        elif scenario == "texts_modal_merges_into_custom":
            mgr = manager()
            modal = NS(manager=mgr, user_id=123, status=STATUS, name=text_input(" Séries "),
                       details=text_input(""), state=text_input("T2"))
            await RichTextsModal.on_submit(modal, it)
            uid, enabled, custom = mgr.rich_presence_set.await_args.args
            assert (uid, enabled) == (123, True)
            assert custom == {**_NEW_FIELD_DEFAULTS, **CUSTOM, "name": "Séries", "details": None, "state": "T2"}, custom
            it.edit_original_response.assert_awaited_once()
        elif scenario == "texts_modal_starts_from_default":
            mgr = manager()
            status = {**STATUS, "custom": None}
            modal = NS(manager=mgr, user_id=123, status=status, name=text_input("Novo"),
                       details=text_input("a"), state=text_input("b"))
            await RichTextsModal.on_submit(modal, it)
            custom = mgr.rich_presence_set.await_args.args[2]
            assert custom["type"] == "playing" and custom["timer"] == "since_start", custom
            assert custom["name"] == "Novo" and custom["buttons"] == [], custom
        elif scenario == "type_from_default_uses_seed":
            mgr = manager()
            seed = {**CUSTOM, "type": "playing", "name": "Madman", "details": "Madman Services",
                    "state": "aylo.me/to", "timer": "since_start"}
            default = {**DEFAULT, "state": "aylo.me/to | Protecao Ativa"}
            view = RichPresenceView(mgr, 123, {**STATUS, "custom": None, "default": default, "seed": seed})
            await view._set_field(it, "type", "watching")
            custom = mgr.rich_presence_set.await_args.args[2]
            assert custom == {**_NEW_FIELD_DEFAULTS, **seed, "type": "watching"}, custom
        elif scenario == "images_modal":
            mgr = manager()
            modal = NS(manager=mgr, user_id=123, status=STATUS,
                       large_image=text_input("https://a.com/x.png"), large_text=text_input("Capa"),
                       small_image=text_input(""), small_text=text_input(""))
            await RichImagesModal.on_submit(modal, it)
            custom = mgr.rich_presence_set.await_args.args[2]
            assert custom["large_image"] == "https://a.com/x.png" and custom["large_text"] == "Capa"
            assert custom["small_image"] is None
        elif scenario == "buttons_modal":
            mgr = manager()
            modal = NS(manager=mgr, user_id=123, status=STATUS,
                       label_1=text_input("Site"), url_1=text_input("https://e.com"),
                       label_2=text_input(""), url_2=text_input(""))
            await RichButtonsModal.on_submit(modal, it)
            custom = mgr.rich_presence_set.await_args.args[2]
            assert custom["buttons"] == [{"label": "Site", "url": "https://e.com"}], custom
        elif scenario == "error_result_shows_message":
            mgr = manager(result={"ok": False, "code": "INVALID_RICH_PRESENCE", "message": "O nome é obrigatório."})
            view = RichPresenceView(mgr, 123, STATUS)
            await view._toggle(it)
            text = embed_text(it.edit_original_response.await_args.kwargs["embed"])
            assert "O nome é obrigatório." in text, text
        elif scenario == "disabled_embed":
            text = embed_text(build_rich_presence_embed({**STATUS, "enabled": False, "live": None}))
            assert "Desligado" in text, text
        elif scenario == "embed_non_playing_layout":
            live = {**DEFAULT, **CUSTOM, "source": "custom", "status_display": "details",
                    "party_current": 2, "party_max": 4, "state": "Parte 2"}
            text = embed_text(build_rich_presence_embed({**STATUS, "live": live}))
            assert "**Assistindo Filmes**" in text, text
            assert "**Duna**" in text and "Parte 2 (2 de 4)" in text, text
            assert "Na lista de membros: Assistindo Duna" in text, text
        elif scenario == "embed_playing_layout":
            live = {**DEFAULT, "source": "default"}
            text = embed_text(build_rich_presence_embed({**STATUS, "custom": None, "live": live}))
            assert "**Jogando**" in text and "**Madman**" in text and "Madman Services" in text, text
        elif scenario == "status_display_select":
            mgr = manager()
            view = RichPresenceView(mgr, 123, STATUS)
            await view._set_field(it, "status_display", "details")
            assert mgr.rich_presence_set.await_args.args[2]["status_display"] == "details"
        elif scenario == "links_modal":
            mgr = manager()
            modal = NS(manager=mgr, user_id=123, status=STATUS,
                       details_url=text_input("https://a.com/d"), state_url=text_input(""),
                       large_url=text_input("https://a.com/l"), small_url=text_input(""))
            await RichLinksModal.on_submit(modal, it)
            custom = mgr.rich_presence_set.await_args.args[2]
            assert custom["details_url"] == "https://a.com/d" and custom["state_url"] is None, custom
            assert custom["large_url"] == "https://a.com/l", custom
        elif scenario == "extras_modal":
            mgr = manager()
            modal = NS(manager=mgr, user_id=123, status=STATUS, pending={"type": "streaming"},
                       stream_url=text_input("https://twitch.tv/x"), party=text_input("2/4"),
                       duration=text_input("90"), platform=text_input(" PS5 "))
            await RichExtrasModal.on_submit(modal, it)
            custom = mgr.rich_presence_set.await_args.args[2]
            assert custom["type"] == "streaming" and custom["stream_url"] == "https://twitch.tv/x", custom
            assert (custom["party_current"], custom["party_max"]) == (2, 4), custom
            assert custom["duration_minutes"] == 90 and custom["platform"] == "ps5", custom
        elif scenario == "extras_modal_empty_clears":
            mgr = manager()
            status = {**STATUS, "custom": {**CUSTOM, "party_current": 1, "party_max": 2, "platform": "xbox"}}
            modal = NS(manager=mgr, user_id=123, status=status, pending={},
                       stream_url=text_input(""), party=text_input(""), duration=text_input(""), platform=text_input(""))
            await RichExtrasModal.on_submit(modal, it)
            custom = mgr.rich_presence_set.await_args.args[2]
            assert custom["party_current"] is None and custom["party_max"] is None and custom["platform"] is None, custom
        elif scenario == "extras_modal_invalid_party":
            mgr = manager()
            modal = NS(manager=mgr, user_id=123, status=STATUS, pending={},
                       stream_url=text_input(""), party=text_input("dois"), duration=text_input(""), platform=text_input(""))
            await RichExtrasModal.on_submit(modal, it)
            mgr.rich_presence_set.assert_not_awaited()
            it.response.send_message.assert_awaited_once()
            assert "grupo" in it.response.send_message.await_args.args[0].lower()
        elif scenario == "streaming_without_url_opens_extras":
            mgr = manager()
            view = RichPresenceView(mgr, 123, STATUS)
            await view._choose_type(it, "streaming")
            mgr.rich_presence_set.assert_not_awaited()
            modal = it.response.send_modal.await_args.args[0]
            assert isinstance(modal, RichExtrasModal) and modal.pending == {"type": "streaming"}
        elif scenario == "duration_without_minutes_opens_extras":
            mgr = manager()
            view = RichPresenceView(mgr, 123, STATUS)
            await view._choose_timer(it, "duration")
            mgr.rich_presence_set.assert_not_awaited()
            assert it.response.send_modal.await_args.args[0].pending == {"timer": "duration"}
        elif scenario == "plain_type_saves_directly":
            mgr = manager()
            view = RichPresenceView(mgr, 123, STATUS)
            await view._choose_type(it, "listening")
            assert mgr.rich_presence_set.await_args.args[2]["type"] == "listening"
        elif scenario == "task_embed":
            live = {**DEFAULT, "source": "task", "details": "Limpando DMs"}
            text = embed_text(build_rich_presence_embed({**STATUS, "live": live, "task": "Limpando DMs"}))
            assert "Limpando DMs" in text and "tarefa" in text.lower(), text
        else:
            raise SystemExit("cenario desconhecido")

    asyncio.run(main(sys.argv[1]))
""")


class RichPresencePanelTests(unittest.TestCase):
    def _check(self, scenario):
        result = subprocess.run(
            [sys.executable, "-c", _CODE, scenario],
            capture_output=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_not_authenticated(self):
        self._check("not_authenticated")

    def test_opens_with_embed(self):
        self._check("opens_with_embed")

    def test_toggle_sends_inverted_enabled(self):
        self._check("toggle_sends_inverted_enabled")

    def test_restore_sends_null_custom(self):
        self._check("restore_sends_null_custom")

    def test_texts_modal_merges_into_custom(self):
        self._check("texts_modal_merges_into_custom")

    def test_texts_modal_starts_from_default(self):
        self._check("texts_modal_starts_from_default")

    def test_type_from_default_uses_seed(self):
        self._check("type_from_default_uses_seed")

    def test_images_modal(self):
        self._check("images_modal")

    def test_buttons_modal(self):
        self._check("buttons_modal")

    def test_error_result_shows_message(self):
        self._check("error_result_shows_message")

    def test_disabled_embed(self):
        self._check("disabled_embed")

    def test_embed_non_playing_layout(self):
        self._check("embed_non_playing_layout")

    def test_embed_playing_layout(self):
        self._check("embed_playing_layout")

    def test_status_display_select(self):
        self._check("status_display_select")

    def test_links_modal(self):
        self._check("links_modal")

    def test_extras_modal(self):
        self._check("extras_modal")

    def test_extras_modal_empty_clears(self):
        self._check("extras_modal_empty_clears")

    def test_extras_modal_invalid_party(self):
        self._check("extras_modal_invalid_party")

    def test_streaming_without_url_opens_extras(self):
        self._check("streaming_without_url_opens_extras")

    def test_duration_without_minutes_opens_extras(self):
        self._check("duration_without_minutes_opens_extras")

    def test_plain_type_saves_directly(self):
        self._check("plain_type_saves_directly")

    def test_task_embed(self):
        self._check("task_embed")


if __name__ == "__main__":
    unittest.main()
