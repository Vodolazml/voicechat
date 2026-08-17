from __future__ import annotations

import sys
from collections.abc import Callable
from functools import partial
from time import monotonic

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QPoint, QRectF, QSize, QTimer, Qt
from PySide6.QtGui import QAction, QColor, QCursor, QIcon, QPainter, QPen, QPixmap, QPolygon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QMenu,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from .api import ApiClient, ApiError
from .screen_share import STOP_FRAME, ScreenShareClient
from .settings_store import load_client_settings, save_client_settings
from .styles import APP_STYLE
from .voice_audio import AudioDevice, MicTestMonitor, VoiceAudioClient, audio_devices, device_display_name


SCREEN_FRAME_LIMIT_BYTES = 2_800_000
SCREEN_QUALITY_PRESETS = {
    "1080p_high": {
        "label": "FullHD высокое",
        "tooltip": "1920x1080, высокая четкость",
        "captures": ((1920, 1080, 86), (1920, 1080, 78), (1600, 900, 74)),
    },
    "1080p_speed": {
        "label": "FullHD скорость",
        "tooltip": "1920x1080, ниже JPEG quality ради частоты кадров",
        "captures": ((1920, 1080, 62), (1600, 900, 58), (1280, 720, 54)),
    },
    "1080p_balanced": {
        "label": "FullHD среднее",
        "tooltip": "1920x1080, меньше нагрузка на сеть",
        "captures": ((1920, 1080, 74), (1600, 900, 70), (1280, 720, 66)),
    },
    "720p_high": {
        "label": "HD 720p",
        "tooltip": "1280x720, стабильнее на слабой сети",
        "captures": ((1280, 720, 78), (1280, 720, 68), (960, 540, 64)),
    },
    "540p_low": {
        "label": "540p эконом",
        "tooltip": "960x540, минимальная нагрузка",
        "captures": ((960, 540, 72), (854, 480, 64)),
    },
}
DEFAULT_SCREEN_QUALITY = "1080p_high"
SCREEN_FPS_PRESETS = (
    ("2 FPS", 500),
    ("5 FPS", 200),
    ("10 FPS", 100),
    ("15 FPS", 67),
    ("30 FPS", 33),
    ("60 FPS", 17),
)
DEFAULT_SCREEN_FPS_INTERVAL_MS = 200
VIEWER_QUALITY_PRESETS = {
    "source": {"label": "Исходное", "size": None},
    "1080p": {"label": "FullHD 1080p", "size": (1920, 1080)},
    "720p": {"label": "HD 720p", "size": (1280, 720)},
    "540p": {"label": "540p", "size": (960, 540)},
}
DEFAULT_VIEWER_QUALITY = "source"
DEFAULT_VIEWER_FPS_INTERVAL_MS = 67


def svg_icon(name: str, color: str = "#dbdee1") -> QIcon:
    paths = {
        "mic": '<path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3Z"/><path d="M5 10v2a7 7 0 0 0 14 0v-2"/><path d="M12 19v3"/><path d="M8 22h8"/>',
        "mic_off": '<path d="m2 2 20 20"/><path d="M9 9v3a3 3 0 0 0 5 2.2"/><path d="M15 9.3V6a3 3 0 0 0-5.1-2.1"/><path d="M5 10v2a7 7 0 0 0 11.7 5.2"/><path d="M19 10v2a7 7 0 0 1-.7 3"/><path d="M12 19v3"/><path d="M8 22h8"/>',
        "headphones": '<path d="M3 14v-2a9 9 0 0 1 18 0v2"/><path d="M5 14h3v7H5a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2Z"/><path d="M16 14h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2h-3v-7Z"/>',
        "headphones_off": '<path d="m2 2 20 20"/><path d="M3 14v-2a9 9 0 0 1 13.2-8"/><path d="M20.6 13.2A9 9 0 0 0 19 8.5"/><path d="M5 14h3v7H5a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2Z"/><path d="M16 14h3a2 2 0 0 1 2 2v3"/>',
        "voice": '<path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3Z"/><path d="M5 10v2a7 7 0 0 0 14 0v-2"/><path d="M12 19v3"/><path d="M8 22h8"/>',
        "settings": '<path d="M12 15.5A3.5 3.5 0 1 0 12 8a3.5 3.5 0 0 0 0 7.5Z"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1A2 2 0 1 1 4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.6-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.3 7A2 2 0 1 1 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3h.1a1.7 1.7 0 0 0 1-1.6V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.6h.1a1.7 1.7 0 0 0 1.9-.3l.1-.1A2 2 0 1 1 19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9v.1a1.7 1.7 0 0 0 1.6 1h.1a2 2 0 1 1 0 4H21a1.7 1.7 0 0 0-1.6 1Z"/>',
        "screen": '<path d="M3 5h18v12H3z"/><path d="M8 21h8"/><path d="M12 17v4"/>',
        "maximize": '<path d="M8 3H5a2 2 0 0 0-2 2v3"/><path d="M21 8V5a2 2 0 0 0-2-2h-3"/><path d="M3 16v3a2 2 0 0 0 2 2h3"/><path d="M16 21h3a2 2 0 0 0 2-2v-3"/>',
        "plus": '<path d="M12 5v14"/><path d="M5 12h14"/>',
        "refresh": '<path d="M21 12a9 9 0 0 1-15.5 6.2"/><path d="M3 12A9 9 0 0 1 18.5 5.8"/><path d="M18 3v5h-5"/><path d="M6 21v-5h5"/>',
        "join": '<path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/><path d="M10 17l5-5-5-5"/><path d="M15 12H3"/>',
        "leave": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5"/><path d="M21 12H9"/>',
    }
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{paths[name]}</svg>'
    pixmap = QPixmap()
    pixmap.loadFromData(svg.encode("utf-8"), "SVG")
    return QIcon(pixmap)


def icon_button(icon: str, tooltip: str, *, danger: bool = False) -> QToolButton:
    button = QToolButton()
    button.setIcon(svg_icon(icon, "#ffffff" if danger else "#dbdee1"))
    button.setIconSize(QSize(22, 22))
    button.setToolTip(tooltip)
    button.setObjectName("iconDanger" if danger else "iconButton")
    button.setCursor(Qt.PointingHandCursor)
    return button


class LoginDialog(QDialog):
    def __init__(self, api: ApiClient) -> None:
        super().__init__()
        self.api = api
        self.setWindowTitle("Вход")
        self.setMinimumWidth(380)
        self.username = QLineEdit("admin")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("Пароль")
        self.server = QLineEdit(api.base_url)
        self.error = QLabel("")
        self.error.setObjectName("warn")

        form = QFormLayout()
        form.addRow("Сервер", self.server)
        form.addRow("Логин", self.username)
        form.addRow("Пароль", self.password)

        self.login_button = QPushButton("Войти")
        self.login_button.clicked.connect(self.try_login)

        layout = QVBoxLayout(self)
        title = QLabel("Private VoiceChat")
        title.setObjectName("title")
        layout.addWidget(title)
        layout.addLayout(form)
        layout.addWidget(self.error)
        layout.addWidget(self.login_button)

    def try_login(self) -> None:
        self.api.base_url = self.server.text().strip() or self.api.base_url
        try:
            data = self.api.login(self.username.text().strip(), self.password.text())
            if data.get("must_change_password"):
                dialog = PasswordDialog(self.api, self.password.text())
                if dialog.exec() != QDialog.Accepted:
                    self.error.setText("Перед работой нужно сменить временный пароль.")
                    return
            self.accept()
        except ApiError as exc:
            self.error.setText(str(exc))


class PasswordDialog(QDialog):
    def __init__(self, api: ApiClient, old_password: str) -> None:
        super().__init__()
        self.api = api
        self.old_password = old_password
        self.setWindowTitle("Смена временного пароля")
        self.setMinimumWidth(380)
        self.new_password = QLineEdit()
        self.new_password.setEchoMode(QLineEdit.Password)
        self.repeat_password = QLineEdit()
        self.repeat_password.setEchoMode(QLineEdit.Password)
        self.error = QLabel("")
        self.error.setObjectName("warn")

        form = QFormLayout()
        form.addRow("Новый пароль", self.new_password)
        form.addRow("Повтор", self.repeat_password)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Задайте постоянный пароль"))
        layout.addLayout(form)
        layout.addWidget(self.error)
        layout.addWidget(buttons)

    def save(self) -> None:
        if self.new_password.text() != self.repeat_password.text():
            self.error.setText("Пароли не совпадают.")
            return
        try:
            self.api.change_password(self.old_password, self.new_password.text())
            self.accept()
        except ApiError as exc:
            self.error.setText(str(exc))


