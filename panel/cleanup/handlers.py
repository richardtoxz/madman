import discord
from discord import ui

from .modals import CleanDMModal, CleanGuildModal
from ..shared.helpers import generate_progress_bar
from ..shared.interaction import send_modal_safely
from ..shared.stealth_dm import CHOICE_TTL_SECONDS, open_temporary_dm


def _require_auth(manager, user_id: int) -> bool:
    is_auth = getattr(manager, "is_authenticated", None)
    if callable(is_auth):
        return bool(is_auth(user_id))
    return user_id in getattr(manager, "active_processes", {})


def _cancel_running_view(manager, user_id: int, dm) -> ui.View:
    view = ui.View(timeout=None)
    button = ui.Button(
        label="Cancelar operacao",
        emoji="⏹️",
        style=discord.ButtonStyle.danger,
    )

    async def cancel_callback(interaction: discord.Interaction):
        if interaction.user.id != user_id:
            return await interaction.response.send_message("Esta operacao pertence a outro usuario.", ephemeral=True)
        await interaction.response.defer()
        await manager.control_clean(user_id, "CANCEL")
        await dm.edit(
            content="⏹️ Cancelamento solicitado. Finalizando o item atual...",
            view=None,
        )

    button.callback = cancel_callback
    view.add_item(button)
    return view


async def handle_clean_dm(interaction: discord.Interaction, manager, user_id: int):
    if not _require_auth(manager, user_id):
        return await interaction.response.send_message(
            "❌ Você precisa estar autenticado! Use **Autenticar** primeiro.", ephemeral=True
        )
    return await send_modal_safely(interaction, CleanDMModal(manager, user_id))


async def handle_clean_all_dms(interaction: discord.Interaction, manager, user_id: int):
    if not _require_auth(manager, user_id):
        return await interaction.response.send_message(
            "❌ Você precisa estar autenticado! Use **Autenticar** primeiro.", ephemeral=True
        )

    dm = await open_temporary_dm(
        interaction,
        embed=discord.Embed(title="🧹 Limpeza de conversas", description="🔍 Buscando DMs abertas..."),
    )
    if dm is None:
        return

    dms = await manager.list_open_dms(user_id)

    if not dms:
        return await dm.finish(content="✅ Nenhuma conversa privada aberta encontrada no momento.")

    state = {"index": 0, "dms": list(dms)}

    def build_embed():
        dm_list = state["dms"]
        idx = state["index"]
        dm = dm_list[idx]

        name = dm.get("global_name") or dm.get("name", "Desconhecido")
        username = dm.get("name", "?")
        uid = dm.get("user_id", "?")
        typ = dm.get("type", "dm")
        typ_label = "👤 DM" if typ == "dm" else "👥 Group"
        avatar = dm.get("avatar_url", "")

        embed = discord.Embed(
            title=f"{name}",
            description=(f"**Username:** `@{username}`\n**ID:** `{uid}`\n**Tipo:** {typ_label}"),
            color=0x57F287 if typ == "dm" else 0x5865F2,
        )
        if avatar:
            embed.set_thumbnail(url=avatar)
        embed.set_footer(text=f"DM {idx + 1} de {len(dm_list)}  •  {len(dm_list)} abertas no total")
        return embed

    def build_view():
        view = ui.View(timeout=CHOICE_TTL_SECONDS)
        dm_list = state["dms"]
        idx = state["index"]

        btn_prev = ui.Button(emoji="◀", style=discord.ButtonStyle.secondary, disabled=(idx == 0))
        btn_next = ui.Button(emoji="▶", style=discord.ButtonStyle.secondary, disabled=(idx >= len(dm_list) - 1))
        btn_close = ui.Button(label="Fechar esta DM", emoji="❌", style=discord.ButtonStyle.danger)
        btn_clean = ui.Button(
            label=f"Limpar {len(dm_list)} conversas", emoji="🧹", style=discord.ButtonStyle.danger, row=1
        )

        async def prev_cb(i: discord.Interaction):
            state["index"] = max(0, state["index"] - 1)
            await i.response.defer()
            await dm.edit(embed=build_embed(), view=build_view())

        async def next_cb(i: discord.Interaction):
            state["index"] = min(len(state["dms"]) - 1, state["index"] + 1)
            await i.response.defer()
            await dm.edit(embed=build_embed(), view=build_view())

        async def close_dm_cb(i: discord.Interaction):
            await i.response.defer()
            conversation = state["dms"][state["index"]]
            ch_id = conversation.get("id")
            success, message = await manager.close_single_dm(user_id, ch_id)
            if not success:
                return await i.followup.send(f"❌ Nao foi possivel fechar a DM: {message}", ephemeral=True)
            state["dms"].pop(state["index"])
            if not state["dms"]:
                return await dm.finish(content="✅ Todas as DMs foram fechadas!")
            state["index"] = min(state["index"], len(state["dms"]) - 1)
            await dm.edit(embed=build_embed(), view=build_view())

        async def clean_all_cb(i: discord.Interaction):
            await i.response.defer()
            dm.hold()
            total_dms_count = len(state["dms"])

            await dm.edit(
                content=f"Limpeza iniciada — **{total_dms_count} conversas** na fila.\nAguarde...",
                view=_cancel_running_view(manager, user_id, dm),
            )

            prog = {
                "dm_idx": 0,
                "dm_total": total_dms_count,
                "dm_name": "",
                "dm_msgs": 0,
            }

            async def safe_update(content: str, *, throttle: bool = False):
                await dm.edit(content=content, throttle=throttle)

            async def progress(curr, total, status, extra):
                if status == "ALL_DMS_START":
                    prog["dm_total"] = total
                    await safe_update(f"**{total} conversas** encontradas. Iniciando limpeza...")

                elif status == "ALL_DMS_CHANNEL":
                    prog["dm_idx"] = curr
                    prog["dm_total"] = total
                    prog["dm_name"] = extra
                    prog["dm_msgs"] = 0
                    await safe_update(f"**DM {curr}/{total}** — `{extra}`\nColetando mensagens...")

                elif status == "ALL_DMS_FOUND":
                    count_str = extra.split(":")[-1].strip().split(" ")[0]
                    prog["dm_msgs"] = int(count_str) if count_str.isdigit() else 0
                    await safe_update(
                        f"**DM {prog['dm_idx']}/{prog['dm_total']}** — `{prog['dm_name']}`\n"
                        f"{prog['dm_msgs']} mensagens encontradas. Deletando..."
                    )

                elif status == "CLEANING" and curr is not None and total:
                    bar = generate_progress_bar(curr, total)
                    await safe_update(
                        f"**DM {prog['dm_idx']}/{prog['dm_total']}** — `{prog['dm_name']}`\n"
                        f"{bar} `{curr}/{total} msgs`",
                        throttle=bool(curr < total),
                    )

            try:
                result = await manager.clean_all_dms(user_id, update_callback=progress)
            except Exception:
                await dm.finish(content="❌ Falha inesperada ao acompanhar a limpeza.")
                raise
            raw_result = result or "ERRO:Sem resposta do worker"
            status, separator, detail = raw_result.partition(":")
            result_clean = detail.strip() if separator else raw_result.strip()
            status = status.upper()

            if status == "COMPLETADO":
                title = "🧹 Limpeza Em Massa Concluida"
                color = 0x2ECC71
            elif status == "PARCIAL":
                title = "⚠️ Limpeza Em Massa Parcial"
                color = 0xF1C40F
            elif status == "CANCELADO":
                title = "⏹️ Limpeza Em Massa Cancelada"
                color = 0xE67E22
            else:
                title = "❌ Limpeza Em Massa Interrompida"
                color = 0xE74C3C

            await dm.finish(embed=discord.Embed(title=title, description=f"**Resultado:** {result_clean}", color=color))

        btn_prev.callback = prev_cb
        btn_next.callback = next_cb
        btn_close.callback = close_dm_cb
        btn_clean.callback = clean_all_cb

        view.add_item(btn_prev)
        view.add_item(btn_next)
        view.add_item(btn_close)
        view.add_item(btn_clean)
        return view

    await dm.edit(embed=build_embed(), view=build_view())
    await dm.expire_after(CHOICE_TTL_SECONDS)


