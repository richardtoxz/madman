import discord
from discord import ui

from ..shared.helpers import generate_progress_bar
from ..shared.stealth_dm import CHOICE_TTL_SECONDS, TemporaryDM, open_temporary_dm


_AUTH_REQUIRED = "❌ Você precisa estar autenticado! Use **Autenticar** primeiro."


def _authenticated(manager, user_id: int) -> bool:
    is_auth = getattr(manager, "is_authenticated", None)
    if callable(is_auth):
        return bool(is_auth(user_id))
    return user_id in getattr(manager, "active_processes", {})


async def _ensure_authenticated(
    interaction: discord.Interaction, manager, user_id: int, message: str = _AUTH_REQUIRED
) -> bool:
    if _authenticated(manager, user_id):
        return True
    await interaction.response.send_message(message, ephemeral=True)
    return False


def _safe_display(value) -> str:
    text = " ".join(str(value or "Desconhecido").split())
    text = discord.utils.escape_mentions(discord.utils.escape_markdown(text))
    return text[:100] or "Desconhecido"


def _preview_text(items: list[dict], total: int, *, paginated: bool = False) -> str:
    if not items:
        return "*Nenhum*"

    lines = []
    size = 0
    for item in items:
        line = f"• **{_safe_display(item.get('name'))}** (`{int(item.get('id', 0))}`)"
        if size + len(line) + 1 > 900:
            break
        lines.append(line)
        size += len(line) + 1

    hidden = 0 if paginated else max(0, total - len(lines))
    if hidden:
        lines.append(f"*... e mais {hidden}*")
    return "\n".join(lines)


def _result_presentation(result: str, action: str) -> tuple[str, int, str]:
    raw = result or "ERRO:Sem resposta do worker"
    status, separator, detail = raw.partition(":")
    status = status.strip().upper()
    detail = detail.strip() if separator else raw.strip()
    presentations = {
        "COMPLETADO": ("✅", 0x2ECC71, "concluida"),
        "PARCIAL": ("⚠️", 0xF1C40F, "concluida parcialmente"),
        "CANCELADO": ("⏹️", 0xE67E22, "cancelada"),
        "ABORTADO": ("🛑", 0xE74C3C, "interrompida com seguranca"),
    }
    emoji, color, label = presentations.get(status, ("❌", 0xE74C3C, "nao concluida"))
    return f"{emoji} {action} {label}", color, detail


def _cancel_view(manager, owner_id: int, dm: TemporaryDM) -> ui.View:
    view = ui.View(timeout=None)
    button = ui.Button(
        label="Cancelar operacao",
        emoji="⏹️",
        style=discord.ButtonStyle.danger,
    )

    async def cancel_callback(interaction: discord.Interaction):
        if interaction.user.id != owner_id:
            return await interaction.response.send_message("Esta operacao pertence a outro usuario.", ephemeral=True)
        await interaction.response.defer()
        sent = await manager.control_clean(owner_id, "CANCEL")
        message = (
            "⏹️ Cancelamento solicitado. Finalizando o item atual..."
            if sent
            else "❌ O worker nao esta mais disponivel."
        )
        await dm.edit(content=message, view=None)

    button.callback = cancel_callback
    view.add_item(button)
    return view


def _make_progress_callback(
    dm: TemporaryDM,
    *,
    title: str,
    action: str,
):
    async def progress(current, total, status, detail):
        embed = discord.Embed(title=title, color=0xF1C40F)
        should_throttle = False
        if status == "WAITING_START":
            embed.description = f"Preparando **{total or 0}** itens..."
        elif status == "CLEANING":
            current = current or 0
            total = total or 0
            bar = generate_progress_bar(current, total) if total else ""
            embed.description = f"{action}: **{_safe_display(detail)}**\nProgresso: **{current}/{total}**\n\n{bar}"
            should_throttle = bool(current and total and current < total)
        elif status == "BATCH_PAUSE":
            embed.description = f"⏸️ {_safe_display(detail)}\nProgresso: **{current or 0}/{total or 0}**"
            embed.color = 0xE67E22
        elif status == "RATE_LIMITED":
            embed.description = "⏳ O Discord solicitou uma espera."
            embed.color = 0xE67E22
        elif status == "ERROR":
            embed.description = f"⚠️ Falha individual: {_safe_display(detail)}"
            embed.color = 0xE74C3C
        else:
            return

        await dm.edit(embed=embed, throttle=should_throttle)

    return progress


async def _finish_operation(
    dm: TemporaryDM,
    *,
    action: str,
    result: str,
):
    title, color, detail = _result_presentation(result, action)
    await dm.finish(embed=discord.Embed(title=title, description=f"**Resultado:** {detail}", color=color))


