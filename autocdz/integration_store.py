"""Транзакции интеграции, шифрование сессий и постоянная очередь."""

import hashlib
import json
import secrets
import time
from contextlib import asynccontextmanager

import aiosqlite
from cryptography.fernet import Fernet

from autocdz.domain import Assignment
from autocdz.storage import now


class IntegrationStore:
    def __init__(self, path, encryption_key=""):
        self.path = path
        self.cipher = Fernet(encryption_key.encode()) if encryption_key else None

    @asynccontextmanager
    async def transaction(self):
        async with aiosqlite.connect(self.path, timeout=15) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    async def ticket(self, user_id):
        ticket = secrets.token_urlsafe(32)
        async with self.transaction() as db:
            await db.execute("DELETE FROM connection_tickets WHERE expires < ? OR user_id=?", (time.time(), user_id))
            await db.execute("INSERT INTO connection_tickets VALUES (?, ?, ?)",
                             (hashlib.sha256(ticket.encode()).hexdigest(), user_id, time.time() + 600))
        return ticket

    async def consume_ticket(self, ticket):
        if not 30 <= len(ticket) <= 100:
            return None
        async with self.transaction() as db:
            async with db.execute("DELETE FROM connection_tickets WHERE digest=? AND expires>? RETURNING user_id",
                                  (hashlib.sha256(ticket.encode()).hexdigest(), time.time())) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    async def ticket_owner(self, ticket):
        if not 30 <= len(ticket) <= 100:
            return None
        async with self.transaction() as db:
            async with db.execute("SELECT user_id FROM connection_tickets WHERE digest=? AND expires>?",
                                  (hashlib.sha256(ticket.encode()).hexdigest(), time.time())) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    async def connect(self, user_id, token, profile_id, ai_consent=False, ticket=None):
        if not self.cipher:
            raise ValueError("Ключ шифрования не настроен")
        sealed = self.cipher.encrypt(json.dumps({"token": token, "profile_id": profile_id}).encode()).decode()
        session_id = secrets.token_hex(16)
        async with self.transaction() as db:
            if ticket is not None:
                async with db.execute("DELETE FROM connection_tickets WHERE digest=? AND user_id=? AND expires>? RETURNING user_id",
                                      (hashlib.sha256(ticket.encode()).hexdigest(), user_id, time.time())) as cursor:
                    if not await cursor.fetchone():
                        return None
            # Отмена удаления пользователем имеет приоритет над завершением сетевого запроса.
            async with db.execute("SELECT 1 FROM users WHERE telegram_id=?", (user_id,)) as cursor:
                if not await cursor.fetchone():
                    return None
            await db.execute("""INSERT INTO mesh_connections(user_id,session_id,sealed_session,ai_consent)
                VALUES (?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
                session_id=excluded.session_id,sealed_session=excluded.sealed_session,status='active',
                ai_consent=excluded.ai_consent,automation=0,next_sync=0,failures=0""",
                             (user_id, session_id, sealed, int(ai_consent)))
            await db.execute("INSERT INTO integration_events(user_id,code,created_at) VALUES (?,'connected',?)", (user_id, now().isoformat()))
        return session_id

    async def connection(self, user_id, decrypt=False):
        async with self.transaction() as db:
            async with db.execute("SELECT * FROM mesh_connections WHERE user_id=?", (user_id,)) as cursor:
                row = await cursor.fetchone()
        if not row:
            return None
        result = dict(row)
        sealed = result.pop("sealed_session")
        if decrypt and sealed and self.cipher:
            result.update(json.loads(self.cipher.decrypt(sealed.encode())))
        return result

    async def disconnect(self, user_id):
        async with self.transaction() as db:
            await db.execute("DELETE FROM connection_tickets WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM mesh_connections WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM integration_events WHERE user_id=?", (user_id,))

    async def automation(self, user_id, enabled):
        async with self.transaction() as db:
            async with db.execute("""UPDATE mesh_connections SET automation=? WHERE user_id=? AND status='active'
                AND EXISTS(SELECT 1 FROM users WHERE telegram_id=? AND subscription_until>?) RETURNING user_id""",
                                  (int(enabled), user_id, user_id, now().isoformat())) as cursor:
                return bool(await cursor.fetchone())

    async def claim_sync(self, interval):
        async with self.transaction() as db:
            async with db.execute("""SELECT c.user_id FROM mesh_connections c JOIN users u ON u.telegram_id=c.user_id
                WHERE c.status='active' AND c.automation=1 AND c.next_sync<=? AND u.subscription_until>?
                ORDER BY c.next_sync LIMIT 1""", (time.time(), now().isoformat())) as cursor:
                row = await cursor.fetchone()
            if not row:
                return None
            await db.execute("UPDATE mesh_connections SET next_sync=? WHERE user_id=?", (time.time() + interval, row[0]))
            return row[0]

    async def sync_result(self, user_id, session_id, code):
        async with self.transaction() as db:
            if code == "expired":
                await db.execute("""UPDATE mesh_connections SET status='reauth',sealed_session=NULL,automation=0
                    WHERE user_id=? AND session_id=?""", (user_id, session_id))
            else:
                await db.execute("""UPDATE mesh_connections SET failures=CASE WHEN ?='ok' THEN 0 ELSE failures+1 END
                    WHERE user_id=? AND session_id=?""", (code, user_id, session_id))
            await db.execute("""INSERT INTO integration_events(user_id,code,created_at)
                SELECT ?,?,? WHERE EXISTS(SELECT 1 FROM mesh_connections WHERE user_id=? AND session_id=?)""",
                             (user_id, code, now().isoformat(), user_id, session_id))

    async def save_assignment(self, user_id, session_id, assignment: Assignment):
        payload = assignment.model_dump_json()
        # Отметка в дневнике не изменяет решение или версию содержания.
        content = assignment.model_dump(exclude={"diary_done"})
        digest = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        status = "queued" if assignment.questions else "unsupported"
        async with self.transaction() as db:
            async with db.execute("SELECT 1 FROM mesh_connections WHERE user_id=? AND session_id=? AND status='active'", (user_id, session_id)) as cursor:
                if not await cursor.fetchone():
                    return False
            async with db.execute("SELECT id,content_hash FROM assignments WHERE user_id=? AND external_id=?", (user_id, assignment.external_id)) as cursor:
                existing = await cursor.fetchone()
            if existing and existing["content_hash"] == digest:
                return False
            async with db.execute("""INSERT INTO assignments(user_id,external_id,content_hash,payload,status,updated_at)
                VALUES (?,?,?,?,?,?) ON CONFLICT(user_id,external_id) DO UPDATE SET content_hash=excluded.content_hash,
                payload=excluded.payload,status=excluded.status,updated_at=excluded.updated_at RETURNING id""",
                                  (user_id, assignment.external_id, digest, payload, status, now().isoformat())) as cursor:
                assignment_id = (await cursor.fetchone())[0]
            await db.execute("DELETE FROM questions WHERE assignment_id=?", (assignment_id,))
            for question in assignment.questions:
                await db.execute("INSERT INTO questions(assignment_id,external_id,payload) VALUES (?,?,?)", (assignment_id, question.id, question.model_dump_json()))
            await db.execute("UPDATE processing_jobs SET state='superseded' WHERE assignment_id=? AND state IN ('pending','processing')", (assignment_id,))
            if assignment.questions:
                await db.execute("""INSERT INTO processing_jobs(assignment_id,content_hash) VALUES (?,?)
                    ON CONFLICT(assignment_id,content_hash) DO UPDATE SET state='pending',attempts=0,
                    lease_until=0,lease_token=NULL,error_code=NULL""", (assignment_id, digest))
            return True

    async def assignments(self, user_id, limit=10):
        async with self.transaction() as db:
            async with db.execute("SELECT id,status,payload FROM assignments WHERE user_id=? ORDER BY updated_at DESC,id DESC LIMIT ?", (user_id, limit)) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def claim_job(self):
        stamp = time.time()
        lease = secrets.token_hex(16)
        async with self.transaction() as db:
            # Задачи после аварии возвращаются в очередь с ограниченным числом попыток.
            await db.execute("UPDATE processing_jobs SET state='pending' WHERE state='processing' AND lease_until<? AND attempts<3", (stamp,))
            await db.execute("""UPDATE assignments SET status='attention' WHERE id IN
                (SELECT assignment_id FROM processing_jobs WHERE state='processing' AND lease_until<? AND attempts>=3)""", (stamp,))
            await db.execute("UPDATE processing_jobs SET state='error',error_code='lease_exhausted' WHERE state='processing' AND lease_until<? AND attempts>=3", (stamp,))
            async with db.execute("""SELECT j.id,a.user_id,a.payload FROM processing_jobs j
                JOIN assignments a ON a.id=j.assignment_id AND a.content_hash=j.content_hash
                JOIN mesh_connections c ON c.user_id=a.user_id JOIN users u ON u.telegram_id=a.user_id
                WHERE j.state='pending' AND j.lease_until<=? AND j.attempts<3 AND c.status='active'
                AND c.automation=1 AND c.ai_consent=1 AND u.subscription_until>? LIMIT 1""", (stamp, now().isoformat())) as cursor:
                row = await cursor.fetchone()
            if not row:
                return None
            await db.execute("UPDATE processing_jobs SET state='processing',attempts=attempts+1,lease_token=?,lease_until=? WHERE id=?", (lease, stamp + 120, row["id"]))
            return {**dict(row), "lease_token": lease}

    async def extend_lease(self, job):
        async with self.transaction() as db:
            async with db.execute("""UPDATE processing_jobs SET lease_until=? WHERE id=?
                AND lease_token=? AND state='processing' RETURNING id""",
                                  (time.time() + 120, job["id"], job["lease_token"])) as cursor:
                return bool(await cursor.fetchone())

    async def release_job(self, job):
        async with self.transaction() as db:
            await db.execute("""UPDATE assignments SET status='attention' WHERE id IN
                (SELECT assignment_id FROM processing_jobs WHERE id=? AND lease_token=?
                AND state='processing' AND attempts>=3)""", (job["id"], job["lease_token"]))
            await db.execute("""UPDATE processing_jobs SET state='pending',lease_until=0
                WHERE id=? AND lease_token=? AND state='processing'""", (job["id"], job["lease_token"]))
            await db.execute("""UPDATE processing_jobs SET state='error',error_code='attempts_exhausted'
                WHERE id=? AND state='pending' AND attempts>=3""", (job["id"],))

    async def finish_job(self, job, answers=None, code=None):
        async with self.transaction() as db:
            async with db.execute("""SELECT j.attempts,j.assignment_id,c.automation,u.subscription_until
                FROM processing_jobs j JOIN assignments a ON a.id=j.assignment_id AND a.content_hash=j.content_hash
                JOIN mesh_connections c ON c.user_id=a.user_id JOIN users u ON u.telegram_id=a.user_id
                WHERE j.id=? AND j.lease_token=? AND j.state='processing' AND c.status='active' AND c.ai_consent=1""", (job["id"], job["lease_token"])) as cursor:
                row = await cursor.fetchone()
            if not row:
                return False
            if not row["automation"] or not row["subscription_until"] or row["subscription_until"] <= now().isoformat():
                await db.execute("UPDATE processing_jobs SET state='pending',lease_until=0 WHERE id=?", (job["id"],))
                return False
            retry = code == "temporary" and row["attempts"] < 3
            state = "pending" if retry else ("done" if code is None else "error")
            status = "queued" if retry else ("ready" if code is None else "attention")
            await db.execute("UPDATE processing_jobs SET state=?,error_code=?,lease_until=? WHERE id=?", (state, code, time.time() + 60 * row["attempts"] if retry else 0, job["id"]))
            await db.execute("UPDATE assignments SET status=?,updated_at=? WHERE id=?", (status, now().isoformat(), row["assignment_id"]))
            if answers is not None:
                await db.execute("INSERT OR REPLACE INTO solutions VALUES (?,?,?)", (job["id"], json.dumps(answers, ensure_ascii=False), now().isoformat()))
                await db.execute("INSERT OR IGNORE INTO submissions VALUES (?,'disabled',0,?)", (job["id"], now().isoformat()))
            return not retry

    async def history(self, user_id):
        async with self.transaction() as db:
            async with db.execute("""SELECT a.id,a.payload,a.status,s.payload AS solution FROM assignments a
                JOIN processing_jobs j ON j.assignment_id=a.id AND j.content_hash=a.content_hash
                LEFT JOIN solutions s ON s.job_id=j.id WHERE a.user_id=? ORDER BY a.updated_at DESC LIMIT 10""", (user_id,)) as cursor:
                return [dict(row) for row in await cursor.fetchall()]
