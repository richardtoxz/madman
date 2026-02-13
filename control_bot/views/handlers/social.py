import asyncio
import discord
from discord import ui

from utils.helpers import generate_progress_bar
from utils.stealth_dm import send_stealth_dm


async def handle_pending_requests(interaction: discord.Interaction, manager, user_id: int):
    if user_id not in manager.active_processes:
        return await interaction.response.send_message(
            "❌ Você precisa estar autenticado! Use **Autenticar** primeiro.", ephemeral=True
        )

    await interaction.response.defer(ephemeral=True)
    data = await manager.get_pending_requests(user_id)

    incoming = data.get("incoming", [])
    outgoing = data.get("outgoing", [])

    embed = discord.Embed(title="📨 Pedidos Pendentes", color=0xfaa61a)
    incoming_text = "\n".join([f"• **{r['name']}** (`{r['id']}`)" for r in incoming[:25]]) or "*Nenhum*"
    outgoing_text = "\n".join([f"• **{r['name']}** (`{r['id']}`)" for r in outgoing[:25]]) or "*Nenhum*"
    embed.add_field(name=f"📥 Recebidos ({len(incoming)})", value=incoming_text, inline=False)
    embed.add_field(name=f"📤 Enviados ({len(outgoing)})", value=outgoing_text, inline=False)
    embed.set_footer(text="⚠️ Aceitar/Negar em massa respeita rate limits (~5s entre cada)")

    action_view = ui.View(timeout=120)

    if incoming:
        async def accept_all(inter: discord.Interaction):
            await inter.response.edit_message(
                content="🚀 Aceitando pedidos... Isso pode demorar. Respeita rate limits.",
                embed=None, view=None
            )
            from utils.stealth_dm import cleanup_stealth_messages
            asyncio.create_task(cleanup_stealth_messages(inter.user, inter.client.user.id))

            result = await manager.accept_all_requests(user_id)
            try:
                await inter.edit_original_response(content=f"✅ **Concluído!**\n{result}")
                if "✅" in result or "OK" in result.upper():
                    await send_stealth_dm(
                        inter, title="👥 Amizades Madman",
                        description=f"**Ação:** Aceitar todos os pedidos\n**Resultado:** {result}",
                        color=0x2ecc71
                    )
            except Exception:
                pass

        btn_accept = ui.Button(
            label=f"Aceitar Todos ({len(incoming)})",
            style=discord.ButtonStyle.green, emoji="✅"
        )
        btn_accept.callback = accept_all
        action_view.add_item(btn_accept)

    if incoming or outgoing:
        async def deny_all(inter: discord.Interaction):
            await inter.response.edit_message(
                content="🚀 Negando pedidos... Isso pode demorar. Respeita rate limits.",
                embed=None, view=None
            )
            from utils.stealth_dm import cleanup_stealth_messages
            asyncio.create_task(cleanup_stealth_messages(inter.user, inter.client.user.id))

            result = await manager.deny_all_requests(user_id)
            try:
                await inter.edit_original_response(content=f"✅ **Concluído!**\n{result}")
                if "✅" in result or "OK" in result.upper():
                    await send_stealth_dm(
                        inter, title="👥 Amizades Madman",
                        description=f"**Ação:** Negar/Remover todos os pedidos\n**Resultado:** {result}",
                        color=0x2ecc71
                    )
            except Exception:
                pass

        btn_deny = ui.Button(
            label=f"Negar Todos ({len(incoming) + len(outgoing)})",
            style=discord.ButtonStyle.danger, emoji="❌"
        )
        btn_deny.callback = deny_all
        action_view.add_item(btn_deny)

    await interaction.followup.send(embed=embed, view=action_view, ephemeral=True)


