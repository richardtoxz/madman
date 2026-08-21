import discord


from .config import ControlState, MessageConfig
from .message_cleanup import MessageCleanupMixin
from .message_helpers import MessageDeleteMixin, MessageLookupMixin, MessageStateMixin
from .message_operations import MessageSessionMixin, MessageUtilityMixin


class MessageManager(
    MessageStateMixin,
    MessageDeleteMixin,
    MessageLookupMixin,
    MessageCleanupMixin,
    MessageSessionMixin,
    MessageUtilityMixin,
):
    def __init__(self, client: discord.Client, control_state: ControlState):
        self.client = client
        self.control_state = control_state
        self.cancelled = False
        self.paused = False
        self._current_task = None
        self._unavailable_sources: set[tuple[str, int, str]] = set()
        self.config = MessageConfig()
