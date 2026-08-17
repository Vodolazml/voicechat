# Первая версия: GitHub, VPS по IP, EXE для пользователей

Это временный сценарий без домена. Пользователи подключаются к:

```text
http://YOUR_SERVER_IP:8765
```

Важно: это экспериментальный режим без HTTPS/WSS. Используйте его только для короткого теста с доверенными людьми.

## 1. Создать репозиторий на GitHub

1. Зайдите на https://github.com/new
2. Repository name: например `private-voicechat`
3. Visibility: лучше `Private`
4. Не ставьте галочки `README`, `.gitignore`, `license`, потому что проект уже создан локально.
5. Нажмите `Create repository`.

В локальной папке проекта:

```powershell
git remote add origin https://github.com/YOUR_LOGIN/private-voicechat.git
git branch -M main
git push -u origin main
```

Если remote уже есть:

```powershell
git remote -v
git remote set-url origin https://github.com/YOUR_LOGIN/private-voicechat.git
git push -u origin main
```

## 2. Поднять сервер по IP

На VPS с Ubuntu 24.04:

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo tee /etc/apt/keyrings/docker.asc >/dev/null
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Скачать проект:

```bash
git clone https://github.com/YOUR_LOGIN/private-voicechat.git
cd private-voicechat
```

Создать `.env.ip`:

```bash
cp .env.ip.example .env.ip
openssl rand -base64 32
nano .env.ip
```

Замените:

```env
VOICECHAT_SECRET_KEY=<вывод openssl rand -base64 32>
VOICECHAT_BOOTSTRAP_PASSWORD=<временный пароль администратора>
VOICECHAT_ALLOWED_HOSTS=["YOUR_SERVER_IP"]
```

Запуск:

```bash
docker compose -f docker-compose.ip.yml --env-file .env.ip up -d --build
docker compose -f docker-compose.ip.yml --env-file .env.ip logs -f
```

Проверка:

```bash
curl http://YOUR_SERVER_IP:8765/health
```

Ожидаемый ответ:

```json
{"status":"ok","encoding":"utf-8"}
```

## 3. Подготовить клиент под IP

На компьютере разработчика создайте рядом с EXE файл `client_config.json`:

```json
{
  "server_url": "http://YOUR_SERVER_IP:8765",
  "lock_server_url": true,
  "allow_insecure_http": true
}
```

Пользователь не будет вводить адрес сервера вручную.

## 4. Собрать EXE

```powershell
python -m pip install pyinstaller
pyinstaller --noconfirm --windowed --name PrivateVoiceChat --collect-all PySide6 --collect-all sounddevice app/client/main.py
```

После сборки:

```text
dist\PrivateVoiceChat\PrivateVoiceChat.exe
```

Положите `client_config.json` в:

```text
dist\PrivateVoiceChat\client_config.json
```

Потом архивируйте папку:

```powershell
Compress-Archive -Path dist\PrivateVoiceChat\* -DestinationPath dist\PrivateVoiceChat-0.1.0-ip.zip -Force
```

## 5. Что дать двум пользователям

Передайте каждому:

- архив `PrivateVoiceChat-0.1.0-ip.zip`;
- логин;
- временный пароль.

Пользователь:

1. Распаковывает архив.
2. Запускает `PrivateVoiceChat.exe`.
3. Вводит логин и временный пароль.
4. Меняет пароль при первом входе.

## 6. После теста

Когда появится домен, переключитесь на HTTPS-сценарий из `docs/EXPERIMENT_DEPLOY.md`.
