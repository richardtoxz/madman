import discord


async def handle_protection(interaction: discord.Interaction, manager, user_id: int):
    if not manager.is_authenticated(user_id):
        return await interaction.response.send_message(
            "Você precisa estar autenticado! Use **Autenticar** primeiro.", ephemeral=True
        )

    await interaction.response.defer(ephemeral=True)

    status = await manager.protection_status(user_id)

    if status and status.get("enabled"):
        embed = discord.Embed(
            title="🛡️ Proteções Ativas",
            color=0x2ecc71,
        )
        embed.add_field(
            name="Scams bloqueados",
            value=str(status.get("scams_blocked", 0)),
            inline=True,
        )
        embed.add_field(
            name="Spam fechado",
            value=str(status.get("spam_closed", 0)),
            inline=True,
        )
        embed.add_field(
            name="Floods de DM",
            value=str(status.get("dm_floods", 0)),
            inline=True,
        )
        embed.add_field(
            name="Floods de FR",
            value=str(status.get("fr_floods", 0)),
            inline=True,
        )
        embed.add_field(
            name="FRs rejeitados",
            value=str(status.get("fr_rejected", 0)),
            inline=True,
        )
        embed.set_footer(text="Selecione novamente para desativar.")

        await manager.protection_disable(user_id)
        embed.title = "🛡️ Proteções Desativadas"
        embed.color = 0xe74c3c
        embed.set_footer(text="Stats da sessão anterior mostradas acima.")

        await interaction.followup.send(embed=embed, ephemeral=True)

    else:
        await manager.protection_enable(user_id)
        embed = discord.Embed(
            title="🛡️ Proteções Ativadas",
            description=(
                "**DM Link Shield** — Scams e spam serão detectados e fechados automaticamente.\n"
                "**Anti-Raid** — Floods de DM e friend requests serão bloqueados."
            ),
            color=0x2ecc71,
        )
        embed.set_footer(text="Selecione novamente para desativar e ver stats.")
        await interaction.followup.send(embed=embed, ephemeral=True)
