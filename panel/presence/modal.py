from __future__ import annotations

import re

import discord
from discord import ui

TYPE_NAMES = {
    "playing": "Jogando",
    "streaming": "Transmitindo",
    "listening": "Ouvindo",
    "watching": "Assistindo",
    "competing": "Competindo",
}
# Como o Discord escreve o tipo antes do nome no cabeçalho e na lista de membros.
TYPE_PREFIX = {**TYPE_NAMES, "competing": "Competindo em"}
TIMER_NAMES = {
    "none": "Sem cronômetro",
    "since_start": "Desde que a conta ligou",
    "since_save": "Desde que salvei",
    "duration": "Duração (com fim)",
}
STATUS_DISPLAY_NAMES = {"name": "Nome", "details": "Detalhes", "state": "Estado"}
PLATFORMS = {
    "desktop": "Computador",
    "xbox": "Xbox",
    "ps4": "PS4",
    "ps5": "PS5",
    "android": "Android",
    "ios": "iOS",
}
# Campos novos têm padrão: um Rich Presence salvo antes deles continua válido.
_NEW_FIELD_DEFAULTS = {
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
}


def _safe(value, limit: int = 200) -> str:
    text = " ".join(str(value or "").split())
    return discord.utils.escape_mentions(discord.utils.escape_markdown(text))[:limit]


def _optional(value: str) -> str | None:
    return value.strip() or None


def base_custom(status: dict) -> dict:
    """O Rich Presence atual como ponto de partida da edição (o padrão do Madman se não houver um)."""
    custom = status.get("custom")
    if custom:
        return {**_NEW_FIELD_DEFAULTS, **custom}
    if status.get("seed"):
        # O worker manda o padrão sem o aviso de proteção, que muda sozinho e não deve ser gravado.
        return {**_NEW_FIELD_DEFAULTS, **status["seed"]}
    default = status.get("default") or {}
    return {
        **_NEW_FIELD_DEFAULTS,
        "type": default.get("type") or "playing",
        "name": default.get("name") or "Madman",
        "details": default.get("details"),
        "state": default.get("state"),
        "timer": "since_start",
        "large_image": None,
        "large_text": None,
        "small_image": None,
        "small_text": None,
        "buttons": [],
    }


def member_list_line(live: dict) -> str:
    field = live.get(live.get("status_display") or "name") or live.get("name")
    return f"Na lista de membros: {TYPE_PREFIX.get(live.get('type'), 'Jogando')} {_safe(field)}"


def _activity_lines(live: dict) -> list[str]:
    """O card como o Discord desenha: em Jogando o nome é o título; nos outros tipos o nome
    sobe para o cabeçalho ("Ouvindo Madman") e os detalhes viram o título."""
    activity_type = live.get("type") or "playing"
    platform = PLATFORMS.get(live.get("platform") or "")
    head = "Jogando" if activity_type == "playing" else f"{TYPE_PREFIX.get(activity_type)} {_safe(live.get('name'))}"
    if platform:
        head += f" · {platform}"
    if activity_type == "streaming":
        head = "🔴 " + head
    party = f" ({live['party_current']} de {live['party_max']})" if live.get("party_current") else ""
    state = (_safe(live.get("state")) + party).strip()
    if activity_type == "playing":
        lines = [f"**{head}**", f"**{_safe(live.get('name'))}**", _safe(live.get("details")), state]
    else:
        lines = [f"**{head}**", f"**{_safe(live.get('details') or live.get('name'))}**", state]
    return [line for line in lines if line]


