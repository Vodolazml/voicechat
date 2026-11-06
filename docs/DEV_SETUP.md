# Настройка разработки

Проект хранит текстовые файлы в UTF-8. Для интерфейса используем русский текст, для кода и имен файлов по возможности ASCII, чтобы меньше зависеть от настроек Windows, Docker и сервера.

## Локально на Windows

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -m uvicorn app.server.main:app --reload --host 127.0.0.1 --port 8765
```

## Через Docker

```powershell
Copy-Item .env.example .env
docker compose up --build
```

API будет доступен на `http://127.0.0.1:8765`.

Клиент запускается отдельно:

```powershell
python run_client.py
```

Первый пользователь создается автоматически:

- логин: `admin`;
- пароль: значение `VOICECHAT_BOOTSTRAP_PASSWORD` из `.env`, а если оно пустое — одноразовый пароль, который сервер печатает в консоль при первом запуске.

В `.env` для разработки обязательно `VOICECHAT_ENVIRONMENT=development` и свой `VOICECHAT_SECRET_KEY` (`python -c "import secrets; print(secrets.token_urlsafe(48))"`).

## Git

Перед первым коммитом проверь:

```powershell
git status
git add .
git commit -m "Initial project setup"
```

Секреты, локальная БД, логи и виртуальное окружение не должны попадать в репозиторий.
