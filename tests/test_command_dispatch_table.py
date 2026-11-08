"""Tabela de comandos do worker.

`dispatch` procura primeiro o comando exato e depois percorre os prefixos em ordem, e o primeiro
que casa vence. A ordem só importaria se um prefixo fosse prefixo de outro; o teste fixa qual
handler (módulo e nome) atende cada comando e esse invariante.
"""

import unittest

from worker.core.router import register_handlers

EXACT_COMMANDS = {
    "ACCOUNT_PROFILE": "worker.account.commands.handle_account_profile",
    "ACCOUNT_PROFILE_REFRESH": "worker.account.commands.handle_account_profile",
    "ACCOUNT_SESSIONS": "worker.account.commands.handle_account_sessions",
    "ACCOUNT_STATUS": "worker.account.commands.handle_account_status",
    "ACCOUNT_UNREAD": "worker.account.commands.handle_account_unread",
    "CANCEL": "worker.control.commands.handle_cancel",
    "CLOSE": "worker.control.commands.handle_close",
    "DM_CLOSE_ALL": "worker.messages.commands.handle_dm_close_all",
    "DM_LIST": "worker.messages.commands.handle_dm_list",
    "DM_LIST_REFRESH": "worker.messages.commands.handle_dm_list",
    "GROUP_DM_LEAVE_ALL": "worker.messages.commands.handle_group_dm_leave_all",
    "GUILD_STATS": "worker.guilds.commands.handle_guild_stats",
    "MSG_CLEAN_ALL_DMS": "worker.messages.commands.handle_msg_clean_all_dms",
    "MSG_SCAN_GUILDS": "worker.messages.commands.handle_msg_scan_guilds",
    "PAUSE": "worker.control.commands.handle_pause",
    "PRESENCE_MAIN": "worker.presence.commands.handle_presence_main",
    "RICH_PRESENCE_STATUS": "worker.presence.commands.handle_rich_presence_status",
    "PROTECTION_DISABLE": "worker.protection.commands.handle_protection_disable",
    "PROTECTION_ENABLE": "worker.protection.commands.handle_protection_enable",
    "PROTECTION_STATUS": "worker.protection.commands.handle_protection_status",
    "QUEST_AUTO": "worker.quest.commands.handle_quest_auto",
    "QUEST_CANCEL": "worker.quest.commands.handle_quest_cancel",
    "QUEST_LIST": "worker.quest.commands.handle_quest_list",
    "REL_ACCEPT_ALL": "worker.relationships.commands.handle_rel_accept_all",
    "REL_BLOCKED_LIST": "worker.relationships.commands.handle_rel_blocked_list",
    "REL_DENY_ALL": "worker.relationships.commands.handle_rel_deny_all",
    "REL_FRIENDS_LIST": "worker.relationships.commands.handle_rel_friends_list",
    "REL_PENDING_LIST": "worker.relationships.commands.handle_rel_pending_list",
    "REL_STATS": "worker.relationships.commands.handle_rel_stats",
    "REL_UNBLOCK_ALL": "worker.relationships.commands.handle_rel_unblock_all",
    "RESUME": "worker.control.commands.handle_resume",
    "START": "worker.control.commands.handle_start",
    "TRACKER_STATUS": "worker.tracker.commands.handle_tracker_status",
    "TRACKER_STOP": "worker.tracker.commands.handle_tracker_stop",
    "VOICE_LEAVE": "worker.voice.commands.handle_voice_leave",
    "VOICE_STATUS": "worker.voice.commands.handle_voice_status",
    "VOICE_UNFOLLOW": "worker.voice.commands.handle_voice_unfollow",
}

