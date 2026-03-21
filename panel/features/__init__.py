from .session import handle_authenticate, handle_disconnect
from .cleanup import handle_clean_dm, handle_clean_all_dms, handle_clean_guild, handle_close_dms
from .social import (
    handle_pending_requests,
    handle_clean_friends,
    handle_unblock_all,
    handle_mute_servers,
    handle_leave_servers,
)
from .voice import handle_voice_afk, handle_voice_follow
from .protection import handle_protection
from .tracker import handle_tracker
from .quest import handle_quest
from .settings import handle_settings

HANDLER_MAP = {
    "auth": handle_authenticate,
    "disconnect": handle_disconnect,
    "clean_dm": handle_clean_dm,
    "clean_all_dms": handle_clean_all_dms,
    "clean_guild": handle_clean_guild,
    "close_dms": handle_close_dms,
    "mute_servers": handle_mute_servers,
    "leave_servers": handle_leave_servers,
    "pending": handle_pending_requests,
    "clean_friends": handle_clean_friends,
    "unblock": handle_unblock_all,
    "voice_afk": handle_voice_afk,
    "voice_follow": handle_voice_follow,
    "protection": handle_protection,
    "tracker": handle_tracker,
    "quest": handle_quest,
    "settings": handle_settings,
}

__all__ = ["HANDLER_MAP"]
