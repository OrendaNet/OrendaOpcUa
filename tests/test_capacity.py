"""Exercise the supported channel limit against a separate secure OPC UA server."""
import asyncio
import tempfile
import time
import unittest
from asyncua import ua
from .mock_server import Plant
from .support import InstalledApp, RuntimeFixture


class ChannelCapacity(unittest.IsolatedAsyncioTestCase):
    async def test_128_channel_snapshots(self):
        edge = RuntimeFixture()
        await edge.start()
        self.addAsyncCleanup(edge.server.close)
        plant = Plant("capacity-fixture")
        await plant.start()
        self.addAsyncCleanup(plant.stop_server)
        namespace = plant.robot.nodeid.NamespaceIndex
        nodes = []
        for i in range(128):
            name = f"Meter{i:03}"
            variable = await plant.robot.add_variable(ua.NodeId(name, namespace), ua.QualifiedName(name, namespace), float(i))
            nodes.append({"id":variable.nodeid.to_string(), "path":name})
        with tempfile.TemporaryDirectory() as directory:
            app = await InstalledApp(directory, "capacity-proxy", edge.base).start()
            headers = {"x-orenda-edge-proxy-secret":"capacity-proxy", "x-orenda-user-roles":"admin",
                       "x-orenda-username":"fixture", "x-orenda-auth-source":"test"}
            async def state():
                return await (await app.get("/api/state",headers=headers)).json()
            async def change(action, **fields):
                res = await app.post("/api/"+action, json={"revision":(await state())["revision"], **fields}, headers=headers)
                result = await res.json()
                self.assertEqual(res.status,200,result)
                return result
            try:
                found = await change("discover", url=plant.url)
                await change("connect",url=plant.url,fingerprint=found["fingerprint"])
                await change("interval",interval=1)
                generation = (await state())["generation"]
                for start in range(0,128,32):
                    await change("addMany",nodes=nodes[start:start+32],generation=generation)
                deadline = time.monotonic()+45
                while time.monotonic()<deadline:
                    if any(len(snapshot)==128 and all(item["value"] is not None for item in snapshot) for snapshot in edge.snapshots):
                        break
                    await asyncio.sleep(.1)
                else:
                    self.fail("Missing complete 128-value SDK snapshot")
                result = await state()
                self.assertEqual(len(result["samples"]),128)
                self.assertTrue(all(v["quality"]=="Good" for v in result["samples"].values()))
                self.assertEqual(sum(item["value"] for item in result["samples"].values()),sum(range(128)))
            finally:
                await app.close()
