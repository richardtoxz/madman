from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Painel e worker usam bibliotecas do Discord diferentes: roda num processo só do painel.
SCRIPT = textwrap.dedent(
    """
    import asyncio
    import json
    from types import SimpleNamespace as NS

    import panel
    from panel.social import handlers as social

    FOOTERS = []
    STATE = {}

    class FakeDM:
        async def edit(self, **kwargs):
            STATE["view"] = kwargs.get("view")
            FOOTERS.append(kwargs["embed"].footer.text)

        async def finish(self, **kwargs):
            raise AssertionError(kwargs)

        async def expire_after(self, seconds):
            pass

    async def fake_open_temporary_dm(interaction, embed):
        return FakeDM()

    social.open_temporary_dm = fake_open_temporary_dm

    async def noop(*args, **kwargs):
        pass

    def interaction():
        return NS(user=NS(id=123), response=NS(defer=noop, send_message=noop))

    class Manager:
        def is_authenticated(self, user_id):
            return True

        async def get_blocked_list(self, user_id, page=0):
            # A lista encolheu entre as consultas: 3 páginas na primeira, 2 na segunda.
            return {"items": [{"id": 30 + page, "name": "Bloqueado"}], "total": 60 - 20 * page,
                    "page": page, "pages": 3 - page, "fresh": True, "warning": None}

    async def main():
        await social.handle_unblock_all(interaction(), Manager(), 123)
        following = next(c for c in STATE["view"].children if str(getattr(c, "emoji", "") or "") == "▶")
        await following.callback(interaction())
        print(json.dumps(FOOTERS))

    asyncio.run(main())
    """
)


class BlockedListFooterTests(unittest.TestCase):
    def test_footer_counts_pages_of_the_current_query(self):
        completed = subprocess.run(
            [sys.executable, "-c", SCRIPT],
            capture_output=True,
            encoding="utf-8",
            cwd=ROOT,
            timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        footers = json.loads(completed.stdout)
        self.assertEqual(
            [footer.split(" • ")[0] for footer in footers],
            ["Pagina 1/3", "Pagina 2/2"],
        )


if __name__ == "__main__":
    unittest.main()
