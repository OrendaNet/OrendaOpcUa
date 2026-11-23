import asyncio
import tempfile
import unittest
from .support import InstalledApp, RuntimeFixture


class ManagedExport(unittest.IsolatedAsyncioTestCase):
    async def test_headless_export_and_identity(self):
        edge = RuntimeFixture()
        edge.scrape = "2026-01-01T12:00:00Z"
        await edge.start()
        self.addAsyncCleanup(edge.server.close)
        with tempfile.TemporaryDirectory() as directory:
            app = await InstalledApp(directory, "proxy", edge.base).start()
            try:
                for _ in range(50):
                    if edge.snapshots:
                        break
                    await asyncio.sleep(.1)
                self.assertEqual(edge.snapshots, [[]])
                headers = {"x-orenda-edge-proxy-secret":"proxy", "x-orenda-user-roles":"viewer",
                           "x-orenda-auth-source":"edge", "x-orenda-username":"viewer"}
                state = await (await app.get("/api/state",headers=headers)).json()
                self.assertEqual(state["lastScrape"],edge.scrape)
                self.assertFalse(state["manage"])
            finally:
                await app.close()
