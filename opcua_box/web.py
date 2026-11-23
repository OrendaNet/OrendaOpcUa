import asyncio
import contextlib
import hmac
import os
import socket
from pathlib import Path

from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import serialization

from .monitor import Monitor, utc
from .protocol import discover, message
from .store import Store

PUBLIC = Path(__file__).resolve().parent.parent / "public"
MANAGE = web.RequestKey("manage", bool)


def eq(a, b):
    return bool(a and b and hmac.compare_digest(a.encode(), b.encode()))


def application(directory, secret):
    store = Store(directory)
    monitor = Monitor(store)

    @web.middleware
    async def authorize(req, handler):
        if req.path in {"/health", "/metrics"}:
            return await handler(req)
        if not (eq(secret, req.headers.get("x-orenda-edge-proxy-secret", "")) and
                req.headers.get("x-orenda-auth-source") and req.headers.get("x-orenda-username")):
            raise web.HTTPUnauthorized(text="Open the app from Edge Console.")
        roles = {r.strip() for r in req.headers.get("x-orenda-user-roles", "").split(",")}
        req[MANAGE] = bool(roles.intersection({"admin", "owner"}))
        if (req.method != "GET" or req.path in {"/api/token", "/api/browse", "/api/preview"}) and not req[MANAGE]:
            raise web.HTTPForbidden(text="An Edge administrator or owner is required.")
        try:
            result = await handler(req)
        except web.HTTPException:
            raise
        except Exception as exc:
            return web.json_response({"error": message(exc)}, status=400, headers={"Cache-Control": "no-store"})
        result.headers["Cache-Control"] = "no-store"
        result.headers["X-Content-Type-Options"] = "nosniff"
        return result

    app = web.Application(middlewares=[authorize], client_max_size=16 * 1024)

    async def health(req):
        return web.json_response({"ok": True})

    async def state(req):
        c = store.data
        conn = c["connection"]
        return web.json_response(dict(manage=req[MANAGE], revision=c["revision"], interval=c["interval"],
                                      connection={k: conn[k] for k in ("url", "username", "fingerprint")} if conn else None,
                                      online=monitor.online(), error=monitor.error, channels=c["channels"],
                                      samples=monitor.samples if monitor.online() else {}, lastRead=monitor.last_read,
                                      lastScrape=monitor.last_scrape, events=list(monitor.events),
                                      eventState=monitor.event_state, notifier=c["notifier"]))

    async def api(req):
        action = req.match_info["action"]
        if req.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType()
        data = await req.json()
        if not isinstance(data, dict):
            raise ValueError("Expected a configuration object.")
        async with monitor.lock:
            if action == "discover":
                await monitor.network()
                _, _, _, info = await discover(data.get("url"))
                return web.json_response(info)
            await monitor.update(action, data)
        return web.json_response({"ok": True})

    async def read(req):
        async with monitor.lock:
            node = req.query.get("id", "i=85")
            if req.path == "/api/browse":
                offset = int(req.query.get("offset", "0"))
                if not 0 <= offset <= 65536:
                    raise ValueError("Invalid browse page.")
                result = await monitor.browse(node, offset)
            else:
                result = await monitor.preview(node)
        return web.json_response(result)

    async def certificate(req):
        der = x509.load_pem_x509_certificate(store.data["cert"].encode()).public_bytes(serialization.Encoding.DER)
        return web.Response(body=der, headers={"Content-Disposition": 'attachment; filename="orenda-opcua.der"'},
                            content_type="application/pkix-cert")

    async def token(req):
        return web.json_response({"token": store.data["token"]})

    async def setup_helper(req):
        return web.FileResponse(PUBLIC.parent / "tools" / "box_scrape.py", headers={
            "Content-Disposition": 'attachment; filename="box_scrape.py"', "Content-Type": "application/octet-stream"})

    async def metrics(req):
        if req.transport.get_extra_info("socket").family != getattr(socket, "AF_UNIX", None):
            if not eq("Bearer " + store.data["token"], req.headers.get("Authorization", "")):
                raise web.HTTPUnauthorized()
        monitor.last_scrape = utc()
        return web.Response(text=monitor.metrics(), headers={"Content-Type": "text/plain; version=0.0.4; charset=utf-8",
                                                            "Cache-Control": "no-store"})

    async def output(req):
        return web.Response(text=monitor.metrics(), content_type="text/plain")

    async def static(req):
        name = req.match_info.get("name", "index.html")
        if name not in {"index.html", "style.css", "app.js"}:
            raise web.HTTPNotFound()
        return web.FileResponse(PUBLIC / name)

    async def lifecycle(app):
        runner = None
        if os.name == "posix":
            path = store.directory / "scrape"
            path.mkdir(mode=0o750, exist_ok=True)
            target = path / "metrics.sock"
            target.unlink(missing_ok=True)
            scrape_app = web.Application()
            scrape_app.router.add_get("/metrics", metrics)
            runner = web.AppRunner(scrape_app, access_log=None)
            await runner.setup()
            await web.UnixSite(runner, str(target)).start()
            target.chmod(0o660)
        task = asyncio.create_task(monitor.run())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            await monitor.close()
            if runner:
                await runner.cleanup()

    app.cleanup_ctx.append(lifecycle)
    for path, handler in [("/health", health), ("/metrics", metrics), ("/api/state", state),
                          ("/api/preview", read), ("/api/browse", read), ("/api/certificate", certificate),
                          ("/api/box-setup", setup_helper),
                          ("/api/token", token), ("/api/output", output), ("/", static), ("/{name}", static)]:
        app.router.add_get(path, handler)
    app.router.add_post("/api/{action}", api)
    return app


if __name__ == "__main__":
    web.run_app(application(os.getenv("ORENDA_APP_DATA_DIR", "/data"), os.environ["ORENDA_EDGE_APP_PROXY_SECRET"]),
                host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "3101")), access_log=None)
