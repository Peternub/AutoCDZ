"""Мониторинг и выполнение постоянной очереди независимо от Telegram-клиента."""

import asyncio
import logging
from contextlib import suppress

from autocdz.domain import Assignment, UnsafeContent
from autocdz.mesh import MeshUnavailable, SessionExpired
from autocdz.solver import SolverUnavailable
from autocdz.storage import subscription_active
from autocdz.submission import SubmissionService


class Worker:
    def __init__(self, config, storage, store, connector, solver, bot):
        self.config, self.storage, self.store = config, storage, store
        self.connector, self.solver, self.bot = connector, solver, bot

    async def notify(self, user_id, text):
        user = await self.storage.user(user_id)
        if user and user["notifications"]:
            try:
                await self.bot.send_message(user_id, text)
            except Exception as error:
                logging.warning("Не доставлено уведомление: %s", type(error).__name__)

    async def eligible(self, user_id, session_id=None, ai=False):
        if not subscription_active(await self.storage.user(user_id)):
            return None
        connection = await self.store.connection(user_id, decrypt=not ai)
        if not connection or connection["status"] != "active" or not connection["automation"]:
            return None
        if session_id and connection["session_id"] != session_id:
            return None
        if ai and not connection["ai_consent"]:
            return None
        return connection

    async def synchronize(self, user_id):
        connection = await self.eligible(user_id)
        if not connection:
            return
        try:
            tasks = await self.connector.fetch(connection["token"], connection["profile_id"])
            if not await self.eligible(user_id, connection["session_id"]):
                return
            changed = 0
            for assignment in tasks:
                changed += bool(await self.store.save_assignment(user_id, connection["session_id"], assignment))
            await self.store.sync_result(user_id, connection["session_id"], "ok")
            if changed:
                await self.notify(user_id, f"📘 Обнаружено новых или изменённых ДЗ: {changed}. Открой «Мои ЦДЗ». Форматы материалов без подтверждённого парсера не обрабатываются.")
        except SessionExpired:
            await self.store.sync_result(user_id, connection["session_id"], "expired")
            await self.notify(user_id, "🔒 Сессия МЭШ истекла или доступ отклонён. Сохранённая сессия удалена. Подключи аккаунт повторно.")
        except MeshUnavailable:
            await self.store.sync_result(user_id, connection["session_id"], "unavailable")
            await self.notify(user_id, "⚠️ МЭШ временно недоступна. Следующая проверка выполнится по расписанию.")

    async def process_one(self):
        if not self.config.ai_key or not self.config.ai_model:
            return
        job = await self.store.claim_job()
        if not job:
            return
        task = Assignment.model_validate_json(job["payload"])
        answers, code = [], None
        try:
            for question in task.questions:
                if not await self.eligible(job["user_id"], ai=True):
                    code = "paused"
                    break
                answer = await self.solver.solve(question)
                answers.append({"question_id": question.id, **answer.model_dump()})
                if answer.confidence < 0.8:
                    code = "low_confidence"
            await SubmissionService().submit(task, answers)
        except UnsafeContent:
            code = "privacy_blocked"
        except SolverUnavailable:
            code = "temporary"
        finished = await self.store.finish_job(job, answers=answers if answers else None, code=code)
        if finished:
            text = "✅ Решение подготовлено. Отправка в МЭШ отключена." if code is None else "⚠️ Задание требует внимания. Подробности доступны в истории."
            await self.notify(job["user_id"], text)

    async def sync_loop(self):
        while True:
            try:
                user_id = await self.store.claim_sync(self.config.poll_interval)
                if user_id:
                    await self.synchronize(user_id)
            except Exception as error:
                logging.error("Ошибка синхронизации: %s", type(error).__name__)
            await asyncio.sleep(5)

    async def solver_loop(self):
        while True:
            try:
                await self.process_one()
            except Exception as error:
                logging.error("Ошибка обработки очереди: %s", type(error).__name__)
            await asyncio.sleep(5)

    def start(self):
        return [asyncio.create_task(self.sync_loop()), asyncio.create_task(self.solver_loop())]


async def stop_tasks(tasks):
    for task in tasks:
        task.cancel()
    for task in tasks:
        with suppress(asyncio.CancelledError):
            await task
