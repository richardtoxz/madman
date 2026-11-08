import subprocess
import sys
import textwrap
import unittest


class TrackerPanelTests(unittest.TestCase):
    def test_multi_target_modal_and_status_use_panel_library(self):
        code = textwrap.dedent("""
            import asyncio
            from panel.tracker.modal import TrackerModal, TrackerControlView, build_tracker_embed

            async def main():
                ids = list(range(123456789012345678, 123456789012345688))
                status = {"ok": True, "enabled": True, "target_ids": ids,
                    "targets": [{"target_id": item, "history_status": "running"} for item in ids]}
                modal = TrackerModal(None, 123, "webhook", ids)
                assert modal.target_id.default.splitlines() == list(map(str, ids))
                view = TrackerControlView(None, 123, status, "webhook")
                assert len(view.children) == 3
                embed = build_tracker_embed(status)
                assert len(embed) <= 6000
                assert all(len(field.value) <= 1024 for field in embed.fields)
                assert all(str(item) in str(embed.to_dict()) for item in ids)
                view.stop()
                modal.stop()

            asyncio.run(main())
        """)
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
