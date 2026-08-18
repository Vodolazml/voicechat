# Деплой обновлений PrivateVoiceChat

## Структура файлов на сервере

```
/voicechat/
├── downloads/                    # Папка для раздачи файлов (настроить VOICECHAT_DOWNLOADS_DIR)
│   ├── PrivateVoiceChat-1.0.0-setup.exe
│   ├── PrivateVoiceChat-1.0.0-setup.exe.sha256
│   ├── PrivateVoiceChat-1.0.0.zip
│   ├── PrivateVoiceChat-1.0.0.zip.sha256
│   └── ... (новые версии добавляются)
├── app/
│   ├── server/
│   │   └── main.py              # Сервер с endpoint /client/update
│   └── client/
│       ├── auto_updater.py      # Клиентская логика автообновления
│       └── updater.py           # UpdateWorkflow класс
└── build/
    ├── setup.iss                # Inno Setup скрипт
    └── build.bat                # Скрипт сборки
```

## Сборка новой версии

### Требования:
1. **Inno Setup 6+**: https://jrsoftware.org/isdl.php (установить в PATH)
2. **PyInstaller**: `pip install pyinstaller`
3. **Python 3.12+**

### Шаг 1: Обновить версию
```powershell
# Редактировать app/version.py
APP_VERSION = "1.0.1"  # Новая версия
```

### Шаг 2: Запустить сборку
```powershell
cd build
.\build.bat
```

Это создаст:
- `dist/PrivateVoiceChat-{version}-setup.exe` — установщик
- `dist/PrivateVoiceChat-{version}.zip` — ZIP пакет для автообновления
- `dist/*.sha256` — файлы хэшей

### Шаг 3: Загрузить на сервер
```powershell
# Скопировать файлы в папку downloads на сервере
Copy-Item dist\*.* ..\deploy\downloads\
```

Или через SCP:
```bash
scp dist/* user@server:/path/to/voicechat/downloads/
```

## Настройка сервера

### Переменные окружения:
```bash
# Обязательные
VOICECHAT_SECRET_KEY=<random-32-bytes>
VOICECHAT_BOOTSTRAP_PASSWORD=Admin12345!Local

# Обновления (настроить после первой сборки)
VOICECHAT_CLIENT_LATEST_VERSION=1.0.0
VOICECHAT_CLIENT_DOWNLOAD_URL=https://yourserver.com/downloads/PrivateVoiceChat-1.0.0-setup.exe
VOICECHAT_CLIENT_DOWNLOAD_SHA256=<хэш из .sha256 файла>
VOICECHAT_CLIENT_UPDATE_REQUIRED=true    # true = обязательное обновление
VOICECHAT_CLIENT_RELEASE_NOTES_URL=https://github.com/Vodolazml/voicechat/releases/tag/v1.0.0

# Расположение файлов для скачивания
VOICECHAT_DOWNLOADS_DIR=/path/to/downloads

# Сеть (по умолчанию)
VOICECHAT_ALLOWED_HOSTS=127.0.0.1,localhost
VOICECHAT_CORS_ORIGINS=https://yourserver.com
```

### Пример .env файла:
```env
VOICECHAT_SECRET_KEY=abc123def456789012345678901234567890abcdef1234567890abcdef12
VOICECHAT_BOOTSTRAP_PASSWORD=Admin12345!Local
VOICECHAT_CLIENT_LATEST_VERSION=1.0.0
VOICECHAT_CLIENT_DOWNLOAD_URL=https://voicechat.example.com/downloads/PrivateVoiceChat-1.0.0-setup.exe
VOICECHAT_CLIENT_DOWNLOAD_SHA256=a1b2c3d4e5f6...
VOICECHAT_CLIENT_UPDATE_REQUIRED=false
VOICECHAT_DOWNLOADS_DIR=./downloads
```

## Настройка клиента

### client_config.json (расположить рядом с PrivateVoiceChat.exe):
```json
{
    "server_url": "https://voicechat.example.com",
    "lock_server_url": true,
    "master_password": "optional-master-key-for-settings-encryption"
}
```

## Процесс автообновления для пользователя

1. **Первая установка:**
   - Пользователь запускает `PrivateVoiceChat-1.0.0-setup.exe`
   - Выбирает папку установки
   - Отмечает опции (ярлык, автозапуск)
   - Программа устанавливается и запускается

2. **Последующие запуски:**
   - При входе проверяется `/client/update?version=1.0.0`
   - Если есть обновление → показывается диалог с 4 шагами:
     ```
     Шаг 1/4: Проверка обновлений... ✓
     Шаг 2/4: Скачивание обновления... ████████░░ 75%
     Шаг 3/4: Установка обновлений... 
     Шаг 4/4: Перезапуск...
     ```
   - После установки → автоматический перезапуск с новой версией
   - Если ошибка → откат на предыдущую версию + кнопка "Продолжить без обновления"

3. **Безопасность:**
   - Скачанный файл проверяется по SHA256
   - При несовпадении хэша → файл удаляется, ошибка
   - Бэкап текущей версии сохраняется в `~/.private_voicechat/backup/`

## Откат обновления

Если новая версия не работает:

### Автоматический (через 3 минуты):
- Клиент автоматически откатывается на бэкап
- Файлы из `~/.private_voicechat/updates/` удаляются

### Ручной:
```powershell
# Найти бэкап
ls "$env:USERPROFILE\.private_voicechat\backup\"

# Восстановить вручную
Expand-Archive -Path "backup-*.zip" -Destination "C:\Program Files\Private VoiceChat"
```

## CI/CD интеграция (GitHub Actions пример)

```yaml
# .github/workflows/release.yml
name: Release
on:
  push:
    tags: ['v*']

jobs:
  build:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      
      - name: Install dependencies
        run: |
          pip install pyinstaller
      
      - name: Build installer
        run: |
          cd build
          .\build.bat
      
      - name: Upload to server
        run: |
          scp dist/* user@server:/path/to/voicechat/downloads/
        env:
          SERVER_SSH_KEY: ${{ secrets.SERVER_SSH_KEY }}
```

## Диагностика проблем

### Клиент не видит обновление:
1. Проверить `VOICECHAT_CLIENT_DOWNLOAD_URL` на сервере
2. Проверить доступность `/client/update?version=X.X.X`
3. Проверить CORS если клиент в браузере

### Ошибка скачивания:
1. Проверить хэш файла на сервере: `sha256sum PrivateVoiceChat-*.zip`
2. Проверить размер файла (должен совпадать с content-length)

### Ошибка установки:
1. Проверить логи в `%TEMP%\pvchat_setup_*`
2. Проверить бэкап в `~/.private_voicechat/backup/`
3. Запустить установщик вручную без `/VERYSILENT` для отладки

## Обновление версий

```powershell
# 1. Изменить версию
# app/version.py: APP_VERSION = "1.0.1"

# 2. Собрать
cd build; .\build.bat

# 3. Загрузить на сервер
scp dist/* user@server:/path/to/voicechat/downloads/

# 4. Обновить конфигурацию сервера
VOICECHAT_CLIENT_LATEST_VERSION=1.0.1
VOICECHAT_CLIENT_DOWNLOAD_URL=https://.../PrivateVoiceChat-1.0.1-setup.exe
VOICECHAT_CLIENT_DOWNLOAD_SHA256=<новый хэш>