def build_rich_presence_embed(status: dict, result: dict | None = None) -> discord.Embed:
    connected = bool(status.get("ok"))
    enabled = bool(status.get("enabled"))
    live = status.get("live")
    embed = discord.Embed(
        title=f"🎮 Rich Presence {'Ligado' if enabled else 'Desligado'}",
        color=0x5865F2 if enabled else (0x95A5A6 if connected else 0xE74C3C),
    )

    if result and not result.get("ok"):
        embed.description = f"⚠️ {_safe(result.get('message'), 500)}"
    elif result:
        warnings = " ".join(result.get("warnings") or [])
        embed.description = "✅ Rich Presence atualizado." + (f"\n⚠️ {_safe(warnings, 500)}" if warnings else "")
    elif not connected:
        embed.description = f"❌ {_safe(status.get('message'), 500)}"
        return embed
    else:
        embed.description = "A atividade que o Madman mostra no seu perfil. Jogos e outros apps continuam aparecendo."

    if not enabled or not live:
        embed.add_field(name="Agora", value="Desligado: só o seu status personalizado aparece.", inline=False)
    else:
        lines = _activity_lines(live)
        lines.append(f"-# {member_list_line(live)}")
        if live.get("source") == "task":
            lines.append("_Tarefa em andamento: volta para o seu ao terminar._")
        embed.add_field(name="Agora", value="\n".join(lines), inline=False)
        if live.get("large_image"):
            embed.set_thumbnail(url=live["large_image"])

    base = base_custom(status)
    embed.add_field(name="Tipo", value=TYPE_NAMES.get(base["type"], "Jogando"), inline=True)
    timer = TIMER_NAMES.get(base.get("timer"), "Sem cronômetro")
    if base.get("timer") == "duration" and base.get("duration_minutes"):
        timer += f" · {base['duration_minutes']} min"
    embed.add_field(name="Cronômetro", value=timer, inline=True)
    buttons = base.get("buttons") or []
    embed.add_field(
        name="Botões",
        value=", ".join(_safe(b.get("label"), 32) for b in buttons) if buttons else "Nenhum",
        inline=True,
    )
    embed.set_footer(text="Origem: " + ("personalizado" if status.get("custom") else "padrão do Madman"))
    return embed


async def apply_rich_presence(
    interaction: discord.Interaction,
    manager,
    user_id: int,
    status: dict,
    *,
    changes: dict | None = None,
    enabled: bool | None = None,
    restore: bool = False,
):
    """Salva a mudança no worker e atualiza a mensagem do painel com o resultado."""
    await interaction.response.defer()
    custom = None if restore else ({**base_custom(status), **changes} if changes is not None else status.get("custom"))
    target_enabled = bool(status.get("enabled")) if enabled is None else enabled
    result = await manager.rich_presence_set(user_id, target_enabled, custom)
    fresh = await manager.rich_presence_status(user_id)
    await interaction.edit_original_response(
        embed=build_rich_presence_embed(fresh, result),
        view=RichPresenceView(manager, user_id, fresh),
    )


def _select(placeholder: str, names: dict, current, row: int) -> ui.Select:
    return ui.Select(
        placeholder=placeholder,
        options=[
            discord.SelectOption(label=label, value=value, default=value == current) for value, label in names.items()
        ],
        row=row,
    )


