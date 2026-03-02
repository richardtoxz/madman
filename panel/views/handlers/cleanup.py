import asyncio
import discord
from discord import ui

from modals.clean_dm_modal import CleanDMModal
from modals.clean_guild_modal import CleanGuildModal
from utils.helpers import generate_progress_bar
from utils.stealth_dm import send_stealth_dm


def _require_auth(manager, user_id: int):
    return user_id in manager.active_processes


async def handle_clean_dm(interaction: discord.Interaction, manager, user_id: int):
    if not _require_auth(manager, user_id):
        return await interaction.response.send_message(
            "❌ Você precisa estar autenticado! Use **Autenticar** primeiro.", ephemeral=True
        )
    await interaction.response.send_modal(CleanDMModal(manager))


async def handle_clean_all_dms(interaction: discord.Interaction, manager, user_id: int):
    if not _require_auth(manager, user_id):
        return await interaction.response.send_message(
            "❌ Você precisa estar autenticado! Use **Autenticar** primeiro.", ephemeral=True
        )

    await interaction.response.send_message(
        content="🔍 Buscando DMs abertas...",
        ephemeral=True
    )

    dms = await manager.list_open_dms(user_id)

    if not dms:
        return await interaction.edit_original_response(
            content="✅ Nenhuma DM aberta encontrada no momento.",
            embed=None,
            view=None
        )

    # Estado da navegação (mutável via lista para closures)
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
            description=(
                f"**Username:** `@{username}`\n"
                f"**ID:** `{uid}`\n"
                f"**Tipo:** {typ_label}"
            ),
            color=0x57f287 if typ == "dm" else 0x5865f2
        )
        if avatar:
            embed.set_thumbnail(url=avatar)
        embed.set_footer(text=f"DM {idx + 1} de {len(dm_list)}  •  {len(dm_list)} abertas no total")
        return embed

    def build_view(inter_ref):
        view = ui.View(timeout=120)
        dm_list = state["dms"]
        idx = state["index"]

        btn_prev = ui.Button(emoji="◀", style=discord.ButtonStyle.secondary, disabled=(idx == 0))
        btn_next = ui.Button(emoji="▶", style=discord.ButtonStyle.secondary, disabled=(idx >= len(dm_list) - 1))
        btn_close = ui.Button(label="Fechar esta DM", emoji="❌", style=discord.ButtonStyle.danger)
        btn_clean = ui.Button(label=f"Limpar {len(dm_list)} DMs", emoji="🧹", style=discord.ButtonStyle.danger, row=1)

        async def prev_cb(i: discord.Interaction):
            state["index"] = max(0, state["index"] - 1)
            await i.response.edit_message(embed=build_embed(), view=build_view(i))

        async def next_cb(i: discord.Interaction):
            state["index"] = min(len(state["dms"]) - 1, state["index"] + 1)
            await i.response.edit_message(embed=build_embed(), view=build_view(i))

        async def close_dm_cb(i: discord.Interaction):
            dm = state["dms"][state["index"]]
            ch_id = dm.get("id")
            # Envia DM_CLOSE via IPC
            await manager.close_single_dm(user_id, ch_id)
            # Remove da lista local
            state["dms"].pop(state["index"])
            if not state["dms"]:
                return await i.response.edit_message(
                    content="✅ Todas as DMs foram fechadas!",
                    embed=None, view=None
                )
            # Ajusta índice se necessário
            state["index"] = min(state["index"], len(state["dms"]) - 1)
            await i.response.edit_message(embed=build_embed(), view=build_view(i))

        async def clean_all_cb(i: discord.Interaction):
            total_dms_count = len(state["dms"])

            # Fechar a ephemeral imediatamente — o progresso vai por DM real
            await i.response.edit_message(
                content=f"Limpeza iniciada. Acompanhe o progresso na sua **DM com o bot**.",
                embed=None, view=None
            )

            from utils.stealth_dm import cleanup_stealth_messages
            asyncio.create_task(cleanup_stealth_messages(i.user, i.client.user.id))

            prog = {
                "dm_idx": 0, "dm_total": total_dms_count,
                "dm_name": "", "dm_msgs": 0,
            }

            # Abre DM real com o usuário e envia a mensagem de progresso inicial
            try:
                dm_channel = await i.user.create_dm()
                prog_msg = await dm_channel.send(
                    f"Limpeza de DMs iniciada — **{total_dms_count} DMs** na fila.\nAguarde..."
                )
            except Exception:
                prog_msg = None

            async def safe_update(content: str):
                if prog_msg:
                    try:
                        await prog_msg.edit(content=content)
                    except Exception:
                        pass

            async def progress(curr, total, status, extra):
                if status == "ALL_DMS_START":
                    prog["dm_total"] = total
                    await safe_update(f"**{total} DMs** encontradas. Iniciando limpeza...")

                elif status == "ALL_DMS_CHANNEL":
                    prog["dm_idx"] = curr
                    prog["dm_total"] = total
                    prog["dm_name"] = extra
                    prog["dm_msgs"] = 0
                    await safe_update(
                        f"**DM {curr}/{total}** — `{extra}`\n"
                        f"Coletando mensagens..."
                    )

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
                        f"{bar} `{curr}/{total} msgs`"
                    )

            result = await manager.clean_all_dms(user_id, update_callback=progress)
            result_clean = (result or "").replace("COMPLETADO:", "").replace("CANCELADO:", "Cancelado —").strip()
            
            if prog_msg:
                try:
                    await prog_msg.delete()
                except Exception:
                    pass
                    
            await send_stealth_dm(
                interaction=i,
                title="🧹 Limpeza Em Massa Concluída",
                description=f"**Resultado:** {result_clean}",
                color=0x2ecc71
            )

        btn_prev.callback = prev_cb
        btn_next.callback = next_cb
        btn_close.callback = close_dm_cb
        btn_clean.callback = clean_all_cb

        view.add_item(btn_prev)
        view.add_item(btn_next)
        view.add_item(btn_close)
        view.add_item(btn_clean)
        return view

    await interaction.edit_original_response(
        content=None,
        embed=build_embed(),
        view=build_view(interaction)
    )


