"""Local-only commissioning fixture. Run from the repository root."""
import argparse
import asyncio
import contextlib
import math
import secrets
import tempfile
from pathlib import Path

from aiohttp import web
from tests.support import InstalledApp, RuntimeFixture
from tests.mock_server import Plant


async def main(port, fixture_port, directory=None):
    secret = secrets.token_urlsafe(32)
    plant = Plant(secrets.token_urlsafe(24), fixture_port)
    await plant.start()

    with (contextlib.nullcontext(directory) if directory else tempfile.TemporaryDirectory()) as directory:
        edge = RuntimeFixture()
        await edge.start()
        installed = await InstalledApp(Path(directory), secret, edge.base).start()
        async def forward(req):
            if req.host.split(":")[0] != "127.0.0.1" or req.headers.get("Origin") not in (None, f"http://127.0.0.1:{port}"):
                raise web.HTTPForbidden()
            headers = {"x-orenda-edge-proxy-secret":secret, "x-orenda-username":"developer",
                       "x-orenda-auth-source":"local-fixture", "x-orenda-user-roles":"admin"}
            for header in ("Content-Type","Authorization"):
                if header in req.headers:
                    headers[header] = req.headers[header]
            async with installed.session.request(req.method, req.path_qs, data=await req.read(), headers=headers) as res:
                return web.Response(body=await res.read(), status=res.status, headers={"Content-Type":res.headers.get("Content-Type","text/plain")})
        app = web.Application()
        app.router.add_route("*","/{tail:.*}",forward)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", port).start()
        print(f"Simulated controller {plant.url}\nOpen http://127.0.0.1:{port}/ and choose Anonymous.", flush=True)
        step = 0
        try:
            while True:
                await asyncio.sleep(1)
                step += 1
                await plant.axis.write_value(round(-5 + math.sin(step/8), 3))
                if step % 12 == 0:
                    await plant.alarm()
                if step % 12 == 6:
                    await plant.stop.write_value(False)
        finally:
            await runner.cleanup()
            await installed.close()
            await edge.server.close()
            await plant.stop_server()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=3100)
    parser.add_argument("--fixture-port", type=int, default=4841)
    parser.add_argument("--data", help="Persistent fixture data for isolated container qualification")
    args = parser.parse_args()
    asyncio.run(main(args.port, args.fixture_port, args.data))
