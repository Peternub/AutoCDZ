FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock.txt .
RUN pip install --no-cache-dir -r requirements.lock.txt \
    && useradd --uid 10001 --create-home bot \
    && mkdir /app/data && chown bot:bot /app/data
COPY --chown=bot:bot autocdz ./autocdz
USER bot
CMD ["python", "-m", "autocdz"]
