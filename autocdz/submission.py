"""Тестовый режим без отправки оцениваемых работ."""


class SubmissionService:
    async def submit(self, assignment, answers):
        # Сетевой клиент отсутствует намеренно: подтверждённого интерфейса сдачи нет.
        return {"status": "disabled", "confirmed": False, "reason": "Отправка в МЭШ не разрешена и не реализована"}
