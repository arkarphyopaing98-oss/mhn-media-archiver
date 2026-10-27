"""One-time Telegram login through the browser (for hosts where you can't run scripts/make_session.py).

Runs only when TG_SESSION is empty. Open  https://<service>.onrender.com/setup?key=<SETUP_KEY>
1) press "Send code"  2) type the code Telegram sends you (+ 2-step password if you have one)
3) copy the session string shown once into the TG_SESSION env var on Render, then redeploy.
"""
from __future__ import annotations

import html
import logging
import os

from aiohttp import web
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

log = logging.getLogger(__name__)
PAGE = """<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<title>MHN archiver setup</title><body style="font-family:sans-serif;max-width:520px;margin:30px auto;padding:0 16px">
<h2>MHN archiver – Telegram login</h2>{body}</body>"""


def form(key: str, fields: str, button: str) -> str:
    return (f'<form method=post action="/setup?key={html.escape(key)}">{fields}'
            f'<button style="font-size:18px;padding:10px 18px">{button}</button></form>')


async def run_setup_server(port: int) -> None:
    api_id, api_hash = int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"]
    phone, key = os.environ.get("TG_PHONE", ""), os.environ.get("SETUP_KEY", "")
    if not key:
        raise RuntimeError("Set SETUP_KEY to use the browser login")
    state: dict = {"client": None, "hash": None, "done": None}

    async def handler(req: web.Request) -> web.Response:
        if req.query.get("key") != key:
            return web.Response(status=403, text="forbidden")
        if state["done"]:
            return web.Response(content_type="text/html", text=PAGE.format(body="<p>Already logged in. Session was shown once; restart the service to start over.</p>"))
        data = await req.post() if req.method == "POST" else {}
        msg = ""
        try:
            if data.get("code") or data.get("password"):
                c: TelegramClient = state["client"]
                try:
                    if data.get("password"):
                        await c.sign_in(password=data["password"])
                    else:
                        await c.sign_in(phone=phone, code=data["code"].strip(), phone_code_hash=state["hash"])
                except SessionPasswordNeededError:
                    body = "<p>2-step verification is on. Enter your Telegram cloud password:</p>" + form(
                        key, '<input name=password type=password style="font-size:18px;width:100%;margin:8px 0">', "Log in")
                    return web.Response(content_type="text/html", text=PAGE.format(body=body))
                s = c.session.save(); state["done"] = True
                me = await c.get_me()
                body = (f"<p>✅ Logged in as <b>{html.escape(me.first_name or '')}</b>.</p>"
                        "<p>Copy this into the <b>TG_SESSION</b> environment variable (shown only once):</p>"
                        f'<textarea readonly style="width:100%;height:160px">{html.escape(s)}</textarea>')
                await c.disconnect()
                return web.Response(content_type="text/html", text=PAGE.format(body=body))
            if data.get("send"):
                c = TelegramClient(StringSession(), api_id, api_hash)
                await c.connect()
                r = await c.send_code_request(phone)
                state.update(client=c, hash=r.phone_code_hash)
                body = f"<p>Code sent to your Telegram app ({html.escape(phone)}). Enter it:</p>" + form(
                    key, '<input name=code inputmode=numeric style="font-size:24px;width:100%;margin:8px 0">', "Log in")
                return web.Response(content_type="text/html", text=PAGE.format(body=body))
        except Exception as e:  # show the error, keep the server up
            log.exception("setup step failed")
            msg = f"<p style=color:#c00>Error: {html.escape(str(e))}</p>"
        body = msg + f"<p>Phone: <b>{html.escape(phone)}</b></p>" + form(key, "<input type=hidden name=send value=1>", "Send code")
        return web.Response(content_type="text/html", text=PAGE.format(body=body))

    async def health(_):
        return web.Response(text="setup mode")

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_route("*", "/setup", handler)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("SETUP MODE on :%s — open /setup?key=…", port)
