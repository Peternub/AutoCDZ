"""Экспериментальный адаптер только чтения проверенного интерфейса."""

from datetime import datetime, timedelta, timezone

import aiohttp

from autocdz.domain import Assignment
from autocdz.transport import read_limited


class SessionExpired(Exception):
    pass


class MeshUnavailable(Exception):
    pass


class MeshConnector:
    URL = "https://school.mos.ru/api/family/web/v1/homeworks"

    async def fetch(self, token: str, profile_id: int):
        today = datetime.now(timezone(timedelta(hours=3))).date()
        headers = {
            "Authorization": f"Bearer {token}", "Profile-Id": str(profile_id),
            "Profile-Type": "student", "X-mes-subsystem": "familyweb", "Accept": "application/json",
        }
        params = {"from": str(today - timedelta(days=7)), "to": str(today + timedelta(days=14)), "student_id": str(profile_id)}
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30), trust_env=False) as session:
                async with session.get(self.URL, headers=headers, params=params, allow_redirects=False) as response:
                    if response.status in (401, 403):
                        raise SessionExpired()
                    if response.status != 200:
                        raise MeshUnavailable("МЭШ временно недоступна")
                    # Не читаем неограниченный ответ и не сохраняем его целиком.
                    body = await read_limited(response, 2_000_000)
                    import json
                    data = json.loads(body)
            return self.normalize(data)
        except (aiohttp.ClientError, TimeoutError, ValueError, TypeError, KeyError) as error:
            raise MeshUnavailable("Не удалось прочитать список заданий") from None

    @staticmethod
    def normalize(data):
        if not isinstance(data, dict) or not isinstance(data.get("payload"), list):
            raise ValueError("Неизвестная структура ответа МЭШ")
        if len(data["payload"]) > 1000:
            raise ValueError("Слишком много заданий")
        result = []
        for item in data["payload"]:
            external_id = item["homework_entry_student_id"]
            if type(external_id) is not int or external_id <= 0:
                raise ValueError("Неизвестный идентификатор задания")
            description = item.get("description") or item.get("homework") or ""
            # Формат вопросов материалов не подтверждён. Описание не превращаем в вопрос.
            result.append(Assignment(
                external_id=str(external_id), subject=item["subject_name"],
                title="Домашнее задание", description=description,
                due_at=item.get("date_prepared_for") or item["date"],
                has_materials=bool(item.get("materials") or item.get("attachments")),
                diary_done=item.get("is_done") is True,
            ))
        return result