PREFIX_COMMANDS = {
    "ACCOUNT_ACK_ALL_JSON:": "worker.account.commands.handle_account_ack_all",
    "ACCOUNT_MENTIONS_JSON:": "worker.account.commands.handle_account_mentions",
    "ACCOUNT_MENTION_DELETE_JSON:": "worker.account.commands.handle_account_mention_delete",
    "ACCOUNT_STATUS_SET_JSON:": "worker.account.commands.handle_account_status_set",
    "DM_CLOSE:": "worker.messages.commands.handle_dm_close",
    "GROUP_DM_LEAVE:": "worker.messages.commands.handle_group_dm_leave",
    "GUILD_LEAVE_ALL_JSON:": "worker.guilds.commands.handle_guild_leave_all",
    "GUILD_LIST_PAGE_JSON:": "worker.guilds.commands.handle_guild_list",
    "GUILD_MUTE_ALL_JSON:": "worker.guilds.commands.handle_guild_mute_all",
    "MSG_CLEAN_BATCH_JSON:": "worker.messages.commands.handle_msg_clean_batch",
    "MSG_CLEAN_CHANNEL:": "worker.messages.commands.handle_msg_clean_channel",
    "MSG_CLEAN_CHANNEL_NOW:": "worker.messages.commands.handle_msg_clean_channel_now",
    "MSG_CLEAN_GUILD:": "worker.messages.commands.handle_msg_clean_guild",
    "MSG_CLEAN_GUILD_NOW:": "worker.messages.commands.handle_msg_clean_guild_now",
    "MSG_COUNT:": "worker.messages.commands.handle_msg_count",
    "MSG_PREVIEW_JSON:": "worker.messages.commands.handle_msg_preview",
    "PRESENCE_TASK:": "worker.presence.commands.handle_presence_task",
    "PRESENCE_UPDATE:": "worker.presence.commands.handle_presence_update",
    "RICH_PRESENCE_SET_JSON:": "worker.presence.commands.handle_rich_presence_set",
    "PROTECTION_CONFIG_JSON:": "worker.protection.commands.handle_protection_configure",
    "PROTECTION_ENABLE_JSON:": "worker.protection.commands.handle_protection_enable",
    "QUEST_CONFIG:": "worker.quest.commands.handle_quest_config",
    "QUEST_START:": "worker.quest.commands.handle_quest_start",
    "REL_ADD_FRIEND_JSON:": "worker.relationships.commands.handle_rel_add_friend",
    "REL_BLOCKED_PAGE_JSON:": "worker.relationships.commands.handle_rel_blocked_list",
    "REL_BLOCK_JSON:": "worker.relationships.commands.handle_rel_block",
    "REL_CLOSEST_JSON:": "worker.relationships.commands.handle_rel_closest",
    "REL_FRIENDS_PAGE_JSON:": "worker.relationships.commands.handle_rel_friends_list",
    "REL_NICKNAME_JSON:": "worker.relationships.commands.handle_rel_nickname",
    "REL_PENDING_PAGE_JSON:": "worker.relationships.commands.handle_rel_pending_list",
    "REL_REMOVE_ALL:": "worker.relationships.commands.handle_rel_remove_all",
    "REL_UNBLOCK_JSON:": "worker.relationships.commands.handle_rel_unblock",
    "TARGET:": "worker.messages.commands.handle_target",
    "TRACKER_START_JSON:": "worker.tracker.commands.handle_tracker_start",
    "VOICE_FOLLOW_JSON:": "worker.voice.commands.handle_voice_follow",
    "VOICE_JOIN_JSON:": "worker.voice.commands.handle_voice_join",
}


def _names(table):
    return {command: f"{handler.__module__}.{handler.__qualname__}" for command, handler in table.items()}


class CommandDispatchTableTests(unittest.TestCase):
    def test_register_handlers_keeps_the_same_commands(self):
        exact, prefix = register_handlers(None)
        self.assertEqual(_names(exact), EXACT_COMMANDS)
        self.assertEqual(_names(prefix), PREFIX_COMMANDS)

    def test_no_prefix_shadows_another(self):
        _, prefix = register_handlers(None)
        shadowed = [(a, b) for a in prefix for b in prefix if a != b and b.startswith(a)]
        self.assertEqual(shadowed, [])


if __name__ == "__main__":
    unittest.main()
