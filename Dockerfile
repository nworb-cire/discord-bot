FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml /app
COPY alembic.ini /app
COPY uv.lock /app

RUN pip install uv &&  \
    uv sync --locked


COPY src /app/src
COPY migrations /app/migrations
ENV PYTHONPATH=/app/src

ENTRYPOINT ["uv", "run", "--", "sh", "-c", "exec \"$@\"", "dummy"]
CMD ["python3", "src/bot/main.py"]