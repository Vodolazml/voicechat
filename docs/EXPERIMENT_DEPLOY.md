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

## Подготовка клиента без ручного ввода домена

Перед раздачей людям создайте рядом с проектом файл `client_config.json`:

```powershell
Copy-Item client_config.example.json client_config.json
```

Впишите адрес своего сервера:

```json
{
  "server_url": "https://voice.example.com",
  "lock_server_url": true
}
```

Если `lock_server_url` равен `true`, пользователь не сможет менять адрес сервера в окне входа. Он увидит уже готовый адрес и будет вводить только логин и пароль.

`client_config.json` не коммитится в git, потому что это конфиг конкретной раздачи.

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

- логин и временный пароль выдаёт администратор;
- при первом входе пользователь меняет временный пароль.

Если вы положили `client_config.json` рядом с программой, адрес сервера уже будет прописан.

## Обновления клиента

Клиент при входе вызывает:

```text
GET /client/update?version=<текущая версия>
```

Сервер берёт данные обновления из `.env.production`:

```env
VOICECHAT_CLIENT_LATEST_VERSION=0.1.1
VOICECHAT_CLIENT_DOWNLOAD_URL=https://voice.example.com/downloads/PrivateVoiceChat-0.1.1.zip
VOICECHAT_CLIENT_DOWNLOAD_SHA256=<sha256 файла>
VOICECHAT_CLIENT_UPDATE_REQUIRED=false
VOICECHAT_CLIENT_RELEASE_NOTES_URL=https://voice.example.com/releases/0.1.1
```

Если версия новее, клиент покажет окно обновления. При скачивании файл сохраняется в:

```text
%USERPROFILE%\.private_voicechat\updates
```

После загрузки клиент сверяет SHA-256. Если хэш не совпал, файл удаляется.

Обновление не будет предложено, если `VOICECHAT_CLIENT_DOWNLOAD_SHA256` пустой.

Пока нет установщика, клиент открывает скачанный файл, а пользователь ставит или распаковывает обновление вручную. После упаковки в установщик этот же механизм можно довести до автоматического запуска installer/updater.

Если `VOICECHAT_CLIENT_UPDATE_REQUIRED=true`, вход в программу блокируется до обновления.

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