async def _run_operation(dm, operation, *, action):
    try:
        result = await operation
    except Exception:
        await dm.finish(content="❌ Falha inesperada ao acompanhar a operação.")
        raise
    await _finish_operation(dm, action=action, result=result)


async def _run_confirmed_operation(
    inter: discord.Interaction,
    dm: TemporaryDM,
    manager,
    user_id: int,
    *,
    started: str,
    title: str,
    verb: str,
    action: str,
    start,
):
    await inter.response.defer()
    dm.hold()
    await dm.edit(content=started, view=_cancel_view(manager, user_id, dm))
    progress = _make_progress_callback(dm, title=title, action=verb)
    await _run_operation(dm, start(progress), action=action)


def _page_state(data: dict) -> tuple[int, int]:
    return int(data.get("page", 0)), max(1, int(data.get("pages", 1)))


def _add_page_buttons(view: ui.View, page: int, pages: int, change_page, *, row: int | None = None) -> None:
    previous = ui.Button(emoji="◀", style=discord.ButtonStyle.secondary, disabled=page <= 0, row=row)
    following = ui.Button(emoji="▶", style=discord.ButtonStyle.secondary, disabled=page >= pages - 1, row=row)

    async def previous_page(inter: discord.Interaction):
        await change_page(inter, -1)

    async def next_page(inter: discord.Interaction):
        await change_page(inter, 1)

    previous.callback = previous_page
    following.callback = next_page
    view.add_item(previous)
    view.add_item(following)


async def _start_guild_action(interaction: discord.Interaction, manager, user_id: int, action: str):
    if not await _ensure_authenticated(
        interaction, manager, user_id, "Você precisa estar autenticado! Use **Autenticar** primeiro."
    ):
        return
    dm = await open_temporary_dm(
        interaction,
        embed=discord.Embed(title="🏠 Servidores", description="Consultando servidores..."),
    )
    if dm is None:
        return
    stats = await manager.get_guild_stats(user_id)
    total_guilds = int(stats.get("total", 0))
    fresh = bool(stats.get("fresh"))
    warning = stats.get("warning")
    guild_ids = {int(guild_id) for guild_id in stats.get("guild_ids", [])}
    owner_ids = {int(guild_id) for guild_id in stats.get("owner_ids", [])}
    whitelist = set(manager.get_guild_whitelist(user_id))
    whitelisted = len(guild_ids & whitelist)
    protected_owners = len(owner_ids - whitelist) if action == "LEAVE" else 0
    excluded_ids = whitelist | (owner_ids if action == "LEAVE" else set())
    eligible = max(0, total_guilds - len(guild_ids & excluded_ids))

    if total_guilds == 0:
        message = (
            "✅ A conta nao participa de nenhum servidor."
            if fresh
            else "⚠️ Nao foi possivel confirmar a lista de servidores. Tente novamente."
        )
        return await dm.finish(content=message)

    if eligible == 0:
        return await dm.finish(
            content="✅ Nenhum servidor esta elegivel: todos estao protegidos pela whitelist"
            + (" ou pertencem a conta." if action == "LEAVE" else "."),
        )

    action_label = "Saida de servidores" if action == "LEAVE" else "Silenciamento"
    action_infinitive = "sair" if action == "LEAVE" else "silenciar"
    progress_verb = "Saindo" if action == "LEAVE" else "Silenciando"
    confirm_view = ui.View(timeout=CHOICE_TTL_SECONDS)

    async def confirm_callback(inter: discord.Interaction):
        await _run_confirmed_operation(
            inter,
            dm,
            manager,
            user_id,
            started=(
                f"🚀 {action_label} iniciado para ate **{eligible} servidores**.\n"
                "A lista e a whitelist serao validadas novamente antes da primeira alteracao."
            ),
            title=f"🏠 {action_label}",
            verb=progress_verb,
            action=action_label,
            start=lambda progress: manager.start_guild_cleanup(user_id, action, progress),
        )

    btn_confirm = ui.Button(
        label=f"Confirmar: {action_infinitive} ({eligible})",
        style=discord.ButtonStyle.danger,
    )
    btn_confirm.callback = confirm_callback
    confirm_view.add_item(btn_confirm)

    warning_line = f"\n⚠️ **Aviso da consulta:** {_safe_display(warning)}\n" if warning else ""
    owner_line = f"• Servidores proprios protegidos: **{protected_owners}**\n" if action == "LEAVE" else ""
    await dm.edit(
        content=(
            f"⚠️ **CONFIRMAÇÃO DE OPERAÇÃO**\n\n"
            f"Voce esta prestes a **{action_infinitive} em ate {eligible} servidores**.\n\n"
            f"**Resumo da consulta:**\n"
            f"• Servidores atuais: **{total_guilds}**\n"
            f"• Protegidos pela whitelist: **{whitelisted}**\n"
            f"{owner_line}"
            f"• Elegiveis: **{eligible}**\n"
            f"{warning_line}\n"
            "O resultado final indicara falhas, cancelamento e verificacao. Deseja prosseguir?"
        ),
        view=confirm_view,
    )
    await dm.expire_after(CHOICE_TTL_SECONDS)


