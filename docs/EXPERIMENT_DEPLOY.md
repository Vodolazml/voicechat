# Экспериментальная выгрузка на сервер

Цель: поднять закрытый сервер для тестовой группы и раздать клиент людям.

## Требования

- VPS или выделенный сервер с Linux.
- Домен или поддомен, например `voice.example.com`.
- DNS `A`/`AAAA` запись домена указывает на сервер.
- На firewall открыты только `22`, `80`, `443`.
- Docker и Docker Compose установлены.

Порт `8765` наружу открывать не нужно: он доступен только внутри Docker-сети.

## Подготовка `.env.production`

```bash
cp .env.production.example .env.production
openssl rand -base64 32
```

В `.env.production` замените:

```env
VOICECHAT_DOMAIN=voice.example.com
VOICECHAT_SECRET_KEY=<вывод openssl rand -base64 32>
VOICECHAT_BOOTSTRAP_PASSWORD=<временный пароль администратора 12+ символов>
VOICECHAT_ALLOWED_HOSTS=["voice.example.com"]
VOICECHAT_CORS_ORIGINS=[]
```

Не коммитьте `.env.production`.

## Запуск

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
docker compose -f docker-compose.prod.yml --env-file .env.production ps
docker compose -f docker-compose.prod.yml --env-file .env.production logs -f caddy api
```

Caddy автоматически выпустит TLS-сертификат. Клиенты должны подключаться к:

```text
https://voice.example.com
```

## Проверка

```bash
curl https://voice.example.com/health
```

Ожидаемый ответ:

```json
{"status":"ok","encoding":"utf-8"}
```

## Раздача клиента

Пока установщика нет, участникам можно дать архив проекта или приватный git-доступ.

На компьютере участника:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m app.client.main
```

В окне входа:

- сервер: `https://voice.example.com`;
- логин и временный пароль выдаёт администратор;
- при первом входе пользователь меняет временный пароль.

Адрес сервера сохраняется локально после успешного входа.

## Минимальная безопасность для эксперимента

- Используйте только HTTPS/WSS.
- Не открывайте порт `8765` наружу.
- Создавайте отдельного пользователя для каждого участника.
- Не выдавайте всем права администратора.
- После эксперимента меняйте `VOICECHAT_SECRET_KEY`, если сервер или база могли попасть в чужие руки.
- Перед показом экрана закрывайте лишние окна: текущий прототип захватывает весь основной экран.

## Что ещё не production

- Нет установщика и подписи клиента.
- Нет автоматических обновлений клиента.
- Нет UI проверки E2EE-отпечатков участников.
- Нет ротации sender-key при выходе участника.
- Нет резервного копирования и миграций БД.
