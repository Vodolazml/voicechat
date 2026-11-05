# VoiceChat 0.3.0

## Главное

- **Новый интерфейс.** Слева пространства и каналы, внутри голосовых каналов видно, кто там и кто говорит (зелёное кольцо). В звонке: сцена с плитками, чат канала справа, панель кнопок снизу.
- **Вышел значит вышел.** Выход, переключение канала, закрытие приложения или обрыв связи убирают вас из канала у всех сразу. Двойников больше нет.
- **Никаких окон поверх игры.** Ошибки и события показываются маленькими уведомлениями внутри приложения. Окно само никогда не выходит на передний план.
- **Звук.** 48 кГц, эхоподавление, шумоподавление на выбор (стандартное или сильное на нейросети), автоусиление. Тихих друзей приложение подтягивает само, а громкость любого можно поднять до 300% (правая кнопка по его плитке).
- **Демонстрация экрана** до 4K. Чужую трансляцию видно по значку LIVE; кнопка «Смотреть» включает видео. Двойной клик разворачивает, F11 на весь экран.
- **Текстовый чат** в каждом канале, в том числе прямо в голосовом. Ссылки кликабельны, @логин подсвечивает упоминание и присылает уведомление.
- **Оверлей** для игр: маленький список «кто в канале и кто говорит» в углу экрана. Включается в Настройки → Оверлей или Ctrl+Shift+O.

## Горячие клавиши (работают в звонке, даже в игре)

| Действие | По умолчанию |
| --- | --- |
| Рация (push-to-talk) | не назначена, выберите в Настройки → Голос |
| Микрофон вкл/выкл | Ctrl+Shift+M |
| Звук вкл/выкл | Ctrl+Shift+D |
| Оверлей | Ctrl+Shift+O |

Игра продолжает получать эти нажатия как обычно.

## Безопасность

- Голос и экран шифруются на ваших компьютерах (E2EE). Сервер передаёт зашифрованные пакеты и не может их прослушать.
- Нажмите «Сквозное шифрование» в шапке звонка и сверьте 20-значный код с другом голосом. Если ключ друга сменился, приложение предупредит.
- Сообщения чата хранятся на сервере зашифрованными (AES-256-GCM) и передаются только по HTTPS. Сквозное шифрование чата будет в следующей версии.
- Настройки → Аккаунт и устройства: где вы вошли, выход на чужом устройстве одной кнопкой.

## Если что-то не так

Настройки → О программе → «Сохранить отчёт». Получится zip на рабочем столе с журналом и настройками звука, без паролей и ключей.

## Известные ограничения

- Оверлей не виден в играх в эксклюзивном полноэкранном режиме. Включите в игре «Оконный без рамки» (Borderless).
- Звук игры в демонстрации экрана пока не передаётся.
- Старый интерфейс доступен один релиз: `PrivateVoiceChat.exe --classic`.

## Spaces for everyone, real screen-share numbers

- Anyone can create an account (`open_registration`, on by default). Joining a space needs that space's
  invite code (`ABCD-EFGH`), entered at sign-up or later with “+” in the left rail.
- Anyone can create their own space (up to `max_spaces_per_user`); its creator manages channels, the invite
  code and moderation inside it, and can delete it. Members can leave.
- The server owner (`admin`) sees every space and channel on the server; everybody else only theirs.
- Screen share captures with Desktop Duplication (`dxcam`, now a Windows dependency) instead of GDI, which
  topped out at 20–30 fps. Higher bitrates; the encoder keeps size and frame rate instead of shrinking.
- Tiles and the focused view show the measured resolution and frame rate: decoded frames for viewers, and
  for your own share what the encoder really sends, the capture rate, bitrate and any CPU or network limit.
- Join/leave chimes are louder, and your own “left the channel” chime is no longer cut off.