class ChannelRow(QFrame):
    def __init__(self, channel: dict, select_callback, activate_callback) -> None:
        super().__init__()
        self.channel = channel
        self.select_callback = select_callback
        self.activate_callback = activate_callback
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.select_callback(self.channel)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.channel["type"] == "voice":
            self.select_callback(self.channel)
            self.activate_callback(self.channel)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, api: ApiClient) -> None:
        super().__init__()
        self.api = api
        self.me: dict = {}
        self.spaces: list[dict] = []
        self.channels: list[dict] = []
        self.voice_cache: dict[int, list[dict]] = {}
        self.current_space: dict | None = None
        self.current_channel: dict | None = None
        self.connected_channel_id: int | None = None
        self.muted = False
        self.deafened = False
        self.local_mutes: set[int] = set()
        self.local_volumes: dict[int, int] = {}
        self.voice_audio: VoiceAudioClient | None = None
        self.screen_client: ScreenShareClient | None = None
        self.screen_viewer: ScreenShareViewer | None = None
        self.screen_sharing = False
        self.client_settings = load_client_settings()
        self.screen_quality_key = self.valid_screen_quality_key(self.client_settings.get("screen_quality_key"))
        self.screen_fps_interval_ms = self.valid_fps_interval(self.client_settings.get("screen_fps_interval_ms"), DEFAULT_SCREEN_FPS_INTERVAL_MS)
        self.viewer_quality_key = self.valid_viewer_quality_key(self.client_settings.get("viewer_quality_key"))
        self.viewer_fps_interval_ms = self.valid_fps_interval(self.client_settings.get("viewer_fps_interval_ms"), DEFAULT_VIEWER_FPS_INTERVAL_MS)
        self.screen_frame_times: list[float] = []
        self.screen_stream_info = ""
        self.screen_frames: dict[int, QPixmap] = {}
        self.last_screen_stage_update_at = 0.0
        self.current_voice_states: list[dict] = []
        self.audio_status = "audio idle"
        self.input_device_id: int | None = None
        self.output_device_id: int | None = None
        self.noise_suppression = True
        self.noise_threshold = 450
        self.last_speaking = False
        self.last_voice_sync_at = 0.0

        self.setWindowTitle("Private VoiceChat")
        self.resize(1180, 720)
        self.setMinimumSize(900, 560)
        self.setCentralWidget(self.build_ui())
        self.update_audio_device_label()

        self.timer = QTimer(self)
        self.timer.setInterval(2500)
        self.timer.timeout.connect(self.refresh_voice)
        self.timer.start()
        self.ping_timer = QTimer(self)
        self.ping_timer.setInterval(3000)
        self.ping_timer.timeout.connect(self.refresh_ping)
        self.ping_timer.start()
        self.speaking_timer = QTimer(self)
        self.speaking_timer.setInterval(250)
        self.speaking_timer.timeout.connect(self.sync_speaking_state)
        self.speaking_timer.start()
        self.screen_timer = QTimer(self)
        self.screen_timer.setTimerType(Qt.PreciseTimer)
        self.screen_timer.setInterval(self.screen_fps_interval_ms)
        self.screen_timer.timeout.connect(self.capture_screen_frame)

        self.reload_all()
        self.refresh_ping()

    def build_ui(self) -> QWidget:
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.space_list = QListWidget()
        self.space_list.setFixedWidth(126)
        self.space_list.itemClicked.connect(self.select_space_item)
        space_frame = QFrame()
        space_frame.setObjectName("sidebar")
        space_layout = QVBoxLayout(space_frame)
        space_layout.setContentsMargins(8, 10, 8, 10)
        space_layout.setSpacing(8)
        server_title = QLabel("Серверы")
        server_title.setObjectName("serverTitle")
        add_space = QPushButton("Новый")
        add_space.setObjectName("serverAdd")
        add_space.setToolTip("Создать сервер")
        add_space.clicked.connect(self.create_space)
        space_layout.addWidget(server_title)
        space_layout.addWidget(self.space_list)
        space_layout.addWidget(add_space)

        self.channel_list = QListWidget()
        self.channel_list.itemClicked.connect(self.select_channel_item)
        self.channel_list.itemDoubleClicked.connect(self.activate_channel_item)
        channel_frame = QFrame()
        channel_frame.setObjectName("panel")
        channel_frame.setFixedWidth(300)
        channel_layout = QVBoxLayout(channel_frame)
        channel_layout.setContentsMargins(10, 12, 10, 10)
        channel_layout.setSpacing(8)
        self.space_title = QLabel("Пространство")
        self.space_title.setObjectName("title")
        add_channel = QPushButton("Новый канал")
        add_channel.setObjectName("secondary")
        add_channel.clicked.connect(self.create_channel)
        add_member = QPushButton("Добавить участника")
        add_member.setObjectName("secondary")
        add_member.clicked.connect(self.add_member_to_channel)
        channel_layout.addWidget(self.space_title)
        channel_layout.addWidget(self.channel_list)
        channel_layout.addWidget(add_channel)
        channel_layout.addWidget(add_member)

        center = QFrame()
        center.setObjectName("mainArea")
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(24, 18, 24, 0)
        center_layout.setSpacing(12)
        self.channel_title = QLabel("Выберите канал")
        self.channel_title.setObjectName("title")
        self.channel_status = QLabel("Готово к работе")
        self.channel_status.setObjectName("muted")
        self.voice_badge = QLabel("НЕ ПОДКЛЮЧЕНО")
        self.voice_badge.setObjectName("voiceBadge")
        self.voice_badge.setVisible(False)
        self.connect_button = QPushButton("Подключиться")
        self.connect_button.clicked.connect(self.toggle_connection)
        self.connect_button.setEnabled(False)
        self.stage = QFrame()
        self.stage.setObjectName("voiceStage")
        stage_layout = QVBoxLayout(self.stage)
        stage_layout.setContentsMargins(28, 26, 28, 26)
        stage_layout.setSpacing(18)
        self.stage_eyebrow = QLabel("ГОЛОСОВОЙ КАНАЛ")
        self.stage_eyebrow.setObjectName("section")
        self.stage_title = QLabel("Выберите канал")
        self.stage_title.setObjectName("stageTitle")
        self.stage_subtitle = QLabel("Здесь появятся участники после подключения к голосовому каналу.")
        self.stage_subtitle.setObjectName("muted")
        self.stage_subtitle.setWordWrap(True)
        stage_layout.addWidget(self.stage_eyebrow)
        stage_layout.addWidget(self.stage_title)
        stage_layout.addWidget(self.stage_subtitle)
        self.stage_scroll = QScrollArea()
        self.stage_scroll.setWidgetResizable(True)
        self.stage_scroll.setFrameShape(QFrame.NoFrame)
        self.stage_body = QWidget()
        self.stage_members_layout = QGridLayout(self.stage_body)
        self.stage_members_layout.setContentsMargins(0, 0, 0, 0)
        self.stage_members_layout.setHorizontalSpacing(12)
        self.stage_members_layout.setVerticalSpacing(12)
        self.stage_scroll.setWidget(self.stage_body)
        stage_layout.addWidget(self.stage_scroll, 1)
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(self.channel_title)
        top.addWidget(self.voice_badge)
        top.addStretch()
        top.addWidget(self.connect_button)
        center_layout.addLayout(top)
        center_layout.addWidget(self.channel_status)
        center_layout.addWidget(self.stage, 1)
        center_layout.addWidget(self.bottom_bar())

        member_frame = QFrame()
        member_frame.setObjectName("rightPanel")
        member_frame.setFixedWidth(330)
        member_layout = QVBoxLayout(member_frame)
        member_layout.setContentsMargins(12, 14, 12, 12)
        member_layout.setSpacing(10)
        member_title = QLabel("Участники")
        member_title.setObjectName("title")
        self.member_list = QListWidget()
        self.member_list.setSpacing(4)
        member_layout.addWidget(member_title)
        member_layout.addWidget(self.member_list)

        layout.addWidget(space_frame)
        layout.addWidget(channel_frame)
        layout.addWidget(center, 1)
        layout.addWidget(member_frame)
        return root

    def bottom_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("bottomBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 10, 10, 10)
        layout.setSpacing(10)
        self.user_label = QLabel("Пользователь")
        self.user_label.setObjectName("ok")
        self.device_label = QLabel("Системные аудиоустройства")
        self.device_label.setObjectName("deviceLabel")
        self.device_label.setToolTip("Выбранные микрофон и устройство вывода")
        self.mute_button = icon_button("mic", "Выключить микрофон")
        self.mute_button.clicked.connect(self.toggle_mute)
        self.deafen_button = icon_button("headphones", "Отключить входящий звук")
        self.deafen_button.clicked.connect(self.toggle_deafen)
        self.screen_button = icon_button("screen", "Включить демонстрацию экрана")
        self.screen_button.clicked.connect(self.toggle_screen_share)
        self.ping_label = QLabel("ping --")
        self.ping_label.setObjectName("pingUnknown")
        self.ping_label.setToolTip("Задержка API до сервера")
        self.settings_button = icon_button("settings", "Настройки")
        self.settings_button.clicked.connect(self.open_audio_settings)
        create_user = QPushButton("Создать пользователя")
        create_user.setObjectName("secondary")
        create_user.clicked.connect(self.create_user)
        refresh = icon_button("refresh", "Обновить")
        refresh.clicked.connect(self.reload_all)
        layout.addWidget(self.user_label)
        layout.addWidget(self.device_label)
        layout.addStretch()
        layout.addWidget(self.mute_button)
        layout.addWidget(self.deafen_button)
        layout.addWidget(self.screen_button)
        layout.addWidget(self.ping_label)
        layout.addWidget(self.settings_button)
        layout.addWidget(create_user)
        layout.addWidget(refresh)
        return bar

    def reload_all(self) -> None:
        try:
            self.me = self.api.me()
            self.user_label.setText(f"{self.me['display_name']}  @{self.me['username']}")
            self.spaces = self.api.spaces()
            self.space_list.clear()
            for space in self.spaces:
                item = QListWidgetItem()
                item.setSizeHint(QSize(108, 64))
                item.setToolTip(space["name"])
                item.setData(Qt.UserRole, space)
                self.space_list.addItem(item)
                self.space_list.setItemWidget(item, self.server_widget(space, selected=self.current_space and self.current_space.get("id") == space["id"]))
            if self.spaces and not self.current_space:
                self.space_list.setCurrentRow(0)
                self.select_space(self.spaces[0])
        except ApiError as exc:
            self.show_error(str(exc))

    def refresh_ping(self) -> None:
        try:
            ping = self.api.ping_ms()
        except ApiError:
            self.ping_label.setText("offline")
            self.ping_label.setObjectName("pingBad")
            self.ping_label.setToolTip("Сервер недоступен или сеть оборвалась")
            self.repolish(self.ping_label)
            return
        if ping < 80:
            quality = "pingGood"
            text = f"{ping} ms"
            tip = "API-соединение стабильное"
        elif ping < 180:
            quality = "pingWarn"
            text = f"{ping} ms"
            tip = "Есть небольшая задержка API"
        else:
            quality = "pingBad"
            text = f"{ping} ms"
            tip = "Высокая задержка API или проблемы сети"
        self.ping_label.setText(text)
        self.ping_label.setObjectName(quality)
        self.ping_label.setToolTip(tip)
        self.repolish(self.ping_label)

    def repolish(self, widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def valid_screen_quality_key(self, value) -> str:
        return str(value) if value in SCREEN_QUALITY_PRESETS else DEFAULT_SCREEN_QUALITY

    def valid_viewer_quality_key(self, value) -> str:
        return str(value) if value in VIEWER_QUALITY_PRESETS else DEFAULT_VIEWER_QUALITY

    def valid_fps_interval(self, value, default: int) -> int:
        allowed = {interval for _label, interval in SCREEN_FPS_PRESETS}
        return int(value) if isinstance(value, int) and value in allowed else default

    def save_preferences(self) -> None:
        self.client_settings.update(
            {
                "screen_quality_key": self.screen_quality_key,
                "screen_fps_interval_ms": self.screen_fps_interval_ms,
                "viewer_quality_key": self.viewer_quality_key,
                "viewer_fps_interval_ms": self.viewer_fps_interval_ms,
            }
        )
        save_client_settings(self.client_settings)

    def select_space_item(self, item: QListWidgetItem) -> None:
        self.select_space(item.data(Qt.UserRole))

    def select_space(self, space: dict) -> None:
        self.current_space = space
        self.space_title.setText(space["name"])
        self.render_servers()
        try:
            self.channels = self.api.channels(space["id"])
            self.refresh_space_voice_cache()
            self.render_channels()
            if self.channels:
                preferred = next((c for c in self.channels if c["id"] == self.connected_channel_id), None)
                preferred = preferred or next((c for c in self.channels if c["type"] == "voice"), self.channels[0])
                self.select_channel(preferred)
        except ApiError as exc:
            self.show_error(str(exc))

    def select_channel_item(self, item: QListWidgetItem) -> None:
        channel = item.data(Qt.UserRole)
        if channel:
            self.select_channel(channel)

    def activate_channel_item(self, item: QListWidgetItem) -> None:
        channel = item.data(Qt.UserRole)
        if not channel:
            return
        self.select_channel(channel)
        if channel["type"] == "voice":
            self.toggle_connection()

    def select_channel(self, channel: dict) -> None:
        self.current_channel = channel
        kind = "голосовой канал" if channel["type"] == "voice" else "текстовый канал"
        self.channel_title.setText(channel["name"])
        connected_here = self.connected_channel_id == channel["id"]
        if connected_here:
            self.channel_status.setText(f"Вы подключены к каналу «{channel['name']}».")
        else:
            self.channel_status.setText(kind)
        self.connect_button.setEnabled(channel["type"] == "voice")
        self.connect_button.setText("Отключиться" if connected_here else "Подключиться")
        self.connect_button.setObjectName("voiceConnected" if connected_here else "")
        self.connect_button.style().unpolish(self.connect_button)
        self.connect_button.style().polish(self.connect_button)
        self.voice_badge.setVisible(connected_here)
        self.voice_badge.setText("В ГОЛОСЕ" if connected_here else "НЕ ПОДКЛЮЧЕНО")
        self.stage_eyebrow.setText("ГОЛОСОВОЙ КАНАЛ" if channel["type"] == "voice" else "ТЕКСТОВЫЙ КАНАЛ")
        self.stage_title.setText(channel["name"])
        self.stage_subtitle.setText(
            "Вы подключены. Микрофон и входящий звук работают через защищенный канал сервера."
            if connected_here
            else "Нажмите «Подключиться», чтобы войти в канал и увидеть себя среди участников."
            if channel["type"] == "voice"
            else "Текстовый чат будет реализован следующим этапом. Модель доступа уже готова."
        )
        self.render_channels()
        self.refresh_voice()

    def toggle_connection(self) -> None:
        if not self.current_channel:
            return
        try:
            channel_id = self.current_channel["id"]
            if self.connected_channel_id == channel_id:
                self.stop_screen_share()
                self.stop_screen_client()
                self.stop_audio()
                self.api.disconnect(channel_id)
                self.connected_channel_id = None
                self.channel_status.setText("Отключено")
            else:
                self.stop_screen_share()
                self.stop_screen_client()
                self.stop_audio()
                state = self.api.connect(channel_id, self.muted, self.deafened)
                self.connected_channel_id = state["channel_id"]
                self.start_audio(channel_id)
                self.start_screen_client(channel_id)
                self.channel_status.setText("Подключено")
            self.refresh_space_voice_cache()
            self.select_channel(self.current_channel)
        except ApiError as exc:
            self.show_error(str(exc))
        except RuntimeError as exc:
            if self.connected_channel_id:
                try:
                    self.api.disconnect(self.connected_channel_id)
                except ApiError:
                    pass
                self.connected_channel_id = None
            self.show_error(str(exc))

    def start_audio(self, channel_id: int) -> None:
        try:
            self.voice_audio = VoiceAudioClient(
                ws_url=self.api.voice_ws_url(channel_id),
                is_muted=lambda: self.muted,
                is_deafened=lambda: self.deafened,
                is_locally_muted=lambda user_id: user_id in self.local_mutes,
                local_volume=lambda user_id: self.local_volumes.get(user_id, 100),
                noise_suppression=lambda: self.noise_suppression,
                noise_threshold=lambda: self.noise_threshold,
                status_callback=self.set_audio_status,
                input_device=self.input_device_id,
                output_device=self.output_device_id,
            )
            self.voice_audio.start()
        except Exception as exc:
            self.voice_audio = None
            raise RuntimeError(f"Не удалось запустить голос: {exc}") from exc

    def stop_audio(self) -> None:
        if self.voice_audio:
            self.voice_audio.stop()
            self.voice_audio = None
        self.last_speaking = False

    def start_screen_client(self, channel_id: int) -> None:
        self.screen_client = ScreenShareClient(self.api.screen_ws_url(channel_id))
        self.screen_client.frame_received.connect(self.on_screen_frame)
        self.screen_client.stopped_received.connect(self.on_screen_stop)
        self.screen_client.status_changed.connect(self.set_audio_status)
        self.screen_client.start()
        self.send_viewer_preferences_to_server()

    def stop_screen_client(self) -> None:
        if self.screen_client:
            self.screen_client.stop()
            self.screen_client = None
        self.screen_frames.clear()
        if self.screen_viewer:
            self.screen_viewer.close()
            self.screen_viewer = None
        self.redraw_voice_stage()

    def toggle_screen_share(self) -> None:
        if self.screen_sharing:
            answer = QMessageBox.question(
                self,
                "Остановить трансляцию",
                "Вы действительно хотите остановить демонстрацию экрана?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            self.stop_screen_share()
            return
        if not self.connected_channel_id:
            self.show_error("Сначала подключитесь к голосовому каналу.")
            return
        dialog = ScreenShareStartDialog(self, self.screen_quality_key, self.screen_fps_interval_ms)
        if dialog.exec() != QDialog.Accepted:
            return
        self.screen_quality_key = dialog.selected_quality_key()
        self.screen_fps_interval_ms = dialog.selected_fps_interval_ms()
        self.save_preferences()
        if not self.screen_client:
            self.start_screen_client(self.connected_channel_id)
        self.screen_sharing = True
        self.screen_frame_times.clear()
        self.screen_stream_info = ""
        self.screen_timer.setInterval(self.screen_fps_interval_ms)
        self.screen_timer.start()
        self.apply_screen_button()
        self.capture_screen_frame()

    def stop_screen_share(self) -> None:
        was_sharing = self.screen_sharing
        self.screen_sharing = False
        self.screen_stream_info = ""
        self.screen_frame_times.clear()
        self.screen_timer.stop()
        if self.screen_client and was_sharing:
            self.screen_client.send_frame(STOP_FRAME)
        if self.me:
            self.screen_frames.pop(int(self.me["id"]), None)
        self.apply_screen_button()
        if self.current_channel and self.connected_channel_id == self.current_channel["id"]:
            self.channel_status.setText(f"Вы подключены к каналу «{self.current_channel['name']}».")
        self.refresh_screen_preview_widgets(force=True)

    def capture_screen_frame(self) -> None:
        if not self.screen_sharing or not self.screen_client:
            return
        screen = QApplication.primaryScreen()
        if not screen:
            return
        source = screen.grabWindow(0)
        pixmap = source
        frame = b""
        preset = SCREEN_QUALITY_PRESETS.get(self.screen_quality_key, SCREEN_QUALITY_PRESETS[DEFAULT_SCREEN_QUALITY])
        capture_presets = preset["captures"]
        used_quality = 0
        for width, height, quality in capture_presets:
            pixmap = self.prepare_screen_pixmap(source, width, height)
            self.paint_cursor_on_frame(pixmap, source, screen)
            frame = self.encode_screen_frame(pixmap, quality)
            if frame and len(frame) <= SCREEN_FRAME_LIMIT_BYTES:
                used_quality = quality
                break
        if not frame or len(frame) > SCREEN_FRAME_LIMIT_BYTES:
            return
        if self.me:
            self.screen_frames[int(self.me["id"])] = pixmap
        self.screen_client.send_frame(frame)
        self.update_screen_stream_info(pixmap, len(frame), used_quality)
        self.refresh_screen_preview_widgets()
        self.update_screen_status_label()
        if self.screen_viewer:
            self.screen_viewer.mark_dirty()

    def prepare_screen_pixmap(self, source: QPixmap, width: int, height: int) -> QPixmap:
        if source.width() <= width and source.height() <= height:
            return source.copy()
        mode = Qt.FastTransformation if self.screen_fps_interval_ms <= 33 else Qt.SmoothTransformation
        return source.scaled(width, height, Qt.KeepAspectRatio, mode)

    def encode_screen_frame(self, pixmap: QPixmap, quality: int) -> bytes:
        data = QByteArray()
        buffer = QBuffer(data)
        if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
            return b""
        pixmap.save(buffer, "JPG", quality)
        return bytes(data)

    def paint_cursor_on_frame(self, pixmap: QPixmap, source: QPixmap, screen) -> None:
        cursor_pos = QCursor.pos()
        geometry = screen.geometry()
        if not geometry.contains(cursor_pos):
            return
        rel_x = cursor_pos.x() - geometry.x()
        rel_y = cursor_pos.y() - geometry.y()
        if geometry.width() <= 0 or geometry.height() <= 0 or source.width() <= 0 or source.height() <= 0:
            return
        source_x = rel_x * source.width() / geometry.width()
        source_y = rel_y * source.height() / geometry.height()
        x = int(source_x * pixmap.width() / source.width())
        y = int(source_y * pixmap.height() / source.height())
        scale = max(0.8, min(1.8, pixmap.width() / 1280))
        points = QPolygon(
            [
                QPoint(x, y),
                QPoint(x + int(18 * scale), y + int(8 * scale)),
                QPoint(x + int(10 * scale), y + int(11 * scale)),
                QPoint(x + int(15 * scale), y + int(23 * scale)),
                QPoint(x + int(10 * scale), y + int(25 * scale)),
                QPoint(x + int(5 * scale), y + int(13 * scale)),
                QPoint(x, y + int(19 * scale)),
            ]
        )
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#111214"), max(2, int(3 * scale))))
        painter.setBrush(QColor("#f2f3f5"))
        painter.drawPolygon(points)
        painter.end()

    def update_screen_stream_info(self, pixmap: QPixmap, frame_size: int, jpeg_quality: int) -> None:
        now = monotonic()
        self.screen_frame_times = [stamp for stamp in self.screen_frame_times if now - stamp <= 1.2]
        self.screen_frame_times.append(now)
        actual_fps = len(self.screen_frame_times) / max(0.1, self.screen_frame_times[-1] - self.screen_frame_times[0] or 1)
        target_fps = max(1, round(1000 / self.screen_fps_interval_ms))
        preset = SCREEN_QUALITY_PRESETS.get(self.screen_quality_key, SCREEN_QUALITY_PRESETS[DEFAULT_SCREEN_QUALITY])
        bitrate_mbps = frame_size * 8 * actual_fps / 1_000_000
        load_note = " · упор: захват/JPEG" if actual_fps < target_fps * 0.75 else ""
        limit_note = " · близко к лимиту кадра" if frame_size > SCREEN_FRAME_LIMIT_BYTES * 0.9 else ""
        self.screen_stream_info = (
            f"{preset['label']} · {pixmap.width()}x{pixmap.height()} · "
            f"{target_fps} FPS выбрано · {actual_fps:.1f} FPS факт · "
            f"JPEG {jpeg_quality} · {frame_size // 1024} KB · ~{bitrate_mbps:.1f} Мбит/с{limit_note}{load_note}"
        )

    def on_screen_frame(self, user_id: int, frame: bytes) -> None:
        pixmap = QPixmap()
        if pixmap.loadFromData(frame, "JPG"):
            self.screen_frames[user_id] = pixmap
            self.refresh_screen_preview_widgets()
            if self.screen_viewer:
                self.screen_viewer.mark_dirty()

    def on_screen_stop(self, user_id: int) -> None:
        self.screen_frames.pop(user_id, None)
        self.refresh_screen_preview_widgets(force=True)
        if self.screen_viewer:
            self.screen_viewer.mark_dirty()

    def apply_screen_button(self) -> None:
        self.screen_button.setToolTip("Остановить демонстрацию экрана" if self.screen_sharing else "Включить демонстрацию экрана")
        self.screen_button.setObjectName("iconActive" if self.screen_sharing else "iconButton")
        self.repolish(self.screen_button)

    def update_screen_status_label(self) -> None:
        if self.screen_sharing and self.screen_stream_info and self.current_channel and self.connected_channel_id == self.current_channel["id"]:
            self.channel_status.setText(f"Трансляция: {self.screen_stream_info}")

    def refresh_screen_preview_widgets(self, *, force: bool = False) -> None:
        now = monotonic()
        if force or now - self.last_screen_stage_update_at >= 0.5:
            self.last_screen_stage_update_at = now
            self.redraw_voice_stage()

    def open_screen_viewer(self, user_id: int) -> None:
        if user_id not in self.screen_frames:
            return
        if self.screen_viewer:
            self.screen_viewer.close()
        self.screen_viewer = ScreenShareViewer(
            self,
            user_id,
            lambda: list(self.current_voice_states),
            lambda: dict(self.screen_frames),
            lambda selected_user_id: self.screen_stream_info if self.me and selected_user_id == int(self.me["id"]) else "",
            self.viewer_quality_key,
            self.viewer_fps_interval_ms,
            self.apply_viewer_preferences,
            self.initials,
            self.state_text,
        )
        self.screen_viewer.finished.connect(lambda _result: setattr(self, "screen_viewer", None))
        self.screen_viewer.show_fullscreen()

    def apply_viewer_preferences(self, quality_key: str, fps_interval_ms: int) -> None:
        self.viewer_quality_key = self.valid_viewer_quality_key(quality_key)
        self.viewer_fps_interval_ms = self.valid_fps_interval(fps_interval_ms, DEFAULT_VIEWER_FPS_INTERVAL_MS)
        self.save_preferences()
        self.send_viewer_preferences_to_server()

    def send_viewer_preferences_to_server(self) -> None:
        if self.screen_client:
            self.screen_client.send_viewer_settings(self.viewer_fps_interval_ms, self.viewer_quality_key)

    def set_audio_status(self, text: str) -> None:
        self.audio_status = text

    def sync_speaking_state(self) -> None:
        if not self.connected_channel_id:
            return
        now = monotonic()
        speaking = bool(self.voice_audio and self.voice_audio.speaking and not self.muted and not self.deafened)
        needs_heartbeat = now - self.last_voice_sync_at >= 5
        if speaking == self.last_speaking and not needs_heartbeat:
            return
        self.last_speaking = speaking
        self.last_voice_sync_at = now
        try:
            self.api.update_voice(self.connected_channel_id, self.muted, self.deafened, speaking=speaking)
            if self.current_channel and self.current_channel["id"] == self.connected_channel_id:
                self.refresh_voice()
        except ApiError:
            pass

    def open_audio_settings(self) -> None:
        dialog = AudioSettingsDialog(
            self,
            self.input_device_id,
            self.output_device_id,
            self.noise_suppression,
            self.noise_threshold,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        self.input_device_id, self.output_device_id = dialog.selected_devices()
        self.noise_suppression = dialog.noise_suppression_enabled()
        self.noise_threshold = dialog.selected_threshold()
        self.update_audio_device_label()
        if self.connected_channel_id:
            channel_id = self.connected_channel_id
            self.stop_audio()
            try:
                self.start_audio(channel_id)
            except RuntimeError as exc:
                self.show_error(str(exc))

    def update_audio_device_label(self) -> None:
        input_name = device_display_name(self.input_device_id) if self.input_device_id is not None else "системный микрофон"
        output_name = device_display_name(self.output_device_id) if self.output_device_id is not None else "системный вывод"
        short_input = input_name.replace("Микрофон ", "").replace("Динамики ", "")
        short_output = output_name.replace("Микрофон ", "").replace("Динамики ", "")
        self.device_label.setText(f"{short_input} -> {short_output}")
        self.device_label.setToolTip(
            f"Микрофон: {input_name}\nВывод: {output_name}\nЕсли написано «микрофон выключен», это кнопка mute, а не выбранное устройство."
        )

    def toggle_mute(self) -> None:
        self.muted = not self.muted
        self.apply_audio_buttons()
        self.update_voice_flags()

    def toggle_deafen(self) -> None:
        self.deafened = not self.deafened
        if self.deafened and not self.muted:
            self.muted = True
        self.apply_audio_buttons()
        self.update_voice_flags()

    def apply_audio_buttons(self) -> None:
        self.mute_button.setIcon(svg_icon("mic_off" if self.muted else "mic"))
        self.mute_button.setToolTip("Включить микрофон" if self.muted else "Выключить микрофон")
        self.mute_button.setObjectName("iconDanger" if self.muted else "iconButton")
        self.mute_button.style().unpolish(self.mute_button)
        self.mute_button.style().polish(self.mute_button)
        self.deafen_button.setIcon(svg_icon("headphones_off" if self.deafened else "headphones"))
        self.deafen_button.setToolTip("Включить звук" if self.deafened else "Отключить входящий звук")
        self.deafen_button.setObjectName("iconDanger" if self.deafened else "iconButton")
        self.deafen_button.style().unpolish(self.deafen_button)
        self.deafen_button.style().polish(self.deafen_button)

    def update_voice_flags(self) -> None:
        if self.connected_channel_id:
            try:
                self.api.update_voice(self.connected_channel_id, self.muted, self.deafened)
                self.refresh_space_voice_cache()
                self.render_channels()
                self.refresh_voice()
            except ApiError as exc:
                self.show_error(str(exc))

    def refresh_voice(self) -> None:
        self.member_list.clear()
        self.clear_stage_members()
        if not self.current_channel or self.current_channel["type"] != "voice":
            self.current_voice_states = []
            self.add_stage_empty("Для текстовых каналов скоро появится полноценный чат.")
            return
        try:
            states = self.api.voice_states(self.current_channel["id"])
            self.current_voice_states = states
            self.voice_cache[self.current_channel["id"]] = states
            if not states:
                item = QListWidgetItem()
                item.setSizeHint(QSize(220, 46))
                self.member_list.addItem(item)
                self.member_list.setItemWidget(item, self.member_widget("Пока никого нет", "Ожидание подключения", muted=True))
                self.add_stage_empty("В канале пока никого нет. Подключитесь первым.")
                return
            for state in states:
                item = QListWidgetItem()
                item.setSizeHint(QSize(290, 64))
                self.member_list.addItem(item)
                self.member_list.setItemWidget(item, self.voice_member_widget(state, compact=False))
                self.add_stage_member(state)
            self.render_channels()
        except ApiError:
            self.current_voice_states = []
            if self.current_channel and self.current_channel["type"] == "voice":
                self.member_list.addItem("Не удалось обновить участников")
                self.add_stage_empty("Не удалось обновить участников. Проверьте соединение с сервером.")

    def redraw_voice_stage(self) -> None:
        if not hasattr(self, "stage_members_layout"):
            return
        self.clear_stage_members()
        if not self.current_channel or self.current_channel["type"] != "voice":
            return
        if not self.current_voice_states:
            self.add_stage_empty("В канале пока никого нет. Подключитесь первым.")
            return
        for state in self.current_voice_states:
            self.add_stage_member(state)

    def refresh_space_voice_cache(self) -> None:
        self.voice_cache = {}
        for channel in self.channels:
            if channel["type"] != "voice":
                continue
            try:
                self.voice_cache[channel["id"]] = self.api.voice_states(channel["id"])
            except ApiError:
                self.voice_cache[channel["id"]] = []

    def render_channels(self) -> None:
        self.channel_list.clear()
        voice_channels = [c for c in self.channels if c["type"] == "voice"]
        text_channels = [c for c in self.channels if c["type"] == "text"]
        if voice_channels:
            self.add_section("ГОЛОСОВЫЕ КАНАЛЫ")
            for channel in voice_channels:
                self.add_channel_row(channel)
                for state in self.voice_cache.get(channel["id"], []):
                    self.add_voice_child(state)
        if text_channels:
            self.add_section("ТЕКСТОВЫЕ КАНАЛЫ")
            for channel in text_channels:
                self.add_channel_row(channel)

    def add_section(self, title: str) -> None:
        item = QListWidgetItem()
        item.setFlags(Qt.NoItemFlags)
        item.setSizeHint(QSize(220, 28))
        self.channel_list.addItem(item)
        label = QLabel(title)
        label.setObjectName("section")
        label.setContentsMargins(10, 8, 4, 2)
        self.channel_list.setItemWidget(item, label)

    def add_channel_row(self, channel: dict) -> None:
        item = QListWidgetItem()
        item.setData(Qt.UserRole, channel)
        item.setSizeHint(QSize(220, 42))
        self.channel_list.addItem(item)
        active = self.current_channel and self.current_channel.get("id") == channel["id"]
        connected = self.connected_channel_id == channel["id"]
        self.channel_list.setItemWidget(item, self.channel_widget(channel, active=bool(active), connected=connected))

    def add_voice_child(self, state: dict) -> None:
        item = QListWidgetItem()
        item.setFlags(Qt.NoItemFlags)
        item.setSizeHint(QSize(260, 32))
        self.channel_list.addItem(item)
        self.channel_list.setItemWidget(item, self.voice_member_widget(state, compact=True))

    def channel_widget(self, channel: dict, *, active: bool, connected: bool) -> QWidget:
        row = ChannelRow(channel, self.select_channel, self.connect_from_channel_row)
        row.setObjectName("channelRowActive" if active else "channelRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(10, 5, 6, 5)
        layout.setSpacing(8)
        icon = QLabel("#")
        if channel["type"] == "voice":
            icon.setPixmap(svg_icon("voice").pixmap(QSize(17, 17)))
        icon.setObjectName("muted")
        icon.setFixedWidth(22)
        icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        name = QLabel(channel["name"])
        name.setObjectName("channelName")
        name.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(icon)
        layout.addWidget(name, 1)
        if connected:
            badge = QLabel("●")
            badge.setObjectName("ok")
            badge.setToolTip("Вы подключены")
            badge.setAttribute(Qt.WA_TransparentForMouseEvents)
            layout.addWidget(badge)
        count = len(self.voice_cache.get(channel["id"], [])) if channel["type"] == "voice" else 0
        if count:
            meta = QLabel(str(count))
            meta.setObjectName("channelMeta")
            meta.setAttribute(Qt.WA_TransparentForMouseEvents)
            layout.addWidget(meta)
        if channel["type"] == "voice":
            action = icon_button("leave" if connected else "join", "Отключиться" if connected else "Подключиться к каналу")
            action.setObjectName("channelActionConnected" if connected else "channelAction")
            action.clicked.connect(lambda _checked=False, selected_channel=channel: self.connect_from_channel_row(selected_channel))
            layout.addWidget(action)
        return row

    def connect_from_channel_row(self, channel: dict) -> None:
        self.select_channel(channel)
        self.toggle_connection()

    def render_servers(self) -> None:
        for index in range(self.space_list.count()):
            item = self.space_list.item(index)
            space = item.data(Qt.UserRole)
            if space:
                selected = bool(self.current_space and self.current_space.get("id") == space["id"])
                self.space_list.setItemWidget(item, self.server_widget(space, selected=selected))

    def server_widget(self, space: dict, *, selected: bool) -> QWidget:
        row = QFrame()
        row.setObjectName("serverRowSelected" if selected else "serverRow")
        row.setToolTip(space["name"])
        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 7, 6, 7)
        layout.setSpacing(8)
        badge = QLabel(self.initials(space["name"])[:2])
        badge.setObjectName("serverBadgeSelected" if selected else "serverBadge")
        badge.setAlignment(Qt.AlignCenter)
        layout.addWidget(badge)
        label = QLabel(space["name"])
        label.setObjectName("serverName")
        label.setWordWrap(True)
        layout.addWidget(label, 1)
        return row

    def voice_member_widget(self, state: dict, *, compact: bool) -> QWidget:
        subtitle = self.state_text(state)
        if compact:
            return self.member_widget(f"    {state['display_name']}", subtitle, muted=state["muted"] or state["deafened"], compact=True, state=state)
        return self.member_widget(state["display_name"], subtitle, muted=state["muted"] or state["deafened"], compact=False, state=state)

    def member_widget(self, name: str, subtitle: str, *, muted: bool, compact: bool = False, state: dict | None = None) -> QWidget:
        row = QFrame()
        row.setObjectName("memberRowSpeaking" if state and state.get("speaking") else "memberRow")
        if state:
            row.setContextMenuPolicy(Qt.CustomContextMenu)
            row.customContextMenuRequested.connect(partial(self.show_member_menu, row, state))
        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 3 if compact else 8, 8, 3 if compact else 8)
        layout.setSpacing(8)
        if not compact:
            avatar = QLabel(self.initials(name))
            avatar.setObjectName("avatar")
            avatar.setAlignment(Qt.AlignCenter)
            layout.addWidget(avatar)
        texts = QVBoxLayout()
        texts.setSpacing(1)
        title = QLabel(name)
        title.setObjectName("channelName")
        title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        sub = QLabel(subtitle)
        sub.setObjectName("muted" if muted else "ok")
        sub.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        texts.addWidget(title)
        texts.addWidget(sub)
        layout.addLayout(texts, 1)
        if state and state["user_id"] in self.local_mutes:
            badge = QLabel("LOCAL MUTE")
            badge.setObjectName("pillMuted")
            layout.addWidget(badge)
        return row

    def add_stage_member(self, state: dict) -> None:
        card = QFrame()
        card.setObjectName("voiceCardSpeaking" if state.get("speaking") else "voiceCard")
        card.setFixedSize(230, 170)
        card.setContextMenuPolicy(Qt.CustomContextMenu)
        card.customContextMenuRequested.connect(partial(self.show_member_menu, card, state))
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 14, 14, 14)
        card_layout.setSpacing(8)
        user_id = int(state["user_id"])
        screen_pixmap = self.screen_frames.get(user_id)
        if screen_pixmap:
            preview = QLabel()
            preview.setObjectName("screenPreview")
            preview.setAlignment(Qt.AlignCenter)
            preview.setFixedSize(202, 86)
            preview.setPixmap(screen_pixmap.scaled(preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
            preview.setCursor(Qt.PointingHandCursor)
            preview.setToolTip("Открыть трансляцию на весь экран")
            preview.mousePressEvent = lambda event, selected_user_id=user_id: self.open_screen_viewer(selected_user_id)
            card_layout.addWidget(preview, 0, Qt.AlignCenter)
        else:
            avatar = QLabel(self.initials(state["display_name"]))
            avatar.setObjectName("bigAvatar")
            avatar.setAlignment(Qt.AlignCenter)
            card_layout.addWidget(avatar, 0, Qt.AlignCenter)
        name = QLabel(state["display_name"])
        name.setObjectName("stageMemberName")
        name.setAlignment(Qt.AlignCenter)
        name.setWordWrap(True)
        status = QLabel(self.state_text(state))
        status.setObjectName("muted" if state["muted"] or state["deafened"] else "ok")
        status.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(name)
        card_layout.addWidget(status)
        card_layout.addStretch()
        bottom = QHBoxLayout()
        bottom.addStretch()
        badge_text = "MUTE" if state["muted"] else "ГОВОРИТ" if state.get("speaking") else "LIVE"
        badge = QLabel(badge_text)
        badge.setObjectName("pillMuted" if state["muted"] else "pillSpeaking" if state.get("speaking") else "pillLive")
        bottom.addWidget(badge)
        if screen_pixmap:
            badge.setText("SCREEN")
            badge.setObjectName("pillScreen")
            open_button = icon_button("maximize", "Открыть трансляцию на весь экран")
            open_button.setObjectName("channelAction")
            open_button.clicked.connect(lambda _checked=False, selected_user_id=user_id: self.open_screen_viewer(selected_user_id))
            bottom.addWidget(open_button)
        if state["user_id"] in self.local_mutes:
            local = QLabel("LOCAL")
            local.setObjectName("pillMuted")
            bottom.addWidget(local)
        bottom.addStretch()
        card_layout.addLayout(bottom)
        index = self.stage_members_layout.count()
        self.stage_members_layout.addWidget(card, index // 4, index % 4)

    def show_member_menu(self, parent: QWidget, state: dict, point) -> None:
        menu = QMenu(parent)
        user_id = int(state["user_id"])
        muted_locally = user_id in self.local_mutes
        mute_action = QAction("Включить локально" if muted_locally else "Заглушить локально", menu)
        mute_action.triggered.connect(lambda: self.toggle_local_mute(user_id))
        menu.addAction(mute_action)
        menu.addSeparator()
        volume_label = QLabel(f"Громкость: {self.local_volumes.get(user_id, 100)}%")
        volume_label.setObjectName("menuLabel")
        volume_action = QWidgetAction(menu)
        volume_action.setDefaultWidget(volume_label)
        menu.addAction(volume_action)
        slider = QSlider(Qt.Horizontal)
        slider.setRange(0, 200)
        slider.setValue(self.local_volumes.get(user_id, 100))
        slider.setMinimumWidth(180)
        slider.valueChanged.connect(lambda value: self.set_local_volume(user_id, value, volume_label))
        slider_action = QWidgetAction(menu)
        slider_action.setDefaultWidget(slider)
        menu.addAction(slider_action)
        menu.exec(parent.mapToGlobal(point))

    def toggle_local_mute(self, user_id: int) -> None:
        if user_id in self.local_mutes:
            self.local_mutes.remove(user_id)
        else:
            self.local_mutes.add(user_id)
        self.refresh_voice()

    def set_local_volume(self, user_id: int, value: int, label: QLabel) -> None:
        self.local_volumes[user_id] = value
        label.setText(f"Громкость: {value}%")

    def closeEvent(self, event) -> None:
        if self.screen_viewer:
            self.screen_viewer.close()
            self.screen_viewer = None
        self.stop_screen_share()
        self.stop_screen_client()
        self.stop_audio()
        if self.connected_channel_id:
            try:
                self.api.disconnect(self.connected_channel_id)
            except ApiError:
                pass
        event.accept()

    def add_stage_empty(self, text: str) -> None:
        empty = QLabel(text)
        empty.setObjectName("emptyText")
        empty.setAlignment(Qt.AlignCenter)
        empty.setWordWrap(True)
        self.stage_members_layout.addWidget(empty, 0, 0, 1, 4)

    def clear_stage_members(self) -> None:
        while self.stage_members_layout.count():
            item = self.stage_members_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def state_text(self, state: dict) -> str:
        if state["deafened"]:
            return "без звука"
        if state["muted"]:
            return "микрофон выключен"
        if state["speaking"]:
            return "говорит"
        return "в канале"

    def initials(self, name: str) -> str:
        cleaned = name.strip().replace("@", "")
        if not cleaned:
            return "?"
        parts = cleaned.split()
        return "".join(part[0] for part in parts[:2]).upper()

    def create_space(self) -> None:
        name, ok = QInputDialog.getText(self, "Новое пространство", "Название")
        if ok and name.strip():
            try:
                self.api.create_space(name.strip())
                self.current_space = None
                self.reload_all()
            except ApiError as exc:
                self.show_error(str(exc))

    def create_channel(self) -> None:
        if not self.current_space:
            return
        dialog = ChannelDialog(self)
        if dialog.exec() == QDialog.Accepted:
            try:
                self.api.create_channel(self.current_space["id"], dialog.name.text().strip(), dialog.type.currentData())
                self.select_space(self.current_space)
            except ApiError as exc:
                self.show_error(str(exc))

    def create_user(self) -> None:
        dialog = UserDialog(self)
        if dialog.exec() == QDialog.Accepted:
            try:
                self.api.create_user(
                    dialog.username.text().strip(),
                    dialog.display_name.text().strip(),
                    dialog.password.text(),
                    dialog.is_admin.isChecked(),
                )
                QMessageBox.information(self, "Готово", "Пользователь создан. Передайте ему временный пароль безопасным способом.")
            except ApiError as exc:
                self.show_error(str(exc))

    def add_member_to_channel(self) -> None:
        if not self.current_channel:
            return
        try:
            users = self.api.users()
        except ApiError as exc:
            self.show_error(str(exc))
            return
        labels = [f"{u['display_name']} (@{u['username']})" for u in users]
        if not labels:
            return
        selected, ok = QInputDialog.getItem(self, "Добавить участника", "Пользователь", labels, 0, False)
        if ok:
            user = users[labels.index(selected)]
            try:
                self.api.add_channel_member(self.current_channel["id"], user["id"])
                QMessageBox.information(self, "Готово", "Пользователь добавлен в канал.")
            except ApiError as exc:
                self.show_error(str(exc))

    def show_error(self, text: str) -> None:
        QMessageBox.warning(self, "Ошибка", text)


class MicTestDialog(QDialog):
    def __init__(self, parent: QWidget, input_device_id: int | None, output_device_id: int | None, threshold: int) -> None:
        super().__init__(parent)
        self.setWindowTitle("Тест микрофона")
        self.setMinimumWidth(420)
        self.monitor = MicTestMonitor(input_device_id, output_device_id, threshold)
        self.level = QProgressBar()
        self.level.setRange(0, 100)
        self.level.setValue(0)
        self.status = QLabel("Говорите в микрофон.")
        self.status.setObjectName("muted")
        self.hint = QLabel("Вы услышите себя с небольшой задержкой. Если включено шумоподавление, настройте порог так, чтобы голос уверенно срабатывал, а фон не срабатывал.")
        self.hint.setObjectName("muted")
        self.hint.setWordWrap(True)
        self.timer = QTimer(self)
        self.timer.setInterval(80)
        self.timer.timeout.connect(self.refresh_level)

        layout = QVBoxLayout(self)
        title = QLabel("Тест микрофона")
        title.setObjectName("title")
        layout.addWidget(title)
        layout.addWidget(self.level)
        layout.addWidget(self.status)
        layout.addWidget(self.hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        try:
            self.monitor.start()
            self.timer.start()
        except Exception as exc:
            self.status.setObjectName("warn")
            self.status.setText(f"Не удалось открыть микрофон: {exc}")

    def refresh_level(self) -> None:
        self.level.setValue(self.monitor.level)
        self.status.setText("Голос обнаружен" if self.monitor.speaking else "Тишина или фоновый шум")
        self.status.setObjectName("ok" if self.monitor.speaking else "muted")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def closeEvent(self, event) -> None:
        self.monitor.stop()
        event.accept()

    def reject(self) -> None:
        self.monitor.stop()
        super().reject()


class VoiceThresholdMeter(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.level = 0
        self.threshold = 450
        self.active = False
        self.setMinimumHeight(42)

    def set_values(self, level: int, threshold: int, active: bool) -> None:
        self.level = max(0, min(100, level))
        self.threshold = max(150, min(1600, threshold))
        self.active = active
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(0, 8, self.width(), 20)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#1e1f22"))
        painter.drawRoundedRect(rect, 8, 8)

        level_width = rect.width() * (self.level / 100)
        if level_width > 0:
            level_rect = QRectF(rect.left(), rect.top(), level_width, rect.height())
            painter.setBrush(QColor("#23a559" if self.active else "#4e5058"))
            painter.drawRoundedRect(level_rect, 8, 8)

        threshold_percent = min(100, int(self.threshold / 30)) / 100
        threshold_x = rect.left() + rect.width() * threshold_percent
        painter.setPen(QPen(QColor("#f0b232"), 2))
        painter.drawLine(int(threshold_x), 4, int(threshold_x), 34)

        painter.setPen(QColor("#949ba4"))
        painter.drawText(rect.adjusted(0, 21, 0, 18), Qt.AlignRight, "порог")


class ScreenShareStartDialog(QDialog):
    def __init__(self, parent: QWidget, quality_key: str, fps_interval_ms: int) -> None:
        super().__init__(parent)
        self.setWindowTitle("Демонстрация экрана")
        self.setMinimumWidth(560)

        self.preview = QLabel("Предпросмотр экрана")
        self.preview.setObjectName("screenStartPreview")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setFixedSize(520, 292)

        self.quality_combo = QComboBox()
        for key, preset in SCREEN_QUALITY_PRESETS.items():
            self.quality_combo.addItem(str(preset["label"]), key)
        quality_index = self.quality_combo.findData(quality_key)
        self.quality_combo.setCurrentIndex(quality_index if quality_index >= 0 else 0)
        self.quality_combo.currentIndexChanged.connect(self.update_quality_hint)

        self.fps_combo = QComboBox()
        for label, interval_ms in SCREEN_FPS_PRESETS:
            self.fps_combo.addItem(label, interval_ms)
        fps_index = self.fps_combo.findData(fps_interval_ms)
        self.fps_combo.setCurrentIndex(fps_index if fps_index >= 0 else self.fps_combo.findData(DEFAULT_SCREEN_FPS_INTERVAL_MS))
        self.fps_combo.currentIndexChanged.connect(self.update_quality_hint)

        self.quality_hint = QLabel("")
        self.quality_hint.setObjectName("muted")
        self.quality_hint.setWordWrap(True)

        start_button = QPushButton("Начать трансляцию")
        start_button.clicked.connect(self.accept)
        cancel_button = QPushButton("Отмена")
        cancel_button.setObjectName("secondary")
        cancel_button.clicked.connect(self.reject)

        form = QFormLayout()
        form.addRow("Разрешение", self.quality_combo)
        form.addRow("Частота кадров", self.fps_combo)

        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(cancel_button)
        buttons.addWidget(start_button)

        layout = QVBoxLayout(self)
        title = QLabel("Поделиться экраном")
        title.setObjectName("title")
        layout.addWidget(title)
        layout.addWidget(self.preview, 0, Qt.AlignCenter)
        layout.addLayout(form)
        layout.addWidget(self.quality_hint)
        layout.addLayout(buttons)

        self.preview_timer = QTimer(self)
        self.preview_timer.setInterval(350)
        self.preview_timer.timeout.connect(self.refresh_preview)
        self.update_quality_hint()
        self.refresh_preview()
        self.preview_timer.start()

    def selected_quality_key(self) -> str:
        return str(self.quality_combo.currentData())

    def selected_fps_interval_ms(self) -> int:
        interval_ms = self.fps_combo.currentData()
        return int(interval_ms) if isinstance(interval_ms, int) else DEFAULT_SCREEN_FPS_INTERVAL_MS

    def update_quality_hint(self) -> None:
        preset = SCREEN_QUALITY_PRESETS.get(self.selected_quality_key(), SCREEN_QUALITY_PRESETS[DEFAULT_SCREEN_QUALITY])
        interval_ms = self.selected_fps_interval_ms()
        fps = max(1, round(1000 / interval_ms))
        capture_presets = preset["captures"]
        width, height, quality = capture_presets[0]
        fps_note = (
            " 60 FPS в текущей JPEG-трансляции зависит от скорости захвата и сжатия; "
            "для максимальной плавности выберите FullHD скорость или HD 720p."
            if interval_ms <= 17
            else ""
        )
        self.quality_hint.setText(f"Будет отправляться: {width}x{height}, {fps} FPS, JPEG {quality}. {preset['tooltip']}.{fps_note}")

    def refresh_preview(self) -> None:
        screen = QApplication.primaryScreen()
        if not screen:
            self.preview.setText("Экран недоступен")
            return
        pixmap = screen.grabWindow(0).scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.preview.setPixmap(pixmap)
        self.preview.setText("")

    def accept(self) -> None:
        self.preview_timer.stop()
        super().accept()

    def reject(self) -> None:
        self.preview_timer.stop()
        super().reject()

    def closeEvent(self, event) -> None:
        self.preview_timer.stop()
        event.accept()


class ScreenShareViewer(QDialog):
    def __init__(
        self,
        parent: QWidget,
        selected_user_id: int,
        states_provider: Callable[[], list[dict]],
        frames_provider: Callable[[], dict[int, QPixmap]],
        stream_info_provider: Callable[[int], str],
        viewer_quality_key: str,
        viewer_fps_interval_ms: int,
        preferences_changed: Callable[[str, int], None],
        initials_provider: Callable[[str], str],
        state_text_provider: Callable[[dict], str],
    ) -> None:
        super().__init__(parent)
        self.selected_user_id = selected_user_id
        self.states_provider = states_provider
        self.frames_provider = frames_provider
        self.stream_info_provider = stream_info_provider
        self.viewer_quality_key = viewer_quality_key if viewer_quality_key in VIEWER_QUALITY_PRESETS else DEFAULT_VIEWER_QUALITY
        self.viewer_fps_interval_ms = viewer_fps_interval_ms
        self.preferences_changed = preferences_changed
        self.initials_provider = initials_provider
        self.state_text_provider = state_text_provider
        self.participants_visible = True
        self.dirty = True
        self.setWindowTitle("Просмотр демонстрации экрана")
        self.setMinimumSize(960, 540)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 14)
        root.setSpacing(10)
        top = QHBoxLayout()
        self.title = QLabel("Демонстрация экрана")
        self.title.setObjectName("title")
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("muted")
        self.hide_participants_button = QPushButton("Скрыть участников")
        self.hide_participants_button.setObjectName("secondary")
        self.hide_participants_button.clicked.connect(self.toggle_participants)
        self.viewer_quality_combo = QComboBox()
        self.viewer_quality_combo.setObjectName("compactCombo")
        self.viewer_quality_combo.setToolTip("Качество просмотра только для вас")
        for key, preset in VIEWER_QUALITY_PRESETS.items():
            self.viewer_quality_combo.addItem(str(preset["label"]), key)
        quality_index = self.viewer_quality_combo.findData(self.viewer_quality_key)
        self.viewer_quality_combo.setCurrentIndex(quality_index if quality_index >= 0 else 0)
        self.viewer_quality_combo.currentIndexChanged.connect(self.change_viewer_preferences)
        self.viewer_fps_combo = QComboBox()
        self.viewer_fps_combo.setObjectName("compactCombo")
        self.viewer_fps_combo.setToolTip("Частота просмотра только для вас")
        for label, interval_ms in SCREEN_FPS_PRESETS:
            self.viewer_fps_combo.addItem(label, interval_ms)
        fps_index = self.viewer_fps_combo.findData(self.viewer_fps_interval_ms)
        self.viewer_fps_combo.setCurrentIndex(fps_index if fps_index >= 0 else self.viewer_fps_combo.findData(DEFAULT_VIEWER_FPS_INTERVAL_MS))
        self.viewer_fps_combo.currentIndexChanged.connect(self.change_viewer_preferences)
        close_button = QPushButton("Закрыть")
        close_button.setObjectName("secondary")
        close_button.clicked.connect(self.close)
        top.addWidget(self.title)
        top.addWidget(self.subtitle)
        top.addStretch()
        top.addWidget(self.viewer_quality_combo)
        top.addWidget(self.viewer_fps_combo)
        top.addWidget(self.hide_participants_button)
        top.addWidget(close_button)
        root.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(12)
        self.preview = QLabel("Ожидание кадра трансляции")
        self.preview.setObjectName("screenLargePreview")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(640, 360)
        self.preview.setScaledContents(False)
        body.addWidget(self.preview, 1)

        self.participants_panel = QFrame()
        self.participants_panel.setObjectName("viewerParticipants")
        self.participants_panel.setFixedWidth(280)
        participants_layout = QVBoxLayout(self.participants_panel)
        participants_layout.setContentsMargins(10, 10, 10, 10)
        participants_layout.setSpacing(8)
        participants_title = QLabel("Участники")
        participants_title.setObjectName("title")
        self.participants_list = QListWidget()
        self.participants_list.itemClicked.connect(self.select_participant)
        participants_layout.addWidget(participants_title)
        participants_layout.addWidget(self.participants_list, 1)
        body.addWidget(self.participants_panel)
        root.addLayout(body, 1)

        self.timer = QTimer(self)
        self.timer.setInterval(self.viewer_fps_interval_ms)
        self.timer.timeout.connect(self.refresh_if_dirty)
        self.timer.start()
        self.refresh_view()

    def show_fullscreen(self) -> None:
        self.showFullScreen()

    def toggle_participants(self) -> None:
        self.participants_visible = not self.participants_visible
        self.participants_panel.setVisible(self.participants_visible)
        self.hide_participants_button.setText("Скрыть участников" if self.participants_visible else "Показать участников")
        self.refresh_view()

    def select_participant(self, item: QListWidgetItem) -> None:
        user_id = item.data(Qt.UserRole)
        if user_id in self.frames_provider():
            self.selected_user_id = int(user_id)
            self.refresh_view()

    def change_viewer_preferences(self) -> None:
        quality_key = str(self.viewer_quality_combo.currentData())
        interval_ms = self.viewer_fps_combo.currentData()
        if quality_key not in VIEWER_QUALITY_PRESETS:
            quality_key = DEFAULT_VIEWER_QUALITY
        if not isinstance(interval_ms, int):
            interval_ms = DEFAULT_VIEWER_FPS_INTERVAL_MS
        self.viewer_quality_key = quality_key
        self.viewer_fps_interval_ms = interval_ms
        self.timer.setInterval(interval_ms)
        self.preferences_changed(quality_key, interval_ms)
        self.refresh_view()

    def mark_dirty(self) -> None:
        self.dirty = True

    def refresh_if_dirty(self) -> None:
        if self.dirty:
            self.refresh_view()

    def refresh_view(self) -> None:
        self.dirty = False
        states = self.states_provider()
        frames = self.frames_provider()
        if self.selected_user_id not in frames and frames:
            self.selected_user_id = next(iter(frames))

        selected_state = next((state for state in states if int(state["user_id"]) == self.selected_user_id), None)
        display_name = selected_state["display_name"] if selected_state else "Трансляция"
        self.title.setText(display_name)
        stream_info = self.stream_info_provider(self.selected_user_id)
        fallback_info = ""
        pixmap = frames.get(self.selected_user_id)
        if pixmap:
            fallback_info = f"{pixmap.width()}x{pixmap.height()}"
        source_info = stream_info if stream_info else fallback_info if fallback_info else "трансляция остановлена"
        viewer_quality = VIEWER_QUALITY_PRESETS.get(self.viewer_quality_key, VIEWER_QUALITY_PRESETS[DEFAULT_VIEWER_QUALITY])
        viewer_fps = max(1, round(1000 / self.viewer_fps_interval_ms))
        self.subtitle.setText(f"{source_info} · просмотр: {viewer_quality['label']}, {viewer_fps} FPS")

        if pixmap:
            target = self.preview.size()
            pixmap = self.apply_viewer_quality(pixmap)
            self.preview.setPixmap(pixmap.scaled(target, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.preview.setText("")
        else:
            self.preview.setPixmap(QPixmap())
            self.preview.setText("Ожидание кадра трансляции")

        self.render_participants(states, frames)

    def apply_viewer_quality(self, pixmap: QPixmap) -> QPixmap:
        preset = VIEWER_QUALITY_PRESETS.get(self.viewer_quality_key, VIEWER_QUALITY_PRESETS[DEFAULT_VIEWER_QUALITY])
        size = preset["size"]
        if not size:
            return pixmap
        width, height = size
        if pixmap.width() <= width and pixmap.height() <= height:
            return pixmap
        return pixmap.scaled(width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    def render_participants(self, states: list[dict], frames: dict[int, QPixmap]) -> None:
        self.participants_list.clear()
        for state in states:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, int(state["user_id"]))
            item.setSizeHint(QSize(250, 54))
            self.participants_list.addItem(item)
            row = QFrame()
            row.setObjectName("viewerParticipantActive" if int(state["user_id"]) == self.selected_user_id else "memberRowSpeaking" if state.get("speaking") else "memberRow")
            layout = QHBoxLayout(row)
            layout.setContentsMargins(8, 6, 8, 6)
            layout.setSpacing(8)
            avatar = QLabel(self.initials_provider(state["display_name"]))
            avatar.setObjectName("avatar")
            avatar.setAlignment(Qt.AlignCenter)
            layout.addWidget(avatar)
            text = QVBoxLayout()
            text.setSpacing(1)
            name = QLabel(state["display_name"])
            name.setObjectName("channelName")
            status_text = "показывает экран" if int(state["user_id"]) in frames else self.state_text_provider(state)
            status = QLabel(status_text)
            status.setObjectName("ok" if int(state["user_id"]) in frames or state.get("speaking") else "muted")
            text.addWidget(name)
            text.addWidget(status)
            layout.addLayout(text, 1)
            self.participants_list.setItemWidget(item, row)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        self.timer.stop()
        event.accept()


class AudioSettingsDialog(QDialog):
    def __init__(
        self,
        parent: QWidget,
        input_device_id: int | None,
        output_device_id: int | None,
        noise_suppression: bool,
        noise_threshold: int,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Настройки аудио")
        self.setMinimumWidth(520)
        self.input_device_id = input_device_id
        self.output_device_id = output_device_id
        try:
            self.inputs, self.outputs = audio_devices(include_advanced=False)
        except Exception as exc:
            self.inputs = []
            self.outputs = []
            self.error_text = f"Не удалось получить список устройств: {exc}"
        else:
            self.error_text = ""
        self.level_monitor: MicTestMonitor | None = None

        self.input_combo = QComboBox()
        self.output_combo = QComboBox()
        self.noise_box = QCheckBox("Шумоподавление")
        self.noise_box.setChecked(noise_suppression)
        self.threshold_slider = QSlider(Qt.Horizontal)
        self.threshold_slider.setRange(150, 1600)
        self.threshold_slider.setValue(noise_threshold)
        self.threshold_slider.valueChanged.connect(self.update_threshold_label)
        self.threshold_label = QLabel("")
        self.threshold_label.setObjectName("muted")
        self.threshold_meter = VoiceThresholdMeter()
        self.threshold_status = QLabel("Говорите в микрофон, чтобы подобрать порог.")
        self.threshold_status.setObjectName("muted")
        self.threshold_timer = QTimer(self)
        self.threshold_timer.setInterval(80)
        self.threshold_timer.timeout.connect(self.refresh_threshold_meter)
        self.advanced_box = QCheckBox("Показать системные и виртуальные устройства")
        self.advanced_box.stateChanged.connect(self.reload_devices)
        self.test_button = QPushButton("Тест микрофона")
        self.test_button.setObjectName("secondary")
        self.test_button.clicked.connect(self.open_mic_test)
        self.fill_combo(self.input_combo, self.inputs, input_device_id, "Системный микрофон")
        self.fill_combo(self.output_combo, self.outputs, output_device_id, "Системное устройство вывода")
        self.input_combo.currentIndexChanged.connect(self.restart_threshold_monitor)

        form = QFormLayout()
        form.addRow("Микрофон", self.input_combo)
        form.addRow("Вывод", self.output_combo)
        noise_row = QHBoxLayout()
        noise_row.addWidget(self.noise_box)
        noise_row.addStretch()
        noise_row.addWidget(self.test_button)
        form.addRow("Голос", noise_row)
        threshold_row = QHBoxLayout()
        threshold_row.addWidget(self.threshold_slider, 1)
        threshold_row.addWidget(self.threshold_label)
        form.addRow("Порог", threshold_row)
        form.addRow("", self.threshold_meter)
        form.addRow("", self.threshold_status)
        form.addRow("", self.advanced_box)
        self.update_threshold_label(noise_threshold)

        hint = QLabel(self.error_text or "По умолчанию показаны обычные устройства. Расширенный список нужен для виртуальных кабелей, line-in и системных endpoints.")
        hint.setObjectName("muted" if not self.error_text else "warn")
        hint.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        title = QLabel("Аудиоустройства")
        title.setObjectName("title")
        layout.addWidget(title)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addWidget(buttons)
        self.restart_threshold_monitor()

    def fill_combo(self, combo: QComboBox, devices: list[AudioDevice], selected: int | None, default_label: str) -> None:
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(default_label, None)
        selected_index = 0
        for device in devices:
            label = f"{device.simple_name}  ({device.id})" if device.advanced else device.simple_name
            combo.addItem(label, device.id)
            if selected == device.id:
                selected_index = combo.count() - 1
        combo.setCurrentIndex(selected_index)
        combo.blockSignals(False)

    def reload_devices(self) -> None:
        self.input_device_id, self.output_device_id = self.selected_devices()
        include_advanced = self.advanced_box.isChecked()
        try:
            self.inputs, self.outputs = audio_devices(include_advanced=include_advanced)
        except Exception:
            self.inputs = []
            self.outputs = []
        self.fill_combo(self.input_combo, self.inputs, self.input_device_id, "Системный микрофон")
        self.fill_combo(self.output_combo, self.outputs, self.output_device_id, "Системное устройство вывода")
        self.restart_threshold_monitor()

    def selected_devices(self) -> tuple[int | None, int | None]:
        return self.input_combo.currentData(), self.output_combo.currentData()

    def noise_suppression_enabled(self) -> bool:
        return self.noise_box.isChecked()

    def selected_threshold(self) -> int:
        return self.threshold_slider.value()

    def update_threshold_label(self, value: int) -> None:
        if value < 400:
            mode = "чувствительно"
        elif value < 900:
            mode = "обычно"
        else:
            mode = "строго"
        self.threshold_label.setText(f"{value} ({mode})")
        if self.level_monitor:
            self.level_monitor.set_threshold(value)
        self.threshold_meter.set_values(
            self.level_monitor.level if self.level_monitor else 0,
            value,
            self.level_monitor.speaking if self.level_monitor else False,
        )

    def restart_threshold_monitor(self) -> None:
        self.stop_threshold_monitor()
        try:
            self.level_monitor = MicTestMonitor(
                self.input_combo.currentData(),
                None,
                self.threshold_slider.value(),
                playback=False,
            )
            self.level_monitor.start()
            self.threshold_timer.start()
            self.threshold_status.setText("Говорите в микрофон, чтобы подобрать порог.")
            self.threshold_status.setObjectName("muted")
        except Exception as exc:
            self.level_monitor = None
            self.threshold_timer.stop()
            self.threshold_status.setText(f"Не удалось открыть микрофон для шкалы: {exc}")
            self.threshold_status.setObjectName("warn")
        self.threshold_status.style().unpolish(self.threshold_status)
        self.threshold_status.style().polish(self.threshold_status)

    def stop_threshold_monitor(self) -> None:
        self.threshold_timer.stop()
        if self.level_monitor:
            self.level_monitor.stop()
            self.level_monitor = None

    def refresh_threshold_meter(self) -> None:
        if not self.level_monitor:
            return
        self.threshold_meter.set_values(
            self.level_monitor.level,
            self.threshold_slider.value(),
            self.level_monitor.speaking,
        )
        self.threshold_status.setText("Голос проходит" if self.level_monitor.speaking else "Фон отсекается")
        self.threshold_status.setObjectName("ok" if self.level_monitor.speaking else "muted")
        self.threshold_status.style().unpolish(self.threshold_status)
        self.threshold_status.style().polish(self.threshold_status)

    def open_mic_test(self) -> None:
        self.stop_threshold_monitor()
        dialog = MicTestDialog(self, self.input_combo.currentData(), self.output_combo.currentData(), self.threshold_slider.value())
        dialog.exec()
        self.restart_threshold_monitor()

    def accept(self) -> None:
        self.stop_threshold_monitor()
        super().accept()

    def reject(self) -> None:
        self.stop_threshold_monitor()
        super().reject()

    def closeEvent(self, event) -> None:
        self.stop_threshold_monitor()
        event.accept()


class ChannelDialog(QDialog):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Новый канал")
        self.name = QLineEdit()
        self.type = QComboBox()
        self.type.addItem("Голосовой", "voice")
        self.type.addItem("Текстовый", "text")
        form = QFormLayout()
        form.addRow("Название", self.name)
        form.addRow("Тип", self.type)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)


class UserDialog(QDialog):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Новый пользователь")
        self.username = QLineEdit()
        self.display_name = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.is_admin = QCheckBox("Администратор")
        form = QFormLayout()
        form.addRow("Логин", self.username)
        form.addRow("Имя", self.display_name)
        form.addRow("Временный пароль", self.password)
        form.addRow("", self.is_admin)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet(APP_STYLE)
    api = ApiClient()
    login = LoginDialog(api)
    if login.exec() != QDialog.Accepted:
        return 0
    window = MainWindow(api)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