async def handle_guild_whitelist(interaction: discord.Interaction, manager, user_id: int):
    if not await _ensure_authenticated(
        interaction, manager, user_id, "Voce precisa estar autenticado! Use **Autenticar** primeiro."
    ):
        return

    await interaction.response.defer(ephemeral=True)
    state = {"data": await manager.get_guilds_page(user_id, page=0, page_size=20)}

    def values():
        data = state["data"]
        items = data.get("items", [])
        whitelist = set(manager.get_guild_whitelist(user_id))
        return data, items, whitelist

    def build_embed():
        data, items, whitelist = values()
        total = int(data.get("total", len(items)))
        lines = []
        for item in items:
            guild_id = int(item.get("id", 0))
            marker = "🛡️" if guild_id in whitelist else "▫️"
            owner = " • proprio" if item.get("owner") else ""
            muted = " • silenciado" if item.get("muted") is True else ""
            lines.append(f"{marker} **{_safe_display(item.get('name'))}** (`{guild_id}`){owner}{muted}")

        embed = discord.Embed(
            title=f"🛡️ Whitelist de Servidores ({len(whitelist)})",
            color=0x3498DB,
        )
        embed.description = "\n".join(lines) if lines else "*Nenhum servidor encontrado.*"
        if data.get("warning"):
            embed.add_field(
                name="Aviso",
                value=f"⚠️ {_safe_display(data['warning'])}",
                inline=False,
            )
        page, pages = _page_state(data)
        embed.set_footer(text=(f"Pagina {page + 1}/{pages} • {total} servidores • marcados nao serao alterados"))
        return embed

    async def change_page(inter: discord.Interaction, offset: int):
        await inter.response.defer()
        current = int(state["data"].get("page", 0))
        state["data"] = await manager.get_guilds_page(user_id, page=current + offset, page_size=20)
        await inter.edit_original_response(embed=build_embed(), view=build_view())

    def build_view():
        data, items, whitelist = values()
        view = ui.View(timeout=180)

        if items:
            options = []
            for item in items:
                guild_id = int(item.get("id", 0))
                name = " ".join(str(item.get("name") or "Servidor").split())[:100]
                description = f"ID {guild_id}"
                if item.get("owner"):
                    description += " • servidor proprio"
                options.append(
                    discord.SelectOption(
                        label=name or "Servidor",
                        description=description[:100],
                        value=str(guild_id),
                        default=guild_id in whitelist,
                    )
                )

            selector = ui.Select(
                placeholder="Marque os servidores que deseja proteger",
                min_values=0,
                max_values=len(options),
                options=options,
                row=0,
            )

            async def save_page(inter: discord.Interaction):
                visible_ids = [int(item["id"]) for item in items]
                selected_ids = [int(value) for value in selector.values]
                await manager.update_guild_whitelist(user_id, visible_ids, selected_ids)
                await inter.response.edit_message(embed=build_embed(), view=build_view())

            selector.callback = save_page
            view.add_item(selector)

        page, pages = _page_state(data)
        _add_page_buttons(view, page, pages, change_page, row=1)
        clear = ui.Button(
            label="Limpar whitelist",
            style=discord.ButtonStyle.danger,
            disabled=not whitelist,
            row=1,
        )

        async def clear_all(inter: discord.Interaction):
            removed = await manager.clear_guild_whitelist(user_id)
            await inter.response.edit_message(
                content=f"Whitelist limpa: **{removed}** servidor(es) removido(s).",
                embed=build_embed(),
                view=build_view(),
            )

        clear.callback = clear_all
        view.add_item(clear)
        return view

    await interaction.followup.send(embed=build_embed(), view=build_view(), ephemeral=True)


