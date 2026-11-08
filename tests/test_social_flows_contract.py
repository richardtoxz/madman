"""Contrato dos fluxos de panel/features/social.py: textos, embeds, botões e chamadas ao manager.

Cada cenário roda o handler com uma DM e um manager falsos, aperta os botões na ordem do roteiro e
grava tudo; o resultado é comparado com `fixtures/social_flows_contract.json`, gerado do
comportamento existente. Uma mudança intencional nos fluxos exige regenerar o arquivo:

    python -m tests.test_social_flows_contract --update
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).parent / "fixtures" / "social_flows_contract.json"

# Painel e worker usam bibliotecas do Discord diferentes: roda num processo só do painel.
SCRIPT = textwrap.dedent(
    """
    import asyncio
    import json
    from types import SimpleNamespace as NS

    import panel
    import discord
    from panel.social import handlers as social

    TRACE = []
    STATE = {"view": None, "dm_blocked": False}

    def view_shape(view):
        if view is None:
            return None
        items = []
        for child in view.children:
            item = {"type": type(child).__name__, "row": child.row}
            for attr in ("label", "disabled", "placeholder", "min_values", "max_values"):
                if hasattr(child, attr):
                    item[attr] = getattr(child, attr)
            if hasattr(child, "style"):
                item["style"] = child.style.name
            emoji = getattr(child, "emoji", None)
            item["emoji"] = str(emoji) if emoji else None
            if isinstance(child, discord.ui.Select):
                item["options"] = [
                    [o.label, o.description, o.value, o.default] for o in child.options
                ]
            items.append(item)
        return {"timeout": view.timeout, "items": items}

    def shape(kwargs):
        out = {}
        for key, value in kwargs.items():
            if key == "embed":
                out[key] = value.to_dict() if value else None
            elif key == "view":
                out[key] = view_shape(value)
                STATE["view"] = value
                if value is not None and any(getattr(c, "label", None) == "Cancelar operacao" for c in value.children):
                    STATE["cancel_view"] = value
            else:
                out[key] = value
        return out

    class FakeDM:
        def __init__(self, embed):
            TRACE.append(["dm.open", shape({"embed": embed})])

        async def edit(self, **kwargs):
            TRACE.append(["dm.edit", shape(kwargs)])

        async def finish(self, **kwargs):
            TRACE.append(["dm.finish", shape(kwargs)])

        def hold(self):
            TRACE.append(["dm.hold"])

        async def expire_after(self, seconds):
            TRACE.append(["dm.expire_after", seconds])

    async def fake_open_temporary_dm(interaction, embed):
        if STATE["dm_blocked"]:
            TRACE.append(["dm.open_blocked"])
            return None
        return FakeDM(embed)

    social.open_temporary_dm = fake_open_temporary_dm

    def recorder(name):
        async def record(*args, **kwargs):
            TRACE.append([name, list(args), shape(kwargs)])
        return record

    def interaction(user_id=123):
        tag = f"inter[{user_id}]"
        return NS(
            user=NS(id=user_id),
            response=NS(
                defer=recorder(tag + ".response.defer"),
                send_message=recorder(tag + ".response.send_message"),
                edit_message=recorder(tag + ".response.edit_message"),
            ),
            followup=NS(send=recorder(tag + ".followup.send")),
            edit_original_response=recorder(tag + ".edit_original_response"),
        )

    RESULTS = {
        "start_guild_cleanup": "PARCIAL:1 falha",
        "start_rel_cleanup": "COMPLETADO:4 removidos",
        "accept_all_requests": "CANCELADO:parou",
        "deny_all_requests": "ABORTADO:seguranca",
        "unblock_all": "",
    }
    PROGRESS = [
        (0, 3, "WAITING_START", None),
        (1, 3, "CLEANING", "Alvo *um*"),
        (1, 3, "BATCH_PAUSE", "pausa"),
        (1, 3, "RATE_LIMITED", None),
        (2, 3, "ERROR", "falhou"),
        (0, 0, "OUTRO", None),
        (3, 3, "CLEANING", "Fim"),
    ]

    class Manager:
        def __init__(self, *, authenticated=True, guild_total=3, guild_fresh=True, whitelist=(2,),
                     friends=4, friends_fresh=False, rel_stats=True, blocked_total=2, blocked_fresh=True):
            self.authenticated = authenticated
            self.guild_total = guild_total
            self.guild_fresh = guild_fresh
            self.whitelist = set(whitelist)
            self.friends = friends
            self.friends_fresh = friends_fresh
            self.rel_stats = rel_stats
            self.blocked_total = blocked_total
            self.blocked_fresh = blocked_fresh

        def _rec(self, name, *args, **kwargs):
            TRACE.append(["manager." + name, list(args), kwargs])

        def is_authenticated(self, user_id):
            return self.authenticated

        async def get_guild_stats(self, user_id):
            self._rec("get_guild_stats", user_id)
            ids = list(range(1, self.guild_total + 1))
            return {"total": self.guild_total, "fresh": self.guild_fresh, "warning": "aviso <@1>",
                    "guild_ids": ids, "owner_ids": ids[-1:]}

        def get_guild_whitelist(self, user_id):
            return sorted(self.whitelist)

        async def get_guilds_page(self, user_id, page=0, page_size=20):
            self._rec("get_guilds_page", user_id, page=page, page_size=page_size)
            return {"items": [{"id": 1 + page * 2, "name": "Alpha", "muted": True},
                              {"id": 2 + page * 2, "name": "Beta *md*", "owner": True}],
                    "total": 4, "page": page, "pages": 2, "warning": None if page else "parcial"}

        async def update_guild_whitelist(self, user_id, visible_ids, selected_ids):
            self._rec("update_guild_whitelist", user_id, visible_ids, selected_ids)
            self.whitelist = (self.whitelist - set(visible_ids)) | set(selected_ids)

        async def clear_guild_whitelist(self, user_id):
            self._rec("clear_guild_whitelist", user_id)
            removed = len(self.whitelist)
            self.whitelist.clear()
            return removed

        async def get_pending_requests(self, user_id, page=0):
            self._rec("get_pending_requests", user_id, page=page)
            return {"incoming": [{"id": 10 + page, "name": "Entrada"}], "outgoing": [{"id": 20 + page, "name": "Saida"}],
                    "incoming_total": 3, "outgoing_total": 2, "page": page, "pages": 2,
                    "warning": "lento" if page == 0 else None}

        async def get_blocked_list(self, user_id, page=0):
            self._rec("get_blocked_list", user_id, page=page)
            items = [{"id": 30 + page, "name": "Bloqueado"}] if self.blocked_total else []
            return {"items": items, "total": self.blocked_total, "page": page,
                    "pages": 3 if page == 0 else 1, "fresh": self.blocked_fresh,
                    "warning": None if page == 0 else "pagina 2"}

        async def get_rel_stats(self, user_id):
            self._rec("get_rel_stats", user_id)
            if not self.rel_stats:
                return None
            return {"friends": self.friends, "fresh": self.friends_fresh, "warning": "cache antigo"}

        async def control_clean(self, user_id, command):
            self._rec("control_clean", user_id, command)
            return True

        async def _operation(self, name, user_id, *args):
            progress = args[-1]
            self._rec(name, user_id, *args[:-1])
            for current, total, status, detail in PROGRESS:
                await progress(current, total, status, detail)
            return RESULTS[name]

        async def start_guild_cleanup(self, user_id, action, progress):
            return await self._operation("start_guild_cleanup", user_id, action, progress)

        async def start_rel_cleanup(self, user_id, progress):
            return await self._operation("start_rel_cleanup", user_id, progress)

        async def accept_all_requests(self, user_id, progress):
            return await self._operation("accept_all_requests", user_id, progress)

        async def deny_all_requests(self, user_id, progress):
            return await self._operation("deny_all_requests", user_id, progress)

        async def unblock_all(self, user_id, progress):
            return await self._operation("unblock_all", user_id, progress)

    def button(label_start):
        return next(c for c in STATE["view"].children if (getattr(c, "label", None) or "").startswith(label_start))

    def button_by_emoji(emoji):
        return next(c for c in STATE["view"].children if str(getattr(c, "emoji", "") or "") == emoji)

    async def click(item, user_id=123):
        TRACE.append(["click", item.label or str(item.emoji), user_id])
        await item.callback(interaction(user_id))

    HANDLERS = {
        "whitelist": social.handle_guild_whitelist,
        "pending": social.handle_pending_requests,
        "friends": social.handle_clean_friends,
        "unblock": social.handle_unblock_all,
        "mute": social.handle_mute_servers,
        "leave": social.handle_leave_servers,
    }

    async def scenario(name):
        manager = Manager()
        if name.startswith("unauth_"):
            await HANDLERS[name.removeprefix("unauth_")](interaction(), Manager(authenticated=False), 123)
        elif name.startswith("blocked_"):
            STATE["dm_blocked"] = True
            await HANDLERS[name.removeprefix("blocked_")](interaction(), manager, 123)
        elif name == "whitelist":
            await social.handle_guild_whitelist(interaction(), manager, 123)
            select = next(c for c in STATE["view"].children if isinstance(c, discord.ui.Select))
            select._values = ["1"]
            TRACE.append(["select", ["1"]])
            await select.callback(interaction())
            await click(button_by_emoji("▶"))
            await click(button_by_emoji("◀"))
            await click(button("Limpar whitelist"))
        elif name in ("pending_accept", "pending_deny"):
            await social.handle_pending_requests(interaction(), manager, 123)
            await click(button_by_emoji("▶"))
            await click(button("Aceitar Todos" if name == "pending_accept" else "Remover Todos"))
        elif name == "friends":
            await social.handle_clean_friends(interaction(), manager, 123)
            await click(button("Confirmar"))
        elif name == "friends_none":
            await social.handle_clean_friends(interaction(), Manager(friends=0, friends_fresh=True), 123)
        elif name == "friends_unconfirmed":
            await social.handle_clean_friends(interaction(), Manager(friends=0), 123)
        elif name == "friends_no_stats":
            await social.handle_clean_friends(interaction(), Manager(rel_stats=False), 123)
        elif name == "unblock":
            await social.handle_unblock_all(interaction(), manager, 123)
            await click(button_by_emoji("▶"))
            await click(button("Desbloquear Todos"))
            await click(button("SIM, Desbloquear Todos"))
        elif name == "unblock_none":
            await social.handle_unblock_all(interaction(), Manager(blocked_total=0), 123)
        elif name == "unblock_unconfirmed":
            await social.handle_unblock_all(interaction(), Manager(blocked_total=0, blocked_fresh=False), 123)
        elif name in ("mute", "leave"):
            await HANDLERS[name](interaction(), manager, 123)
            await click(button("Confirmar"))
        elif name == "leave_no_guilds":
            await social.handle_leave_servers(interaction(), Manager(guild_total=0), 123)
        elif name == "mute_unconfirmed":
            await social.handle_mute_servers(interaction(), Manager(guild_total=0, guild_fresh=False), 123)
        elif name == "mute_all_protected":
            await social.handle_mute_servers(interaction(), Manager(whitelist=(1, 2, 3)), 123)
        elif name == "leave_all_protected":
            await social.handle_leave_servers(interaction(), Manager(whitelist=(1, 2)), 123)
        else:
            raise SystemExit("cenario desconhecido: " + name)
        # Fluxos confirmados exibem "Cancelar operacao": outro usuário é recusado, o dono cancela.
        cancel_view = STATE.get("cancel_view")
        if cancel_view is not None:
            cancel = next(c for c in cancel_view.children if c.label == "Cancelar operacao")
            await click(cancel, user_id=999)
            await click(cancel, user_id=123)

    SCENARIOS = (
        [f"unauth_{name}" for name in HANDLERS]
        + [f"blocked_{name}" for name in ("pending", "friends", "unblock", "mute", "leave")]
        + ["whitelist", "pending_accept", "pending_deny", "friends", "friends_none", "friends_unconfirmed",
           "friends_no_stats", "unblock", "unblock_none", "unblock_unconfirmed", "mute", "leave",
           "leave_no_guilds", "mute_unconfirmed", "mute_all_protected", "leave_all_protected"]
    )

    async def main():
        result = {}
        for name in SCENARIOS:
            TRACE.clear()
            STATE.update(view=None, dm_blocked=False, cancel_view=None)
            await scenario(name)
            result[name] = TRACE[:]
        # Escape ASCII: o console do Windows (cp1252) não codifica os emojis dos textos.
        print(json.dumps(result))

    asyncio.run(main())
    """
)


def collect_contract() -> dict:
    completed = subprocess.run(
        [sys.executable, "-c", SCRIPT],
        capture_output=True,
        encoding="utf-8",
        cwd=ROOT,
        timeout=60,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return json.loads(completed.stdout)


class SocialFlowsContractTests(unittest.TestCase):
    def test_every_social_flow_keeps_its_texts_buttons_and_calls(self):
        expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
        actual = collect_contract()
        self.assertEqual(sorted(actual), sorted(expected))
        for name in expected:
            with self.subTest(scenario=name):
                self.assertEqual(actual[name], expected[name])


if __name__ == "__main__":
    if "--update" in sys.argv:
        data = collect_contract()
        FIXTURE.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"{len(data)} cenarios gravados em {FIXTURE}")
    else:
        unittest.main()
