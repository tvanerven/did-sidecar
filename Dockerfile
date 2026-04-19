FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# uv installs project dependencies into /app/.venv by default.
ENV PATH="/app/.venv/bin:${PATH}"

COPY uv.lock pyproject.toml ./
# Install uv deterministically in /usr/local/bin
RUN pip install --no-cache-dir uv
RUN uv sync --frozen --no-dev

COPY app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