async def handle_pending_requests(interaction: discord.Interaction, manager, user_id: int):
    if not await _ensure_authenticated(interaction, manager, user_id):
        return

    dm = await open_temporary_dm(
        interaction,
        embed=discord.Embed(title="📨 Pedidos Pendentes", description="Consultando pedidos..."),
    )
    if dm is None:
        return
    state = {"data": await manager.get_pending_requests(user_id, page=0)}

    def values():
        data = state["data"]
        incoming = data.get("incoming", [])
        outgoing = data.get("outgoing", [])
        incoming_total = int(data.get("incoming_total", len(incoming)))
        outgoing_total = int(data.get("outgoing_total", len(outgoing)))
        return data, incoming, outgoing, incoming_total, outgoing_total

    def build_embed():
        data, incoming, outgoing, incoming_total, outgoing_total = values()
        embed = discord.Embed(title="📨 Pedidos Pendentes", color=0xFAA61A)
        if data.get("warning"):
            embed.description = f"⚠️ {_safe_display(data['warning'])}"
        embed.add_field(
            name=f"📥 Recebidos ({incoming_total})",
            value=_preview_text(incoming, incoming_total, paginated=True),
            inline=False,
        )
        embed.add_field(
            name=f"📤 Enviados ({outgoing_total})",
            value=_preview_text(outgoing, outgoing_total, paginated=True),
            inline=False,
        )
        page, pages = _page_state(data)
        embed.set_footer(text=f"Pagina {page + 1}/{pages} • lista atualizada pela API")
        return embed

    async def run_pending_action(inter, *, accept: bool):
        action = "Aceitacao de pedidos" if accept else "Remocao de pedidos"
        await _run_confirmed_operation(
            inter,
            dm,
            manager,
            user_id,
            started=f"🚀 {action} iniciada. O tempo varia conforme o Discord.",
            title="👥 Gerenciando Pedidos",
            verb="Processando",
            action=action,
            start=lambda progress: (
                manager.accept_all_requests(user_id, progress)
                if accept
                else manager.deny_all_requests(user_id, progress)
            ),
        )

    async def change_page(inter: discord.Interaction, offset: int):
        await inter.response.defer()
        current = int(state["data"].get("page", 0))
        state["data"] = await manager.get_pending_requests(user_id, page=current + offset)
        await dm.edit(embed=build_embed(), view=build_view())

    def build_view():
        data, _, _, incoming_total, outgoing_total = values()
        action_view = ui.View(timeout=CHOICE_TTL_SECONDS)

        if incoming_total:
            accept_button = ui.Button(
                label=f"Aceitar Todos ({incoming_total})",
                style=discord.ButtonStyle.green,
                emoji="✅",
            )

            async def accept_all(inter: discord.Interaction):
                await run_pending_action(inter, accept=True)

            accept_button.callback = accept_all
            action_view.add_item(accept_button)

        if incoming_total or outgoing_total:
            deny_button = ui.Button(
                label=f"Remover Todos ({incoming_total + outgoing_total})",
                style=discord.ButtonStyle.danger,
                emoji="❌",
            )

            async def deny_all(inter: discord.Interaction):
                await run_pending_action(inter, accept=False)

            deny_button.callback = deny_all
            action_view.add_item(deny_button)

        page, pages = _page_state(data)
        if pages > 1:
            _add_page_buttons(action_view, page, pages, change_page)

        return action_view

    action_view = build_view()
    await dm.edit(
        embed=build_embed(),
        view=action_view if action_view.children else None,
    )
    await dm.expire_after(CHOICE_TTL_SECONDS)


async def handle_clean_friends(interaction: discord.Interaction, manager, user_id: int):
    if not await _ensure_authenticated(interaction, manager, user_id):
        return

    dm = await open_temporary_dm(
        interaction,
        embed=discord.Embed(title="🧹 Limpeza de amigos", description="Consultando amigos..."),
    )
    if dm is None:
        return
    stats = await manager.get_rel_stats(user_id)
    if not stats:
        return await dm.finish(content="❌ Nao foi possivel carregar as relacoes.")

    friends_total = int(stats.get("friends", 0))
    if not friends_total and stats.get("fresh"):
        return await dm.finish(content="✅ Nenhum amigo para remover.")
    if not friends_total:
        return await dm.finish(content="⚠️ Nao foi possivel confirmar a lista atual de amigos. Tente novamente.")

    confirm_view = ui.View(timeout=CHOICE_TTL_SECONDS)

    async def confirm_callback(inter: discord.Interaction):
        await _run_confirmed_operation(
            inter,
            dm,
            manager,
            user_id,
            started=("🚀 Limpeza de amigos iniciada.\nA lista sera atualizada novamente antes da primeira remocao."),
            title="🧹 Limpando Amizades",
            verb="Removendo",
            action="Limpeza de amigos",
            start=lambda progress: manager.start_rel_cleanup(user_id, progress),
        )

    button = ui.Button(
        label="Confirmar Remocao em Massa",
        style=discord.ButtonStyle.danger,
    )
    button.callback = confirm_callback
    confirm_view.add_item(button)

    warning = f"\n⚠️ {_safe_display(stats['warning'])} A lista sera validada novamente." if stats.get("warning") else ""
    await dm.edit(
        content=(
            "⚠️ **CONFIRMAÇÃO DE LIMPEZA**\n\n"
            f"Você está prestes a remover ate **{friends_total} amigos**.\n"
            "A duracao depende dos rate limits do Discord."
            f"{warning}\n\nDeseja prosseguir?"
        ),
        view=confirm_view,
    )
    await dm.expire_after(CHOICE_TTL_SECONDS)


