FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=UTF-8

WORKDIR /app

RUN python -m pip install --no-cache-dir --upgrade pip

COPY requirements-server.txt .
RUN python -m pip install --no-cache-dir -r requirements-server.txt

COPY . .

RUN useradd --uid 10001 --create-home voicechat
USER 10001:10001

EXPOSE 8765

CMD ["python", "-m", "uvicorn", "app.server.main:app", "--host", "0.0.0.0", "--port", "8765"]