async def handle_clean_guild(interaction: discord.Interaction, manager, user_id: int):
    if not _require_auth(manager, user_id):
        return await interaction.response.send_message(
            "❌ Você precisa estar autenticado! Use **Autenticar** primeiro.", ephemeral=True
        )
    return await send_modal_safely(interaction, CleanGuildModal(manager, user_id))


async def handle_close_dms(interaction: discord.Interaction, manager, user_id: int):
    if not _require_auth(manager, user_id):
        return await interaction.response.send_message(
            "❌ Você precisa estar autenticado! Use **Autenticar** primeiro.", ephemeral=True
        )

    action_view = ui.View(timeout=CHOICE_TTL_SECONDS)
    dm = await open_temporary_dm(
        interaction,
        embed=discord.Embed(title="🚪 Fechar conversas", description="O que deseja fechar?"),
    )
    if dm is None:
        return

    async def close_all_dm_callback(inter: discord.Interaction):
        await inter.response.defer()
        dm.hold()
        await dm.edit(
            content="🚪 Fechando todas as DMs...",
            view=_cancel_running_view(manager, user_id, dm),
        )

        async def progress(curr, total, status, extra):
            try:
                if status in ("STARTING", "CLOSING"):
                    await dm.edit(
                        content=f"🚪 Fechando DMs... **{curr}/{total}**",
                        throttle=bool(curr and total and curr < total),
                    )
            except Exception:
                pass

        try:
            result = await manager.close_all_dms(user_id, update_callback=progress)
        except Exception:
            await dm.finish(content="❌ Falha inesperada ao fechar as DMs.")
            raise
        await dm.finish(content=f"**Operacao finalizada**\n{result}")

    async def leave_all_groups_callback(inter: discord.Interaction):
        await inter.response.defer()
        dm.hold()
        await dm.edit(
            content="🚪 Saindo de todos os Groups...",
            view=_cancel_running_view(manager, user_id, dm),
        )

        async def progress(curr, total, status, extra):
            try:
                if status in ("STARTING", "LEAVING"):
                    await dm.edit(
                        content=f"🚪 Saindo de Groups... **{curr}/{total}**",
                        throttle=bool(curr and total and curr < total),
                    )
            except Exception:
                pass

        try:
            result = await manager.leave_all_group_dms(user_id, update_callback=progress)
        except Exception:
            await dm.finish(content="❌ Falha inesperada ao sair dos grupos.")
            raise
        await dm.finish(content=f"**Operacao finalizada**\n{result}")

    btn_dms = ui.Button(label="Fechar Todas DMs", style=discord.ButtonStyle.danger, emoji="💬")
    btn_dms.callback = close_all_dm_callback
    action_view.add_item(btn_dms)

    btn_groups = ui.Button(label="Sair de Todos Groups", style=discord.ButtonStyle.danger, emoji="👥")
    btn_groups.callback = leave_all_groups_callback
    action_view.add_item(btn_groups)

    await dm.edit(view=action_view)
    await dm.expire_after(CHOICE_TTL_SECONDS)
