import unittest

from panel.main import create_web_app

# API HTTP pública do painel: (método, caminho, handler). Rota nova, removida ou trocada de
# handler de propósito exige atualizar esta lista junto.
EXPECTED_ROUTES = {
    ("GET", "/", "handle_index"),
    ("GET", "/acompanhar", "handle_index"),
    ("GET", "/amigos", "handle_index"),
    ("GET", "/api/account/mentions", "handle_account_mentions"),
    ("GET", "/api/account/profile", "handle_account_profile"),
    ("GET", "/api/account/sessions", "handle_account_sessions"),
    ("GET", "/api/account/status", "handle_account_status"),
    ("GET", "/api/account/unread", "handle_account_unread"),
    ("GET", "/api/auth/discord/callback", "handle_discord_callback"),
    ("GET", "/api/auth/discord/login", "handle_discord_login"),
    ("GET", "/api/auth/session", "handle_auth_session"),
    ("GET", "/api/cleanup/dms", "handle_cleanup_dms"),
    ("GET", "/api/guilds", "handle_guilds_list"),
    ("GET", "/api/health", "handle_health"),
    ("GET", "/api/presence/rich", "handle_rich_presence_status"),
    ("GET", "/api/protection/status", "handle_protection_status"),
    ("GET", "/api/quests", "handle_quests_list"),
    ("GET", "/api/social/overview", "handle_social_overview"),
    ("GET", "/api/tracker/status", "handle_tracker_status"),
    ("GET", "/api/voice/status", "handle_voice_status"),
    ("GET", "/api/worker/status", "handle_worker_status"),
    ("GET", "/api/ws", "handle_ws"),
    ("GET", "/dashboard", "handle_index"),
    ("GET", "/historico", "handle_index"),
    ("GET", "/inicio", "handle_index"),
    ("GET", "/limpeza", "handle_index"),
    ("GET", "/missoes", "handle_index"),
    ("GET", "/privacidade", "handle_index"),
    ("GET", "/servidores", "handle_index"),
    ("POST", "/api/account/ack-all", "handle_account_ack_all"),
    ("POST", "/api/account/mentions/delete", "handle_account_mentions_delete"),
    ("POST", "/api/account/status", "handle_account_status_save"),
    ("POST", "/api/auth/logout", "handle_logout"),
    ("POST", "/api/cleanup/control", "handle_cleanup_control"),
    ("POST", "/api/cleanup/dm-action", "handle_cleanup_dm_action"),
    ("POST", "/api/cleanup/execute", "handle_cleanup_execute"),
    ("POST", "/api/cleanup/preview", "handle_cleanup_preview"),
    ("POST", "/api/cleanup/scan-guilds", "handle_cleanup_scan_guilds"),
    ("POST", "/api/guilds/bulk", "handle_guilds_bulk"),
    ("POST", "/api/guilds/whitelist", "handle_guilds_whitelist"),
    ("POST", "/api/presence/rich", "handle_rich_presence_set"),
    ("POST", "/api/protection/toggle", "handle_protection_toggle"),
    ("POST", "/api/protection/whitelist", "handle_protection_whitelist"),
    ("POST", "/api/quests/action", "handle_quests_action"),
    ("POST", "/api/settings", "handle_settings_save"),
    ("POST", "/api/social/action", "handle_social_action"),
    ("POST", "/api/social/protect-closest", "handle_social_protect_closest"),
    ("POST", "/api/tracker/start", "handle_tracker_start"),
    ("POST", "/api/tracker/stop", "handle_tracker_stop"),
    ("POST", "/api/voice/action", "handle_voice_action"),
    ("POST", "/api/worker/connect", "handle_worker_connect"),
    ("POST", "/api/worker/disconnect", "handle_worker_disconnect"),
}
# add_get registra também HEAD, com o mesmo handler, para cada GET.
EXPECTED_ROUTES |= {("HEAD", path, handler) for method, path, handler in EXPECTED_ROUTES if method == "GET"}


class WebRouteTableTests(unittest.TestCase):
    def test_registered_routes_match_the_public_http_api(self):
        app = create_web_app(manager=None, bot=None)
        routes = {(route.method, route.resource.canonical, route.handler.__name__) for route in app.router.routes()}
        self.assertEqual(routes, EXPECTED_ROUTES)


if __name__ == "__main__":
    unittest.main()
