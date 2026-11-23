"""Start the production Node entry point and native collector against an isolated Edge fixture."""
import asyncio
import os
from pathlib import Path
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

ROOT = Path(__file__).resolve().parents[1]


class InstalledApp:
    def __init__(self, directory, secret, runtime, **extra):
        self.env = {**os.environ, "ORENDA_APP_DATA_DIR": str(directory), "HOST": "127.0.0.1", "PORT": "0",
                    "ORENDA_EDGE_APP_PROXY_SECRET": secret, "ORENDA_EDGE_API_URL": runtime,
                    "ORENDA_APP_TOKEN": "test-installation", **extra}
        self.process = None
        self.session = None

    async def start(self):
        self.process = await asyncio.create_subprocess_exec("node", "server.js", cwd=ROOT, env=self.env,
                                                          stdout=asyncio.subprocess.PIPE)
        line = (await asyncio.wait_for(self.process.stdout.readline(), 60)).decode().strip()
        if not line.startswith("Listening on port "):
            raise RuntimeError("Production entry point failed to start")
        self.base = "http://127.0.0.1:" + line.split()[-1]
        self.session = ClientSession(base_url=self.base)
        return self

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None
        if self.process and self.process.returncode is None:
            self.process.terminate()
            await self.process.wait()

    def get(self, *args, **kwargs):
        return self.session.get(*args, **kwargs)

    def post(self, *args, **kwargs):
        return self.session.post(*args, **kwargs)


class RuntimeFixture:
    def __init__(self):
        self.capabilities = ["network:outbound", "prometheus:publish"]
        self.snapshots = []
        self.scrape = None
        app = web.Application()
        app.router.add_get("/context", self.context)
        app.router.add_post("/prometheus/channels", self.publish)
        self.server = TestServer(app)

    async def context(self, req):
        if req.headers.get("Authorization") != "Bearer test-installation":
            raise web.HTTPUnauthorized()
        return web.json_response({"capabilities": self.capabilities})

    async def publish(self, req):
        if req.headers.get("Authorization") != "Bearer test-installation":
            raise web.HTTPUnauthorized()
        if "prometheus:publish" not in self.capabilities:
            raise web.HTTPForbidden()
        values = (await req.json())["channels"]
        self.snapshots.append(values)
        return web.json_response({"accepted": len(values), "lastScrape": self.scrape})

    async def start(self):
        await self.server.start_server()
        self.base = str(self.server.make_url("/")).rstrip("/")