async def handle_clean_guild(interaction: discord.Interaction, manager, user_id: int):
    if not _require_auth(manager, user_id):
        return await interaction.response.send_message(
            "❌ Você precisa estar autenticado! Use **Autenticar** primeiro.", ephemeral=True
        )
    await interaction.response.send_modal(CleanGuildModal(manager))


async def handle_close_dms(interaction: discord.Interaction, manager, user_id: int):
    if not _require_auth(manager, user_id):
        return await interaction.response.send_message(
            "❌ Você precisa estar autenticado! Use **Autenticar** primeiro.", ephemeral=True
        )

    action_view = ui.View(timeout=60)

    async def close_all_dm_callback(inter: discord.Interaction):
        await inter.response.edit_message(content="🚪 Fechando todas as DMs...", view=None)

        async def progress(curr, total, status, extra):
            try:
                if status in ("STARTING", "CLOSING"):
                    await inter.edit_original_response(
                        content=f"🚪 Fechando DMs... **{curr}/{total}**"
                    )
            except Exception:
                pass

        result = await manager.close_all_dms(user_id, update_callback=progress)
        try:
            await inter.edit_original_response(content=f"**Concluído!**\n{result}")
        except Exception:
            pass

    async def leave_all_groups_callback(inter: discord.Interaction):
        await inter.response.edit_message(content="🚪 Saindo de todos os Groups...", view=None)

        async def progress(curr, total, status, extra):
            try:
                if status in ("STARTING", "LEAVING"):
                    await inter.edit_original_response(
                        content=f"🚪 Saindo de Groups... **{curr}/{total}**"
                    )
            except Exception:
                pass

        result = await manager.leave_all_group_dms(user_id, update_callback=progress)
        try:
            await inter.edit_original_response(content=f"**Concluído!**\n{result}")
        except Exception:
            pass

    btn_dms = ui.Button(label="Fechar Todas DMs", style=discord.ButtonStyle.danger, emoji="💬")
    btn_dms.callback = close_all_dm_callback
    action_view.add_item(btn_dms)

    btn_groups = ui.Button(label="Sair de Todos Groups", style=discord.ButtonStyle.danger, emoji="👥")
    btn_groups.callback = leave_all_groups_callback
    action_view.add_item(btn_groups)

    await interaction.response.send_message(
        content="🚪 **O que deseja fechar?**",
        view=action_view, ephemeral=True
    )
