"""Проверки HTTPS-подключения, фоновой работы и внешних контрактов."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp.test_utils import TestClient, TestServer
from cryptography.fernet import Fernet

from autocdz.config import Config
from autocdz.domain import Answer, UnsafeContent
from autocdz.integration_store import IntegrationStore
from autocdz.mesh import MeshConnector, SessionExpired
from autocdz.solver import AISolver, SolverUnavailable
from autocdz.storage import Storage
from autocdz.web import create_app
from autocdz.worker import Worker
from test_integration import assignment, question


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "test.db")
        self.storage = Storage(self.path)
        await self.storage.open()
        await self.storage.register(99)
        await self.storage.grant(99, 7, 99)
        self.config = Config("unused", 99, self.path, mesh_enabled=True, public_url="https://test.example",
                             encryption_key=Fernet.generate_key().decode(), ai_key="test-key", ai_model="test-model")
        self.store = IntegrationStore(self.path, self.config.encryption_key)
        self.connector = MagicMock(fetch=AsyncMock(return_value=[assignment()]))
        self.client = TestClient(TestServer(create_app(self.config, self.store, self.connector)))
        await self.client.start_server()
        self.headers = {"Host": "test.example", "Origin": self.config.public_url, "X-Forwarded-Proto": "https"}
        self.bot = MagicMock(send_message=AsyncMock())
        self.solver = MagicMock(solve=AsyncMock(return_value=Answer(values=["4"], pairs=[], confidence=0.95, explanation="Проверено")))
        self.worker = Worker(self.config, self.storage, self.store, self.connector, self.solver, self.bot)

    async def asyncTearDown(self):
        await self.client.close()
        await self.storage.close()
        self.temp.cleanup()

    async def body(self):
        return {"ticket": await self.store.ticket(99), "token": "own-session-token", "profile_id": 123, "consent": True, "ai_consent": True}

    async def connect(self):
        body = await self.body()
        response = await self.client.post("/connect", json=body, headers=self.headers)
        self.assertEqual(response.status, 200)
        self.assertNotIn("own-session-token", await response.text())
        return body

    async def test_https_origin_and_one_time_link(self):
        body = await self.body()
        for headers in ({"Host": "test.example"}, {**self.headers, "Origin": "https://attacker.example"}):
            response = await self.client.post("/connect", json=body, headers=headers)
            self.assertEqual(response.status, 403)
        await self.connect()
        used = await self.client.post("/connect", json=body, headers=self.headers)
        self.assertEqual(used.status, 403)

    async def test_actual_replay_is_rejected(self):
        body = await self.connect()
        replay = await self.client.post("/connect", json=body, headers=self.headers)
        self.assertEqual(replay.status, 403)
        self.assertEqual(self.connector.fetch.await_count, 1)

    async def test_disconnect_during_check_cannot_resurrect_session(self):
        body = await self.body()
        async def fetch(*args):
            await self.store.disconnect(99)
            return []
        self.connector.fetch.side_effect = fetch
        response = await self.client.post("/connect", json=body, headers=self.headers)
        self.assertEqual(response.status, 409)
        self.assertIsNone(await self.store.connection(99))

    async def test_failed_auth_clears_ticket_without_storing_token(self):
        body = await self.body()
        self.connector.fetch.side_effect = SessionExpired
        response = await self.client.post("/connect", json=body, headers=self.headers)
        self.assertEqual(response.status, 400)
        self.assertIsNone(await self.store.ticket_owner(body["ticket"]))
        self.assertIsNone(await self.store.connection(99))

    async def test_owner_only_form_and_password_rejected(self):
        await self.storage.register(1)
        body = await self.body()
        body["ticket"] = await self.store.ticket(1)
        response = await self.client.post("/connect", json=body, headers=self.headers)
        self.assertEqual(response.status, 403)
        body = await self.body()
        body["password"] = "never-store"
        response = await self.client.post("/connect", json=body, headers=self.headers)
        self.assertEqual(response.status, 400)

    async def test_worker_monitoring_deduplicates_and_auth_expiry_stops(self):
        await self.connect()
        await self.store.automation(99, True)
        await self.worker.synchronize(99)
        await self.worker.synchronize(99)
        self.assertEqual(len(await self.store.assignments(99)), 1)
        self.assertEqual(self.bot.send_message.await_count, 1)
        self.connector.fetch.side_effect = SessionExpired
        await self.worker.synchronize(99)
        connection = await self.store.connection(99, True)
        self.assertNotIn("token", connection)
        self.assertFalse(connection["automation"])

    async def test_worker_saves_solution_but_does_not_submit(self):
        await self.connect()
        await self.store.automation(99, True)
        connection = await self.store.connection(99)
        await self.store.save_assignment(99, connection["session_id"], assignment(questions=[question()]))
        await self.worker.process_one()
        self.assertEqual((await self.store.assignments(99))[0]["status"], "ready")
        async with self.storage.db.execute("SELECT confirmed,status FROM submissions") as cursor:
            row = await cursor.fetchone()
            self.assertEqual(tuple(row), (0, "disabled"))
        await self.worker.process_one()
        self.assertEqual(self.solver.solve.await_count, 1)

    async def test_worker_blocks_no_consent_and_low_confidence(self):
        await self.connect()
        await self.store.automation(99, True)
        connection = await self.store.connection(99)
        await self.store.save_assignment(99, connection["session_id"], assignment(questions=[question()]))
        async with self.store.transaction() as db:
            await db.execute("UPDATE mesh_connections SET ai_consent=0")
        await self.worker.process_one()
        self.solver.solve.assert_not_called()
        async with self.store.transaction() as db:
            await db.execute("UPDATE mesh_connections SET ai_consent=1")
        self.solver.solve.return_value = Answer(values=["4"], pairs=[], confidence=0.2, explanation="Не уверен")
        await self.worker.process_one()
        self.assertEqual((await self.store.assignments(99))[0]["status"], "attention")

    async def test_temporary_solver_errors_have_bounded_retries(self):
        await self.connect()
        await self.store.automation(99, True)
        connection = await self.store.connection(99)
        await self.store.save_assignment(99, connection["session_id"], assignment(questions=[question()]))
        self.solver.solve.side_effect = SolverUnavailable
        for _ in range(4):
            async with self.store.transaction() as db:
                await db.execute("UPDATE processing_jobs SET lease_until=0")
            await self.worker.process_one()
        self.assertEqual(self.solver.solve.await_count, 3)
        self.assertEqual((await self.store.assignments(99))[0]["status"], "attention")


def mocked_session(data, status=200):
    encoded = json.dumps(data).encode()
    async def chunks(size):
        # Два отдельных сетевых фрагмента должны собираться полностью.
        yield encoded[:5]
        yield encoded[5:]
    response = MagicMock(status=status)
    response.content.iter_chunked = chunks
    request = MagicMock()
    request.__aenter__ = AsyncMock(return_value=response)
    request.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get.return_value = request
    session.post.return_value = request
    manager = MagicMock()
    manager.__aenter__ = AsyncMock(return_value=session)
    manager.__aexit__ = AsyncMock(return_value=False)
    return manager, session


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_mesh_uses_only_known_read_endpoint(self):
        manager, session = mocked_session({"payload": []})
        with patch("autocdz.mesh.aiohttp.ClientSession", return_value=manager):
            self.assertEqual(await MeshConnector().fetch("secret", 123), [])
        args, kwargs = session.get.call_args
        self.assertEqual(args[0], MeshConnector.URL)
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["params"]["student_id"], "123")
        session.post.assert_not_called()

    async def test_solver_structured_output_and_privacy(self):
        answer = Answer(values=["4"], pairs=[], confidence=0.95, explanation="Сложение")
        manager, session = mocked_session({"choices": [{"message": {"content": answer.model_dump_json()}}]})
        with patch("autocdz.solver.aiohttp.ClientSession", return_value=manager):
            result = await AISolver("provider-secret", "test-model").solve(question(calculation="2+2"))
        self.assertEqual(result.values, ["4"])
        payload = session.post.call_args.kwargs["json"]
        self.assertEqual(payload["response_format"]["type"], "json_schema")
        self.assertNotIn("provider-secret", json.dumps(payload))
        session.get.assert_not_called()

    async def test_secrets_do_not_reach_network(self):
        with patch("autocdz.solver.aiohttp.ClientSession") as session:
            with self.assertRaises(UnsafeContent):
                await AISolver("key", "model").solve(question().model_copy(update={"text": "Bearer private"}))
            session.assert_not_called()
