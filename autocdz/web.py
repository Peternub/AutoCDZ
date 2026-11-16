"""Однократное подключение собственной сессии через HTTPS за обратным прокси."""

import asyncio
import re
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web

from autocdz.mesh import SessionExpired, MeshUnavailable


def create_app(config, store, connector):
    connect_lock = asyncio.Lock()

    @web.middleware
    async def security(request, handler):
        if not config.mesh_enabled or request.host != urlsplit(config.public_url).netloc:
            raise web.HTTPNotFound()
        if not request.secure and request.headers.get("X-Forwarded-Proto") != "https":
            raise web.HTTPForbidden(text="Требуется HTTPS")
        if request.method == "POST" and request.headers.get("Origin") != config.public_url:
            raise web.HTTPForbidden(text="Недопустимый источник запроса")
        try:
            response = await handler(request)
        except web.HTTPException as error:
            response = web.Response(status=error.status, body=error.body, headers=error.headers)
        except Exception:
            # Не выводим тело запроса и тексты внешних исключений.
            response = web.json_response({"message": "Не удалось подключить аккаунт. Создай новую ссылку в боте."}, status=503)
        response.headers.update({
            "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
            "Strict-Transport-Security": "max-age=31536000",
            "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self'; form-action 'none'; frame-ancestors 'none'; base-uri 'none'",
        })
        return response

    app = web.Application(middlewares=[security], client_max_size=16384)

    async def page(request):
        return web.Response(text=(Path(__file__).parent / "static" / "connect.html").read_text(encoding="utf-8"), content_type="text/html")

    async def script(request):
        return web.Response(text=(Path(__file__).parent / "static" / "connect.js").read_text(encoding="utf-8"), content_type="application/javascript")

    async def connect(request):
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType()
        if connect_lock.locked():
            return web.json_response({"message": "Проверка уже выполняется. Попробуй позже."}, status=429)
        async with connect_lock:
            try:
                data = await request.json()
                if not isinstance(data, dict) or set(data) != {"ticket", "token", "profile_id", "consent", "ai_consent"}:
                    raise ValueError()
                ticket = data["ticket"]
                if not isinstance(ticket, str):
                    raise ValueError()
                user_id = await store.ticket_owner(ticket)
                if user_id != config.admin_id:
                    raise web.HTTPForbidden(text="Ссылка истекла или уже использована")
                token = data["token"]
                if not isinstance(token, str):
                    raise ValueError()
                token = token.strip().removeprefix("Bearer ")
                profile_id = data["profile_id"]
                if not re.fullmatch(r"[A-Za-z0-9._~+/=-]{10,8192}", token) or type(profile_id) is not int or not 0 < profile_id < 2**63:
                    raise ValueError()
                if data["consent"] is not True or type(data["ai_consent"]) is not bool:
                    raise ValueError()
            except (ValueError, TypeError, KeyError):
                return web.json_response({"message": "Проверь поля и согласие."}, status=400)
            # Ограничиваем неудачные проверки: ошибка аннулирует ссылку.
            try:
                await connector.fetch(token, profile_id)
            except (SessionExpired, MeshUnavailable):
                await store.consume_ticket(ticket)
                return web.json_response({"message": "Сессия не подтверждена. Создай новую ссылку в боте."}, status=400)
            session_id = await store.connect(user_id, token, profile_id, data["ai_consent"], ticket=ticket)
            if not session_id:
                return web.json_response({"message": "Ссылка отозвана. Подключение отменено."}, status=409)
            return web.json_response({"message": "Сессия проверена и сохранена зашифрованной. Вернись в бот и включи автоматизацию. Отправка работ отключена."})

    app.router.add_get("/connect", page)
    app.router.add_get("/connect.js", script)
    app.router.add_post("/connect", connect)
    return app


async def start_web(config, store, connector):
    runner = web.AppRunner(create_app(config, store, connector), access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner, config.web_host, config.web_port).start()
    except BaseException:
        await runner.cleanup()
        raise
    return runner
