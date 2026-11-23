"""Optional scrape-only bridge in the Box Prometheus network namespace."""
from aiohttp import ClientSession, ClientTimeout, UnixConnector, web


async def resources(app):
    async with ClientSession(connector=UnixConnector(path="/scrape/metrics.sock"), timeout=ClientTimeout(total=4)) as client:
        app[CLIENT] = client
        yield


async def metrics(req):
    try:
        async with req.app[CLIENT].get("http://localhost/metrics") as upstream:
            return web.Response(status=upstream.status, body=await upstream.read(),
                                headers={"Content-Type": upstream.headers["Content-Type"]})
    except (OSError, TimeoutError):
        raise web.HTTPServiceUnavailable(text="Exporter socket unavailable")


CLIENT = web.AppKey("client", ClientSession)


def application():
    app = web.Application()
    app.cleanup_ctx.append(resources)
    app.router.add_get("/metrics", metrics)
    return app


if __name__ == "__main__":
    web.run_app(application(), host="127.0.0.1", port=9468, access_log=None)
