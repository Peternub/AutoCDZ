"""Проверки очереди, шифрования и контрактов без реальных аккаунтов."""

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path

from cryptography.fernet import Fernet

from autocdz.domain import Assignment, Answer, Question, QuestionKind, UnsafeContent, calculate, solver_payload, validate_answer
from autocdz.integration_store import IntegrationStore
from autocdz.mesh import MeshConnector
from autocdz.storage import Storage
from autocdz.submission import SubmissionService


def question(kind="number", **kwargs):
    return Question(id="q1", kind=kind, text="Сколько будет 2 + 2?", **kwargs)


def assignment(**kwargs):
    return Assignment(external_id="10", subject="Математика", title="Тест", description="Пример",
                      due_at="2026-12-01", **kwargs)


class ContractsTests(unittest.TestCase):
    def test_numeric_verification(self):
        q = question(calculation="2+2")
        valid = Answer(values=["4"], pairs=[], confidence=0.9, explanation="Сложение")
        validate_answer(q, valid)
        with self.assertRaises(ValueError):
            validate_answer(q, valid.model_copy(update={"values": ["5"]}))
        for expression in ("__import__('os')", "2**999999", "(1).__class__", "1/0"):
            with self.assertRaises((ValueError, ArithmeticError)):
                calculate(expression)

    def test_all_supported_formats(self):
        options = [{"id": "a", "text": "Первый"}, {"id": "b", "text": "Второй"}]
        for kind, values in (("single", ["a"]), ("multiple", ["a", "b"]), ("order", ["b", "a"]), ("short", ["ответ"]), ("number", ["4"])):
            validate_answer(question(kind, options=options), Answer(values=values, pairs=[], confidence=0.9, explanation="Пояснение"))
        q = question("match", options=options, right=options)
        validate_answer(q, Answer(values=[], pairs=[{"left": "a", "right": "b"}, {"left": "b", "right": "a"}], confidence=0.9, explanation="Пояснение"))
        with self.assertRaises(ValueError):
            validate_answer(question("order", options=options), Answer(values=["a"], pairs=[], confidence=1, explanation=""))

    def test_secrets_blocked_and_account_fields_forbidden(self):
        for text in ("Bearer abc", "test@example.com", "+7 (999) 123-45-67", "Cookie: value", "https://example.com/private"):
            with self.assertRaises(UnsafeContent):
                solver_payload(question().model_copy(update={"text": text}))
        with self.assertRaises(ValueError):
            Question(id="1", kind="short", text="Вопрос", token="секрет")
        self.assertNotIn("id", solver_payload(question()))

    def test_mesh_metadata_is_not_submission(self):
        items = MeshConnector.normalize({"payload": [{
            "homework_entry_student_id": 10, "subject_name": "Математика", "description": "Решить тест",
            "date": "2026-12-01", "is_done": True, "materials": [{"url": "https://example.com"}],
            "student_name": "Личные данные", "token": "секрет",
        }]})
        self.assertEqual(items[0].questions, [])
        self.assertNotIn("секрет", items[0].model_dump_json())
        self.assertTrue(items[0].diary_done)


class QueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "test.db")
        self.storage = Storage(self.path)
        await self.storage.open()
        self.store = IntegrationStore(self.path, Fernet.generate_key().decode())
        await self.storage.register(1)
        await self.storage.register(2)
        await self.storage.grant(1, 7, 99)
        self.session = await self.store.connect(1, "secret-session", 123, True)
        await self.store.automation(1, True)

    async def asyncTearDown(self):
        await self.storage.close()
        self.temp.cleanup()

    async def test_encrypted_and_no_cross_user_access(self):
        async with self.storage.db.execute("SELECT sealed_session FROM mesh_connections") as cursor:
            sealed = (await cursor.fetchone())[0]
        self.assertNotIn("secret-session", sealed)
        self.assertNotIn("token", await self.store.connection(1))
        self.assertEqual((await self.store.connection(1, True))["token"], "secret-session")
        self.assertEqual(await self.store.assignments(2), [])

    async def test_deduplication_and_version_change(self):
        task = assignment(questions=[question()])
        self.assertTrue(await self.store.save_assignment(1, self.session, task))
        self.assertFalse(await self.store.save_assignment(1, self.session, task))
        first = await self.store.claim_job()
        self.assertIsNone(await self.store.claim_job())
        changed = task.model_copy(update={"title": "Изменённое задание"})
        await self.store.save_assignment(1, self.session, changed)
        self.assertFalse(await self.store.finish_job(first, answers=[]))
        self.assertIsNotNone(await self.store.claim_job())
        self.assertEqual(len(await self.store.assignments(1)), 1)

    async def test_claim_is_atomic_between_workers(self):
        await self.store.save_assignment(1, self.session, assignment(questions=[question()]))
        claims = await asyncio.gather(self.store.claim_job(), self.store.claim_job())
        self.assertEqual(sum(job is not None for job in claims), 1)

    async def test_expired_subscription_stops_claim_and_completion(self):
        await self.store.save_assignment(1, self.session, assignment(questions=[question()]))
        job = await self.store.claim_job()
        await self.storage.db.execute("UPDATE users SET subscription_until='2000-01-01' WHERE telegram_id=1")
        await self.storage.db.commit()
        self.assertFalse(await self.store.finish_job(job, answers=[]))
        self.assertIsNone(await self.store.claim_job())
        self.assertIsNone(await self.store.claim_sync(1800))

    async def test_restart_and_lease_recovery(self):
        await self.store.save_assignment(1, self.session, assignment(questions=[question()]))
        old = await self.store.claim_job()
        async with self.store.transaction() as db:
            await db.execute("UPDATE processing_jobs SET lease_until=0")
        new_store = IntegrationStore(self.path)
        resumed = await new_store.claim_job()
        self.assertNotEqual(old["lease_token"], resumed["lease_token"])
        self.assertFalse(await self.store.finish_job(old, answers=[]))

    async def test_disconnect_prevents_stale_results_and_connect(self):
        ticket = await self.store.ticket(1)
        await self.store.disconnect(1)
        self.assertFalse(await self.store.save_assignment(1, self.session, assignment()))
        self.assertIsNone(await self.store.connect(1, "secret", 123, ticket=ticket))
        self.assertIsNone(await self.store.connection(1))

    async def test_ticket_single_use_and_deletion_cascade(self):
        ticket = await self.store.ticket(1)
        self.assertEqual(await self.store.consume_ticket(ticket), 1)
        self.assertIsNone(await self.store.consume_ticket(ticket))
        await self.store.save_assignment(1, self.session, assignment(questions=[question()]))
        await self.store.finish_job(await self.store.claim_job(), answers=[])
        await self.storage.delete(1)
        for table in ("mesh_connections", "assignments", "questions", "processing_jobs", "solutions", "submissions", "connection_tickets", "integration_events"):
            async with self.storage.db.execute(f"SELECT COUNT(*) FROM {table}") as cursor:
                self.assertEqual((await cursor.fetchone())[0], 0, table)

    async def test_expired_session_is_removed(self):
        await self.store.sync_result(1, self.session, "expired")
        connection = await self.store.connection(1, True)
        self.assertEqual(connection["status"], "reauth")
        self.assertNotIn("token", connection)

    async def test_no_false_completed_status(self):
        await self.store.save_assignment(1, self.session, assignment(diary_done=True))
        rows = await self.store.assignments(1)
        self.assertEqual(rows[0]["status"], "unsupported")
        result = await SubmissionService().submit(assignment(), [])
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["status"], "disabled")
