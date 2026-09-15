# Развёртывание 0.2.0 по IP для теста

VPS: Ubuntu 24.04, Git, Docker Engine и Compose. Весь код находится в Git; EXE и манифест выпуска передаются отдельно. Домен для теста не нужен. HTTP-режим нельзя считать защищённым для приватного общения.

## 1. Подготовка на сервере

В PuTTY, в папке проекта:

```bash
cd ~/voicechat
git status --short
git pull --ff-only origin main
```

Если Git сообщает о локальных изменениях, сначала сохранить и сравнить их. Не выполнять reset --hard: на сервере могли остаться нужные настройки.

Сохранить резервную копию существующей БД через SQLite backup перед обновлением контейнера. Не удалять Docker volume и не выполнять `docker compose down -v`.

```bash
python3 deploy/configure.py 72.35.246.230
docker compose -f docker-compose.ip.yml --env-file .env.ip config --quiet
docker compose -f docker-compose.ip.yml --env-file .env.ip up -d --build
```

configure сохраняет существующие значения, делает `.env.ip.backup`, создаёт отсутствующие ключи LiveKit и записывает конфигурацию медиасервера. Действующий пароль администратора в БД не меняется. Для новой пустой БД bootstrap-пароль находится в `.env.ip`; не публиковать этот файл.

Разрешить в firewall VPS и панели хостинга входящие TCP 8765, 7880, 7881 и UDP 7882. SSH-порт сохранить доступным. Скрипт не меняет firewall автоматически.

Проверки:

```bash
docker compose -f docker-compose.ip.yml --env-file .env.ip ps
docker compose -f docker-compose.ip.yml --env-file .env.ip logs --tail=60 api livekit
curl http://127.0.0.1:8765/health
```

## 2. Загрузка установщика с компьютера

В PowerShell в папке проекта:

```powershell
scp dist\PrivateVoiceChat-0.2.0-setup.exe dist\PrivateVoiceChat-0.2.0-setup.release.json root@72.35.246.230:/root/voicechat/downloads/
```

## 3. Публикация обновления на сервере

```bash
cd ~/voicechat
python3 deploy/configure.py 72.35.246.230 --installer PrivateVoiceChat-0.2.0-setup.exe
docker compose -f docker-compose.ip.yml --env-file .env.ip up -d --force-recreate api
```

Скрипт проверяет хэш файла против манифеста и подставляет версию, URL, хэш и подпись в `.env.ip`. Клиент независимо проверяет подпись доверенным ключом.

Ссылка после загрузки:

`http://72.35.246.230:8765/downloads/PrivateVoiceChat-0.2.0-setup.exe`

## 4. Проверка вдвоём

Установить 0.2.0 на обоих ПК. Администратор открывает «Пользователи», создаёт второго пользователя и выдаёт ему доступ к нужному каналу. Временный пароль действует 24 часа и меняется при первом входе.

Проверить одновременную речь, local mute/громкость, 10 повторных входов, выключение/включение шума, запуск и остановку видео. Потом проверить длительный разговор и 4K/60 отдельно. Нужны измерения фактического качества, а не только выбранный пункт меню.

## Сборка следующих выпусков

На Windows с Python 3.12 и Inno Setup 6:

```powershell
python -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File deploy\build_client.ps1
```

Перед сборкой новой версии изменить `app/version.py` и метаданные проекта. Проверить, что публичный ключ в `app/update_trust.py` соответствует локальному ключу сборки. Автоматическая генерация другого ключа после утраты старого не обновит доверие уже установленных клиентов.

## Переход к TLS

Когда будет выбран адрес/способ доверия сертификату, настроить HTTPS для API и WSS для LiveKit, закрыть прямые HTTP-порты снаружи и выпустить клиент с защищённым адресом. Не отключать проверку сертификатов ради подключения. Для сложных сетей дополнительно настроить TURN/TLS и проверить соединение через него.