async def handle_clean_friends(interaction: discord.Interaction, manager, user_id: int):
    if user_id not in manager.active_processes:
        return await interaction.response.send_message(
            "❌ Você precisa estar autenticado! Use **Autenticar** primeiro.", ephemeral=True
        )

    await interaction.response.defer(ephemeral=True)

    stats = await manager.get_rel_stats(user_id)
    if not stats or stats.get("friends", 0) == 0:
        return await interaction.followup.send(
            "❌ Ninguém para remover ou estatísticas não carregadas.", ephemeral=True
        )

    friends_total = stats.get("friends", 0)
    est_min = max(1, friends_total * 4 // 60)
    est_max = max(1, friends_total * 7 // 60)

    confirm_view = ui.View(timeout=60)

    async def confirm_callback(inter: discord.Interaction):
        await inter.response.edit_message(
            content=(
                f"🚀 Iniciando limpeza de amigos...\n"
                f"⚠️ Estimativa: ~{est_min}-{est_max} minutos para ~{friends_total} amigos.\n"
                f"Respeita rate limits automaticamente."
            ),
            embed=None, view=None
        )
        from utils.stealth_dm import cleanup_stealth_messages
        asyncio.create_task(cleanup_stealth_messages(inter.user, inter.client.user.id))

        async def progress_callback(curr, total, status, name):
            try:
                embed = discord.Embed(title="🧹 Limpando Amizades", color=0xf1c40f)
                if status == "STARTING":
                    embed.description = f"Preparando para remover **{total}** amigos..."
                elif status == "REMOVING":
                    bar = generate_progress_bar(curr, total)
                    embed.description = f"Removendo: **{name}**\nProgresso: **{curr}/{total}**\n\n{bar}"
                elif status == "BATCH_PAUSE":
                    embed.description = f"⏸️ {name}\nProgresso: **{curr}/{total}**"
                    embed.color = 0xe67e22
                elif status == "RATE_LIMITED":
                    embed.description = f"⏳ {name}"
                    embed.color = 0xe74c3c
                elif status == "ERROR":
                    embed.description = f"⚠️ Erro: {name}"
                    embed.color = 0xe74c3c
                else:
                    return
                await inter.edit_original_response(embed=embed)
            except Exception:
                pass

        result = await manager.start_rel_cleanup(user_id, progress_callback)
        try:
            await inter.edit_original_response(content=f"✅ **Limpeza concluída!**\n{result}", embed=None)
            if "✅" in result or "COMPLETADO" in result.upper():
                await send_stealth_dm(
                    inter, title="🧹 Limpeza de Amigos Madman",
                    description=f"**Resultado:** {result}", color=0x2ecc71
                )
        except Exception:
            pass

    btn_confirm = ui.Button(label="Confirmar Exclusão em Massa", style=discord.ButtonStyle.danger)
    btn_confirm.callback = confirm_callback
    confirm_view.add_item(btn_confirm)

    await interaction.followup.send(
        content=(
            f"⚠️ **CONFIRMAÇÃO DE LIMPEZA**\n\n"
            f"Você está prestes a remover **todos os seus amigos**.\n\n"
            f"📊 **Resumo:**\n"
            f"• Total de amigos: **{friends_total}**\n"
            f"• ⏱️ Estimativa: **{est_min}-{est_max} minutos**\n\n"
            f"⚠️ Operação respeita rate limits automaticamente.\n"
            f"Deseja prosseguir?"
        ),
        view=confirm_view,
        ephemeral=True
    )


async def handle_unblock_all(interaction: discord.Interaction, manager, user_id: int):
    if user_id not in manager.active_processes:
        return await interaction.response.send_message(
            "❌ Você precisa estar autenticado! Use **Autenticar** primeiro.", ephemeral=True
        )

    await interaction.response.defer(ephemeral=True)
    blocked = await manager.get_blocked_list(user_id)

    if not blocked:
        return await interaction.followup.send("Nenhum usuário bloqueado.", ephemeral=True)

    est_min = max(1, len(blocked) * 4 // 60)
    est_max = max(1, len(blocked) * 7 // 60)

    embed = discord.Embed(title=f"🚫 Bloqueados ({len(blocked)})", color=0xe74c3c)
    text = "\n".join([f"• **{b['name']}** (`{b['id']}`)" for b in blocked[:25]])
    if len(blocked) > 25:
        text += f"\n... e mais {len(blocked) - 25}"
    embed.description = text or "*Nenhum bloqueado*"
    embed.set_footer(text=f"⏱️ Desbloquear todos: ~{est_min}-{est_max} minutos")

    action_view = ui.View(timeout=120)

    async def unblock_confirm(inter: discord.Interaction):
        confirm_view = ui.View(timeout=60)

        async def unblock_execute(inter2: discord.Interaction):
            await inter2.response.edit_message(
                content=(
                    f"🚀 Desbloqueando {len(blocked)} usuários...\n"
                    f"⚠️ Estimativa: ~{est_min}-{est_max} minutos.\n"
                    f"Respeita rate limits automaticamente."
                ),
                embed=None, view=None
            )
            from utils.stealth_dm import cleanup_stealth_messages
            asyncio.create_task(cleanup_stealth_messages(inter2.user, inter2.client.user.id))

            result = await manager.unblock_all(user_id)
            try:
                await inter2.edit_original_response(content=f"✅ **Concluído!**\n{result}")
                if "✅" in result or "OK" in result.upper():
                    await send_stealth_dm(
                        inter2, title="🚫 Bloqueios Madman",
                        description=f"**Ação:** Desbloquear todos os usuários\n**Resultado:** {result}",
                        color=0x2ecc71
                    )
            except Exception:
                pass

        btn_confirm = ui.Button(label="SIM, Desbloquear Todos", style=discord.ButtonStyle.danger, emoji="⚠️")
        btn_confirm.callback = unblock_execute
        confirm_view.add_item(btn_confirm)

        await inter.response.edit_message(
            content=(
                f"⚠️ **TEM CERTEZA?**\n\n"
                f"Você vai desbloquear **{len(blocked)}** usuários.\n"
                f"Isso **não pode ser desfeito** facilmente.\n"
                f"Estimativa: **{est_min}-{est_max} minutos**"
            ),
            embed=None, view=confirm_view
        )

    btn = ui.Button(label=f"Desbloquear Todos ({len(blocked)})", style=discord.ButtonStyle.green, emoji="🔓")
    btn.callback = unblock_confirm
    action_view.add_item(btn)

    await interaction.followup.send(embed=embed, view=action_view, ephemeral=True)