async def handle_unblock_all(interaction: discord.Interaction, manager, user_id: int):
    if not await _ensure_authenticated(interaction, manager, user_id):
        return

    dm = await open_temporary_dm(
        interaction,
        embed=discord.Embed(title="🚫 Bloqueados", description="Consultando bloqueados..."),
    )
    if dm is None:
        return
    state = {"data": await manager.get_blocked_list(user_id, page=0)}
    data = state["data"]
    blocked = data.get("items", [])
    blocked_total = int(data.get("total", len(blocked)))

    if not blocked_total and data.get("fresh"):
        return await dm.finish(content="✅ Nenhum usuario bloqueado.")
    if not blocked_total:
        return await dm.finish(content="⚠️ Nao foi possivel confirmar a lista de bloqueados. Tente novamente.")

    def values():
        page_data = state["data"]
        page_items = page_data.get("items", [])
        total = int(page_data.get("total", len(page_items)))
        return page_data, page_items, total

    def build_embed():
        page_data, page_items, total = values()
        embed = discord.Embed(title=f"🚫 Bloqueados ({total})", color=0xE74C3C)
        embed.description = _preview_text(page_items, total, paginated=True)
        if page_data.get("warning"):
            embed.add_field(
                name="Aviso",
                value=f"⚠️ {_safe_display(page_data['warning'])}",
                inline=False,
            )
        page, pages = _page_state(page_data)
        embed.set_footer(text=f"Pagina {page + 1}/{pages} • validacao repetida antes da alteracao")
        return embed

    async def change_page(inter: discord.Interaction, offset: int):
        await inter.response.defer()
        current = int(state["data"].get("page", 0))
        state["data"] = await manager.get_blocked_list(user_id, page=current + offset)
        await dm.edit(embed=build_embed(), view=build_view())

    def build_view():
        page_data, _, total = values()
        action_view = ui.View(timeout=CHOICE_TTL_SECONDS)

        async def unblock_confirm(inter: discord.Interaction):
            confirm_view = ui.View(timeout=CHOICE_TTL_SECONDS)

            async def unblock_execute(inter2: discord.Interaction):
                await _run_confirmed_operation(
                    inter2,
                    dm,
                    manager,
                    user_id,
                    started=(
                        f"🚀 Desbloqueio de ate {total} usuarios iniciado.\n"
                        "A lista sera atualizada antes da primeira alteracao."
                    ),
                    title="🔓 Desbloqueando Usuarios",
                    verb="Desbloqueando",
                    action="Desbloqueio em massa",
                    start=lambda progress: manager.unblock_all(user_id, progress),
                )

            confirm = ui.Button(
                label="SIM, Desbloquear Todos",
                style=discord.ButtonStyle.danger,
                emoji="⚠️",
            )
            confirm.callback = unblock_execute
            confirm_view.add_item(confirm)
            await inter.response.defer()
            await dm.edit(
                content=(
                    "⚠️ **TEM CERTEZA?**\n\n"
                    f"Voce vai desbloquear ate **{total} usuarios**.\n"
                    "A duracao depende dos rate limits do Discord."
                ),
                view=confirm_view,
            )

        unblock_button = ui.Button(
            label=f"Desbloquear Todos ({total})",
            style=discord.ButtonStyle.green,
            emoji="🔓",
        )
        unblock_button.callback = unblock_confirm
        action_view.add_item(unblock_button)

        page, pages = _page_state(page_data)
        if pages > 1:
            _add_page_buttons(action_view, page, pages, change_page)

        return action_view

    await dm.edit(embed=build_embed(), view=build_view())
    await dm.expire_after(CHOICE_TTL_SECONDS)


async def handle_mute_servers(interaction: discord.Interaction, manager, user_id: int):
    await _start_guild_action(interaction, manager, user_id, "MUTE")


async def handle_leave_servers(interaction: discord.Interaction, manager, user_id: int):
    await _start_guild_action(interaction, manager, user_id, "LEAVE")
