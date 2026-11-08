"""Executa funções do JS do painel no Node (pula quando o Node não está instalado)."""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

HTML = Path(__file__).resolve().parents[1] / "panel" / "web" / "dashboard.html"
NODE = shutil.which("node")


def _function_source(source: str, start_marker: str) -> str:
    start = source.index(start_marker)
    return source[start : source.index("\n    }\n", start) + len("\n    }\n")]


def _run(script: str) -> dict:
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout.strip().splitlines()[-1])


@unittest.skipUnless(NODE, "Node não instalado")
class ApiDedupTests(unittest.TestCase):
    def setUp(self):
        source = HTML.read_text(encoding="utf-8")
        self.api = source[source.index("    const inflightRequests = new Map();") :]
        self.api = self.api[: self.api.index("\n    }\n", self.api.index("async function sendRequest(")) + 7]

    def _scenario(self, body: str) -> dict:
        return _run(
            "let csrfToken = 'tok'; const calls = [];\n"
            "let failNext = false;\n"
            "global.fetch = async (path, opts) => {\n"
            "  calls.push(`${opts.method || 'GET'} ${path} ${opts.body || ''}`);\n"
            "  await new Promise((r) => setTimeout(r, 30));\n"
            "  if (failNext) { failNext = false; throw new Error('rede'); }\n"
            "  return { status: 200, json: async () => ({ ok: true, path }) };\n"
            "};\n"
            f"{self.api}\n"
            f"(async () => {{ {body} }})();"
        )

    def test_identical_concurrent_get_is_sent_once(self):
        out = self._scenario(
            "const [a, b] = await Promise.all([api('/api/guilds'), api('/api/guilds')]);"
            "console.log(JSON.stringify({ calls, same: a.data.ok && b.data.ok }));"
        )
        self.assertEqual(out["calls"], ["GET /api/guilds "])
        self.assertTrue(out["same"])

    def test_double_click_on_a_mutation_is_sent_once(self):
        out = self._scenario(
            "const body = () => ({ action: 'accept_all' });"
            "await Promise.all([api('/api/social/action', { method: 'POST', body: body() }),"
            " api('/api/social/action', { method: 'POST', body: body() })]);"
            "console.log(JSON.stringify({ calls }));"
        )
        self.assertEqual(len(out["calls"]), 1)
        self.assertIn('{"action":"accept_all"}', out["calls"][0])

    def test_different_requests_are_not_merged(self):
        out = self._scenario(
            "await Promise.all([api('/api/social/overview?tab=friends'), api('/api/social/overview?tab=pending'),"
            " api('/api/protection/whitelist', { method: 'POST', body: { target_id: '1' } }),"
            " api('/api/protection/whitelist', { method: 'POST', body: { target_id: '2' } })]);"
            "console.log(JSON.stringify({ calls }));"
        )
        self.assertEqual(len(out["calls"]), 4)

    def test_a_finished_request_can_be_sent_again(self):
        out = self._scenario(
            "await api('/api/guilds'); await api('/api/guilds');console.log(JSON.stringify({ calls }));"
        )
        self.assertEqual(len(out["calls"]), 2)

    def test_network_failure_resolves_for_everyone_and_is_not_kept(self):
        out = self._scenario(
            "failNext = true;"
            "const [a, b] = await Promise.all([api('/api/quests'), api('/api/quests')]);"
            "const again = await api('/api/quests');"
            "console.log(JSON.stringify({ calls, a: a.data.ok, b: b.data.ok, again: again.data.ok }));"
        )
        self.assertEqual(len(out["calls"]), 2)
        self.assertFalse(out["a"])
        self.assertFalse(out["b"])
        self.assertTrue(out["again"])

    def test_csrf_header_only_on_mutations(self):
        out = _run(
            "let csrfToken = 'tok'; const seen = [];\n"
            "global.fetch = async (path, opts) => { seen.push([opts.method || 'GET', opts.headers['X-CSRF-Token'] || null]);"
            " return { status: 200, json: async () => ({ ok: true }) }; };\n"
            f"{self.api}\n"
            "(async () => { await api('/a'); await api('/b', { method: 'POST', body: {} });"
            " console.log(JSON.stringify({ seen })); })();"
        )
        self.assertEqual(out["seen"], [["GET", None], ["POST", "tok"]])


@unittest.skipUnless(NODE, "Node não instalado")
class DebounceTests(unittest.TestCase):
    def test_rapid_calls_run_once_with_the_last_arguments(self):
        source = HTML.read_text(encoding="utf-8")
        debounce = _function_source(source, "    function debounce(")
        out = _run(
            f"{debounce}\n"
            "const runs = []; const d = debounce((v) => runs.push(v), 40);\n"
            "d('a'); d('ab'); d('abc');\n"
            "setTimeout(() => { d('x'); setTimeout(() => console.log(JSON.stringify({ runs })), 80); }, 80);"
        )
        self.assertEqual(out["runs"], ["abc", "x"])

    def test_list_searches_are_debounced(self):
        source = HTML.read_text(encoding="utf-8")
        for input_id, handler in (
            ("dm-search-input", "renderOpenDmsSoon()"),
            ("friends-search-input", "renderFriendsListSoon()"),
            ("guilds-search-input", "renderGuildsListSoon()"),
        ):
            start = source.index(f'id="{input_id}"')
            tag = source[start : source.index(">", start)]
            self.assertIn(f'oninput="{handler}"', tag, input_id)


if __name__ == "__main__":
    unittest.main()
