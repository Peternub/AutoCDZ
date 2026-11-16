"""Ограниченное чтение потоковых ответов внешних сервисов."""


async def read_limited(response, limit):
    result = bytearray()
    async for chunk in response.content.iter_chunked(16384):
        result.extend(chunk)
        if len(result) > limit:
            raise ValueError("Превышен размер ответа")
    return bytes(result)