class RichPresenceView(ui.View):
    def __init__(self, manager, user_id: int, status: dict):
        super().__init__(timeout=300)
        self.manager = manager
        self.user_id = int(user_id)
        self.status = status
        enabled = bool(status.get("enabled"))
        base = base_custom(status)

        type_select = _select("Tipo da atividade", TYPE_NAMES, base["type"], 0)
        type_select.callback = lambda it: self._choose_type(it, type_select.values[0])
        self.add_item(type_select)

        timer_select = _select("Cronômetro", TIMER_NAMES, base.get("timer"), 1)
        timer_select.callback = lambda it: self._choose_timer(it, timer_select.values[0])
        self.add_item(timer_select)

        display_names = {key: f"Lista de membros: {label}" for key, label in STATUS_DISPLAY_NAMES.items()}
        display_select = _select("Texto na lista de membros", display_names, base.get("status_display"), 2)
        display_select.callback = lambda it: self._set_field(it, "status_display", display_select.values[0])
        self.add_item(display_select)

        for label, emoji, style, callback, row in (
            (
                "Desligar" if enabled else "Ligar",
                "⏹️" if enabled else "▶️",
                discord.ButtonStyle.danger if enabled else discord.ButtonStyle.success,
                self._toggle,
                3,
            ),
            ("Editar textos", "📝", discord.ButtonStyle.secondary, self._open_texts, 3),
            ("Editar imagens", "🖼️", discord.ButtonStyle.secondary, self._open_images, 3),
            ("Editar botões", "🔗", discord.ButtonStyle.secondary, self._open_buttons, 3),
            ("Editar links", "🌐", discord.ButtonStyle.secondary, self._open_links, 3),
            ("Extras", "⚙️", discord.ButtonStyle.secondary, self._open_extras, 4),
            ("Restaurar padrão", "↩️", discord.ButtonStyle.secondary, self._restore, 4),
        ):
            button = ui.Button(label=label, emoji=emoji, style=style, row=row)
            button.callback = callback
            self.add_item(button)

    async def _owned(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("Este controle pertence a outro usuario.", ephemeral=True)
        return False

    async def _toggle(self, interaction: discord.Interaction):
        if not await self._owned(interaction):
            return
        await apply_rich_presence(
            interaction, self.manager, self.user_id, self.status, enabled=not bool(self.status.get("enabled"))
        )

    async def _restore(self, interaction: discord.Interaction):
        if not await self._owned(interaction):
            return
        await apply_rich_presence(interaction, self.manager, self.user_id, self.status, restore=True)

    async def _set_field(self, interaction: discord.Interaction, key: str, value: str):
        if not await self._owned(interaction):
            return
        await apply_rich_presence(interaction, self.manager, self.user_id, self.status, changes={key: value})

    async def _choose_type(self, interaction: discord.Interaction, value: str):
        # Transmitindo precisa do link: pede pelo modal em vez de salvar e falhar.
        if value == "streaming" and not base_custom(self.status).get("stream_url"):
            if await self._owned(interaction):
                await interaction.response.send_modal(
                    RichExtrasModal(self.manager, self.user_id, self.status, pending={"type": value})
                )
            return
        await self._set_field(interaction, "type", value)

    async def _choose_timer(self, interaction: discord.Interaction, value: str):
        if value == "duration" and not base_custom(self.status).get("duration_minutes"):
            if await self._owned(interaction):
                await interaction.response.send_modal(
                    RichExtrasModal(self.manager, self.user_id, self.status, pending={"timer": value})
                )
            return
        await self._set_field(interaction, "timer", value)

    async def _open_texts(self, interaction: discord.Interaction):
        if await self._owned(interaction):
            await interaction.response.send_modal(RichTextsModal(self.manager, self.user_id, self.status))

    async def _open_images(self, interaction: discord.Interaction):
        if await self._owned(interaction):
            await interaction.response.send_modal(RichImagesModal(self.manager, self.user_id, self.status))

    async def _open_buttons(self, interaction: discord.Interaction):
        if await self._owned(interaction):
            await interaction.response.send_modal(RichButtonsModal(self.manager, self.user_id, self.status))

    async def _open_links(self, interaction: discord.Interaction):
        if await self._owned(interaction):
            await interaction.response.send_modal(RichLinksModal(self.manager, self.user_id, self.status))

    async def _open_extras(self, interaction: discord.Interaction):
        if await self._owned(interaction):
            await interaction.response.send_modal(RichExtrasModal(self.manager, self.user_id, self.status))


class _RichModal(ui.Modal):
    def __init__(self, manager, user_id: int, status: dict):
        super().__init__()
        self.manager = manager
        self.user_id = int(user_id)
        self.status = status

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id


class RichTextsModal(_RichModal, title="Rich Presence: textos"):
    name = ui.TextInput(label="Nome", max_length=128, required=True)
    details = ui.TextInput(label="Detalhes (primeira linha)", max_length=128, required=False)
    state = ui.TextInput(label="Estado (segunda linha)", max_length=128, required=False)

    def __init__(self, manager, user_id: int, status: dict):
        super().__init__(manager, user_id, status)
        base = base_custom(status)
        self.name.default = base.get("name") or ""
        self.details.default = base.get("details") or ""
        self.state.default = base.get("state") or ""

    async def on_submit(self, interaction: discord.Interaction):
        changes = {
            "name": self.name.value.strip(),
            "details": _optional(self.details.value),
            "state": _optional(self.state.value),
        }
        await apply_rich_presence(interaction, self.manager, self.user_id, self.status, changes=changes)


class RichImagesModal(_RichModal, title="Rich Presence: imagens"):
    large_image = ui.TextInput(label="Imagem grande (link https)", max_length=512, required=False)
    large_text = ui.TextInput(label="Texto da imagem grande", max_length=128, required=False)
    small_image = ui.TextInput(label="Imagem pequena (link https)", max_length=512, required=False)
    small_text = ui.TextInput(label="Texto da imagem pequena", max_length=128, required=False)

    def __init__(self, manager, user_id: int, status: dict):
        super().__init__(manager, user_id, status)
        base = base_custom(status)
        for key in ("large_image", "large_text", "small_image", "small_text"):
            getattr(self, key).default = base.get(key) or ""

    async def on_submit(self, interaction: discord.Interaction):
        changes = {
            key: _optional(getattr(self, key).value)
            for key in ("large_image", "large_text", "small_image", "small_text")
        }
        await apply_rich_presence(interaction, self.manager, self.user_id, self.status, changes=changes)


class RichButtonsModal(_RichModal, title="Rich Presence: botões"):
    label_1 = ui.TextInput(label="Botão 1: rótulo", max_length=32, required=False)
    url_1 = ui.TextInput(label="Botão 1: link https", max_length=512, required=False)
    label_2 = ui.TextInput(label="Botão 2: rótulo", max_length=32, required=False)
    url_2 = ui.TextInput(label="Botão 2: link https", max_length=512, required=False)

    def __init__(self, manager, user_id: int, status: dict):
        super().__init__(manager, user_id, status)
        buttons = base_custom(status).get("buttons") or []
        for index in (1, 2):
            button = buttons[index - 1] if len(buttons) >= index else {}
            getattr(self, f"label_{index}").default = button.get("label") or ""
            getattr(self, f"url_{index}").default = button.get("url") or ""

    async def on_submit(self, interaction: discord.Interaction):
        buttons = []
        for index in (1, 2):
            label = getattr(self, f"label_{index}").value.strip()
            url = getattr(self, f"url_{index}").value.strip()
            if label or url:
                buttons.append({"label": label, "url": url})
        await apply_rich_presence(interaction, self.manager, self.user_id, self.status, changes={"buttons": buttons})


class RichLinksModal(_RichModal, title="Rich Presence: links ao clicar"):
    details_url = ui.TextInput(label="Link nos detalhes (https)", max_length=256, required=False)
    state_url = ui.TextInput(label="Link no estado (https)", max_length=256, required=False)
    large_url = ui.TextInput(label="Link na imagem grande (https)", max_length=256, required=False)
    small_url = ui.TextInput(label="Link na imagem pequena (https)", max_length=256, required=False)

    _KEYS = ("details_url", "state_url", "large_url", "small_url")

    def __init__(self, manager, user_id: int, status: dict):
        super().__init__(manager, user_id, status)
        base = base_custom(status)
        for key in self._KEYS:
            getattr(self, key).default = base.get(key) or ""

    async def on_submit(self, interaction: discord.Interaction):
        changes = {key: _optional(getattr(self, key).value) for key in RichLinksModal._KEYS}
        await apply_rich_presence(interaction, self.manager, self.user_id, self.status, changes=changes)


class RichExtrasModal(_RichModal, title="Rich Presence: extras"):
    stream_url = ui.TextInput(
        label="Link da transmissão (Twitch/YouTube)",
        max_length=512,
        required=False,
        placeholder="https://twitch.tv/...",
    )
    party = ui.TextInput(label="Grupo (atual/máximo)", max_length=5, required=False, placeholder="2/4")
    duration = ui.TextInput(label="Duração em minutos (1 a 1440)", max_length=4, required=False)
    platform = ui.TextInput(
        label="Plataforma", max_length=10, required=False, placeholder="desktop, xbox, ps4, ps5, android ou ios"
    )

    def __init__(self, manager, user_id: int, status: dict, pending: dict | None = None):
        super().__init__(manager, user_id, status)
        # Mudança escolhida no select (tipo/cronômetro) que precisa destes dados para valer.
        self.pending = dict(pending or {})
        base = base_custom(status)
        self.stream_url.default = base.get("stream_url") or ""
        if base.get("party_current"):
            self.party.default = f"{base['party_current']}/{base['party_max']}"
        self.duration.default = str(base["duration_minutes"]) if base.get("duration_minutes") else ""
        self.platform.default = base.get("platform") or ""

    async def on_submit(self, interaction: discord.Interaction):
        party = self.party.value.strip()
        match = re.fullmatch(r"(\d{1,2})\s*/\s*(\d{1,2})", party)
        if party and not match:
            await interaction.response.send_message(
                "Grupo inválido: use o formato atual/máximo, por exemplo 2/4.", ephemeral=True
            )
            return
        duration = self.duration.value.strip()
        if duration and not duration.isdigit():
            await interaction.response.send_message("Duração inválida: use só números de 1 a 1440.", ephemeral=True)
            return
        changes = {
            **self.pending,
            "stream_url": _optional(self.stream_url.value),
            "party_current": int(match.group(1)) if match else None,
            "party_max": int(match.group(2)) if match else None,
            "duration_minutes": int(duration) if duration else None,
            "platform": (self.platform.value.strip().lower() or None),
        }
        await apply_rich_presence(interaction, self.manager, self.user_id, self.status, changes=changes)
