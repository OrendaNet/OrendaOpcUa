import asyncio
import tempfile
import unittest
from unittest.mock import patch
import os
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from opcua_box.web import application


class ManagedExport(unittest.IsolatedAsyncioTestCase):
    async def test_install_exports_without_a_browser_or_host_command(self):
        received = asyncio.Event()
        async def publish(req):
            self.assertEqual(req.headers["Authorization"], "Bearer fixture")
            self.assertEqual(await req.json(), {"channels":[]})
            received.set()
            return web.json_response({"accepted":0,"lastScrape":"2026-01-01T12:00:00Z"})
        edge = web.Application()
        edge.router.add_post("/prometheus/channels", publish)
        async with TestServer(edge) as runtime:
            with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                "ORENDA_EDGE_API_URL":str(runtime.make_url("/")).rstrip("/"), "ORENDA_APP_TOKEN":"fixture"}):
                async with TestClient(TestServer(application(directory, "proxy"))) as client:
                    await asyncio.wait_for(received.wait(), 10)
                    headers = {"x-orenda-edge-proxy-secret":"proxy","x-orenda-auth-source":"edge","x-orenda-username":"viewer"}
                    state = await (await client.get("/api/state",headers=headers)).json()
                    self.assertEqual(state["lastScrape"],"2026-01-01T12:00:00Z")
                    self.assertFalse(state["manage"])
