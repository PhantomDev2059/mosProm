"""Локальный туннель к сервису на стенде: http://127.0.0.1:8765 → http://10.0.114.30/user/<логин>/proxy/8765/.

Сам логинится в JupyterHub и подставляет куки и XSRF, поэтому в браузере входить в Jupyter не нужно.
Нужен поднятый VPN.  .venv\\Scripts\\python tools\\stand_tunnel.py
"""
import asyncio
import os
import sys
from pathlib import Path

import aiohttp
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parent))
from linq_remote import Hub, load_env  # noqa: E402

load_env()
PORT = int(os.environ.get("TUNNEL_PORT", "8765"))
UP = os.environ["LINQ_HUB"].rstrip("/")
HOP = {"host", "cookie", "connection", "keep-alive", "transfer-encoding", "upgrade", "content-length",
       "proxy-connection", "te", "trailer", "origin", "referer", "accept-encoding"}
state = {}


def login():
    hub = Hub(UP, os.environ["LINQ_USER"], os.environ["LINQ_PASS"])
    user_path = f"/user/{hub.user}/"
    jar = {c.name: c.value for c in hub.s.cookies if c.path in ("/", "/hub/", user_path.rstrip("/"), user_path)
           and not (c.name == "_xsrf" and c.path.startswith("/hub"))}
    xsrf = hub.s.cookies.get("_xsrf", path=user_path) or ""
    state.update(base=f"/user/{hub.user}/proxy/8765", cookie="; ".join(f"{k}={v}" for k, v in jar.items()), xsrf=xsrf)
    print("вход в Jupyter ок,", state["base"], flush=True)


def upstream_path(req):
    path = req.rel_url.raw_path_qs
    return path if path.startswith("/user/") else state["base"] + path


def headers_for(req):
    h = {k: v for k, v in req.headers.items() if k.lower() not in HOP}
    h.update({"Cookie": state["cookie"], "X-XSRFToken": state["xsrf"], "Origin": UP, "Referer": UP + state["base"] + "/"})
    return h


async def handle(req):
    path = upstream_path(req)
    if req.headers.get("upgrade", "").lower() == "websocket":
        ws_client = web.WebSocketResponse(max_msg_size=0)
        await ws_client.prepare(req)
        async with state["session"].ws_connect(UP.replace("http", "ws", 1) + path, headers=headers_for(req),
                                               max_msg_size=0) as ws_up:
            async def pump(src, dst):
                async for msg in src:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await dst.send_str(msg.data)
                    elif msg.type == aiohttp.WSMsgType.BINARY:
                        await dst.send_bytes(msg.data)
                    else:
                        break
            await asyncio.wait([asyncio.ensure_future(pump(ws_client, ws_up)), asyncio.ensure_future(pump(ws_up, ws_client))],
                               return_when=asyncio.FIRST_COMPLETED)
        return ws_client
    body = await req.read()
    async with state["session"].request(req.method, UP + path, headers=headers_for(req), data=body or None,
                                        allow_redirects=False) as up:
        if up.status in (302, 403) and "/hub/login" in up.headers.get("location", "") + str(up.status):
            await asyncio.get_running_loop().run_in_executor(None, login)   # сессия Jupyter истекла — перелогин
        resp = web.StreamResponse(status=up.status,
                                  headers={k: v for k, v in up.headers.items() if k.lower() not in HOP | {"content-encoding"}})
        await resp.prepare(req)
        async for chunk in up.content.iter_any():
            await resp.write(chunk)
        await resp.write_eof()
        return resp


async def main():
    login()
    state["session"] = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_read=None),
                                             auto_decompress=True)
    app = web.Application(client_max_size=1 << 30)
    app.router.add_route("*", "/{tail:.*}", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", PORT).start()
    print(f"туннель: http://127.0.0.1:{PORT}", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
