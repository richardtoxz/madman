import discord
from discord import ui
import math


class FriendsListView(ui.View):
    """Lista de amigos com status, atividade, plataforma, apelido e filtros"""

    def __init__(self, friends: list, user_id: int):
        super().__init__(timeout=180)
        self.friends = friends
        self.user_id = user_id
        self.page = 0
        self.per_page = 8
        self.filter_mode = "all"
        self._filtered = friends
        self._recalc_pages()
        self._update_buttons()

    def _recalc_pages(self):
        self.total_pages = max(1, math.ceil(len(self._filtered) / self.per_page))

    def _apply_filter(self):
        if self.filter_mode == "all":
            self._filtered = self.friends
        elif self.filter_mode == "online":
            self._filtered = [f for f in self.friends if f.get("status") in ("online", "idle", "dnd")]
        elif self.filter_mode == "offline":
            self._filtered = [f for f in self.friends if f.get("status") == "offline"]
        elif self.filter_mode == "mobile":
            self._filtered = [f for f in self.friends if f.get("on_mobile")]
        elif self.filter_mode == "playing":
            self._filtered = [f for f in self.friends if f.get("activity")]

        self._recalc_pages()
        self.page = min(self.page, self.total_pages - 1)

    def _status_emoji(self, status: str) -> str:
        return {
            "online": "🟢", "idle": "🌙", "dnd": "⛔",
            "offline": "⚫", "invisible": "👻"
        }.get(status, "❓")

    def _platform_icons(self, data: dict) -> str:
        parts = []
        if data.get("desktop_status") not in (None, "offline"):
            parts.append("🖥️")
        if data.get("mobile_status") not in (None, "offline"):
            parts.append("📱")
        if data.get("web_status") not in (None, "offline"):
            parts.append("🌐")
        return " ".join(parts) if parts else ""

    def _get_page_content(self):
        self._apply_filter()
        start = self.page * self.per_page
        end = start + self.per_page
        current = self._filtered[start:end]

        # Stats rápidas
        online = sum(1 for f in self.friends if f.get("status") in ("online", "idle", "dnd"))
        offline = len(self.friends) - online

        filter_labels = {
            "all": "Todos", "online": "Online", "offline": "Offline",
            "mobile": "Mobile", "playing": "Jogando"
        }

        embeds = []
        header = discord.Embed(
            title=f"👫 Amigos ({len(self._filtered)}/{len(self.friends)})",
            description=(
                f"🟢 **Online:** {online} • ⚫ **Offline:** {offline}\n"
                f"🔍 Filtro: **{filter_labels.get(self.filter_mode, self.filter_mode)}**"
            ),
            color=0x5865F2
        )
        header.set_footer(text=f"Página {self.page + 1}/{self.total_pages} • Use 🔍 para filtrar")
        embeds.append(header)

        for f in current:
            status_emoji = self._status_emoji(f.get("status", "offline"))
            platform = self._platform_icons(f)

            desc_parts = [f"🆔 `{f['id']}` (<@{f['id']}>)"]

            if f.get("nick"):
                desc_parts.append(f"📝 Apelido: **{f['nick']}**")

            if f.get("since"):
                desc_parts.append(f"📅 Amigos desde: <t:{f['since']}:R>")

            if f.get("activity"):
                desc_parts.append(f"🎮 {f['activity']}")

            if platform:
                desc_parts.append(f"📡 {platform}")

            status_colors = {
                "online": 0x43b581, "idle": 0xfaa61a,
                "dnd": 0xf04747, "offline": 0x747f8d
            }
            e = discord.Embed(
                description="\n".join(desc_parts),
                color=status_colors.get(f.get("status", "offline"), 0x2b2d31)
            )
            e.set_author(name=f"{status_emoji} {f['name']}", icon_url=f.get('avatar_url'))
            embeds.append(e)

        if not current:
            header.description += "\n\n*Nenhum amigo neste filtro.*"

        return embeds

    def _update_buttons(self):
        for item in self.children:
            if isinstance(item, ui.Button):
                if item.label == "Anterior":
                    item.disabled = self.page == 0
                elif item.label == "Próxima":
                    item.disabled = self.page >= self.total_pages - 1

    @ui.button(label="Anterior", style=discord.ButtonStyle.secondary, emoji="⬅️")
    async def prev_button(self, interaction: discord.Interaction, button: ui.Button):
        self.page = max(0, self.page - 1)
        self._update_buttons()
        await interaction.response.edit_message(embeds=self._get_page_content(), view=self)

    @ui.button(label="Próxima", style=discord.ButtonStyle.secondary, emoji="➡️")
    async def next_button(self, interaction: discord.Interaction, button: ui.Button):
        self.page = min(self.total_pages - 1, self.page + 1)
        self._update_buttons()
        await interaction.response.edit_message(embeds=self._get_page_content(), view=self)

    @ui.button(label="Filtro", style=discord.ButtonStyle.primary, emoji="🔍")
    async def filter_button(self, interaction: discord.Interaction, button: ui.Button):
        cycle = ["all", "online", "offline", "mobile", "playing"]
        idx = cycle.index(self.filter_mode)
        self.filter_mode = cycle[(idx + 1) % len(cycle)]
        self.page = 0
        self._update_buttons()
        await interaction.response.edit_message(embeds=self._get_page_content(), view=self)

    @ui.button(label="Voltar", style=discord.ButtonStyle.danger, emoji="🔙")
    async def back_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(content="Menu de amigos fechado.", embeds=[], view=None)