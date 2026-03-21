import discord

from ..shared.interaction import is_unknown_interaction_error
from .selects import SessionCleanSelect, SocialServersSelect, ToolsSelect, SettingsSelect
from .panel_view import PanelLayoutView


class EphemeralPanelView(PanelLayoutView):
    """Ephemeral panel using LayoutView with 4 categorical selects."""

    def __init__(self, manager, original_interaction: discord.Interaction = None):
        self.original_interaction = original_interaction

        selects = [
            SessionCleanSelect(manager),
            SocialServersSelect(manager),
            ToolsSelect(manager),
            SettingsSelect(manager),
        ]

        # Set original_interaction ref on each select's view after init
        super().__init__(selects, timeout=None)

    async def on_error(self, interaction, error, item):
        if is_unknown_interaction_error(error):
            return
        raise error
