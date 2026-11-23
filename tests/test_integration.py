import asyncio
import os
import secrets
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp import web
from aiohttp import UnixConnector
from aiohttp.test_utils import TestClient, TestServer
from aiohttp import ClientSession
from asyncua import ua
from .mock_server import Plant
from opcua_box.web import application


async def eventually(fn, predicate, timeout=12):
    end = time.monotonic() + timeout
    while True:
        value = await fn()
        if predicate(value):
            return value
        if time.monotonic() >= end:
            raise AssertionError(f"Timed out waiting for {predicate.__name__}")
        await asyncio.sleep(.15)


class Workflow(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.password = secrets.token_hex(16)
        self.secret = secrets.token_hex(32)
        self.plant = Plant(self.password)
        await self.plant.start()
        self.addAsyncCleanup(self.plant.stop_server)
        self.headers = {"x-orenda-edge-proxy-secret": self.secret, "x-orenda-user-roles": "admin",
                        "x-orenda-auth-source": "edge", "x-orenda-username": "test"}
        await self.launch()
        self.addAsyncCleanup(lambda: self.client.close())

    async def launch(self):
        self.client = TestClient(TestServer(application(self.tmp.name, self.secret)))
        await self.client.start_server()

    async def state(self):
        return await (await self.client.get("/api/state", headers=self.headers)).json()

    async def post(self, path, data, status=200):
        revision = (await self.state())["revision"]
        response = await self.client.post("/api/" + path, json={"revision": revision, **data}, headers=self.headers)
        result = await response.json()
        self.assertEqual(response.status, status, result)
        return result

    async def test_network_grant(self):
        runtime = web.Application()
        async def context(req):
            self.assertEqual(req.headers["Authorization"], "Bearer " + self.secret)
            return web.json_response({"capabilities": []})
        runtime.router.add_get("/context", context)
        async with TestServer(runtime) as edge:
            env = {"ORENDA_EDGE_API_URL": str(edge.make_url("/")).rstrip("/"), "ORENDA_APP_TOKEN": self.secret}
            with patch.dict(os.environ, env):
                await self.client.close()
                await self.launch()
                denied = await self.post("discover", {"url":self.plant.url}, 400)
                self.assertIn("Permissions", denied["error"])

    async def test_commission_monitor_recover(self):
        self.assertEqual((await self.client.get("/api/state")).status, 401)
        viewer = {**self.headers, "x-orenda-user-roles": "viewer"}
        self.assertEqual((await self.client.post("/api/connect", json={}, headers=viewer)).status, 403)
        self.assertEqual((await self.client.get("/api/token", headers=viewer)).status, 403)
        self.assertEqual((await self.client.get("/metrics")).status, 401)
        discovery = await self.post("discover", {"url": self.plant.url})
        conn = dict(url=self.plant.url, fingerprint=discovery["fingerprint"], username="observer", password=self.password)
        await self.post("connect", {**conn, "fingerprint": "0"*64}, 400)
        await self.post("connect", {**conn, "password": "incorrect"}, 400)
        await self.post("connect", conn)
        await self.post("interval", {"interval": 1})
        tree = await (await self.client.get("/api/browse", headers=self.headers)).json()
        self.assertIn("rows", tree, tree)
        self.assertIn("Robot", [r["name"] for r in tree["rows"]])
        axis = self.plant.axis.nodeid.to_string()
        await self.post("add", {"id": axis, "metric": "axis_a1_degrees"})
        await self.post("add", {"id": self.plant.stop.nodeid.to_string(), "metric": "emergency_stop"})
        await self.post("add", {"id": self.plant.text.nodeid.to_string(), "metric": "text"}, 400)
        await self.post("notifier", {"id": self.plant.robot.nodeid.to_string()})
        await self.plant.alarm()
        state = await eventually(self.state, lambda s: any(e["code"] == "KSS1" for e in s["events"]))
        self.assertTrue(state["events"][0]["retain"])
        self.assertEqual(state["events"][0]["severity"], 800)
        token = (await (await self.client.get("/api/token", headers=self.headers)).json())["token"]
        async def scrape():
            return await (await self.client.get("/metrics", headers={"Authorization":"Bearer "+token})).text()
        await eventually(scrape, lambda s: "orenda_opcua_value_emergency_stop 1" in s)
        self.assertIn("orenda_opcua_value_axis_a1_degrees -5.34", await scrape())
        self.assertNotIn(self.password, str(await self.state()))
        await self.plant.axis.write_value(ua.DataValue(ua.Variant(None), ua.StatusCode(ua.StatusCodes.BadOutOfService)))
        await eventually(scrape, lambda s: 'channel="axis_a1_degrees"} 0' in s)
        self.assertNotIn("orenda_opcua_value_axis_a1_degrees", await scrape())
        await self.plant.stop_server()
        await eventually(scrape, lambda s: "orenda_opcua_connected 0" in s)
        self.assertNotIn("orenda_opcua_value_emergency_stop", await scrape())
        await self.plant.start(shifted=True)
        await eventually(scrape, lambda s: "orenda_opcua_value_axis_a1_degrees -5.34" in s, 30)
        await self.client.close()
        await self.launch()
        await eventually(scrape, lambda s: "orenda_opcua_value_axis_a1_degrees -5.34" in s)
        await self.post("remove", {"metric":"axis_a1_degrees"})
        self.assertNotIn("axis_a1_degrees", await scrape())
        self.assertTrue((await self.state())["externalScrape"])
