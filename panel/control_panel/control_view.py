from .selects import SessionCleanSelect, SocialServersSelect, ToolsSelect, SettingsSelect
from .panel_view import PanelLayoutView


class PersistentControlView(PanelLayoutView):
    """Persistent panel for the static channel — uses custom_ids for reconnection."""

    def __init__(self, manager):
        self.manager = manager

        selects = [
            SessionCleanSelect(manager, custom_id="madman_session_select"),
            SocialServersSelect(manager, custom_id="madman_social_select"),
            ToolsSelect(manager, custom_id="madman_tools_select"),
            SettingsSelect(manager, custom_id="madman_settings_select"),
        ]

        super().__init__(selects, timeout=None)
