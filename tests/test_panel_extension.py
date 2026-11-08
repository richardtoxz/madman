"""O cog do painel é carregado por nome: a string em bot.py precisa apontar para um módulo real."""

from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class PanelExtensionTests(unittest.TestCase):
    def test_bot_extension_path_imports_the_cog(self):
        source = (ROOT / "panel" / "bot.py").read_text(encoding="utf-8")
        extensions = re.findall(r'load_extension\("([\w.]+)"\)', source)
        self.assertEqual(len(extensions), 1, extensions)
        # Processo só do painel: o discord do painel não pode conviver com o do worker.
        script = (
            "import importlib, panel; "
            f"module = importlib.import_module({extensions[0]!r}); "
            "assert callable(getattr(module, 'setup', None))"
        )
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, encoding="utf-8", cwd=ROOT, timeout=60
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
