from __future__ import annotations

import sys
from functools import partial

from PySide6.QtCore import QSize, QTimer, Qt
from PySide6.QtGui import QAction, QIcon, QPixmap
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
from .styles import APP_STYLE


def svg_icon(name: str, color: str = "#dbdee1") -> QIcon:
    paths = {
        "mic": '<path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3Z"/><path d="M5 10v2a7 7 0 0 0 14 0v-2"/><path d="M12 19v3"/><path d="M8 22h8"/>',
        "mic_off": '<path d="m2 2 20 20"/><path d="M9 9v3a3 3 0 0 0 5 2.2"/><path d="M15 9.3V6a3 3 0 0 0-5.1-2.1"/><path d="M5 10v2a7 7 0 0 0 11.7 5.2"/><path d="M19 10v2a7 7 0 0 1-.7 3"/><path d="M12 19v3"/><path d="M8 22h8"/>',
        "headphones": '<path d="M3 14v-2a9 9 0 0 1 18 0v2"/><path d="M5 14h3v7H5a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2Z"/><path d="M16 14h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2h-3v-7Z"/>',
        "headphones_off": '<path d="m2 2 20 20"/><path d="M3 14v-2a9 9 0 0 1 13.2-8"/><path d="M20.6 13.2A9 9 0 0 0 19 8.5"/><path d="M5 14h3v7H5a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2Z"/><path d="M16 14h3a2 2 0 0 1 2 2v3"/>',
        "voice": '<path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3Z"/><path d="M5 10v2a7 7 0 0 0 14 0v-2"/><path d="M12 19v3"/><path d="M8 22h8"/>',
        "settings": '<path d="M12 15.5A3.5 3.5 0 1 0 12 8a3.5 3.5 0 0 0 0 7.5Z"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1A2 2 0 1 1 4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.6-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.3 7A2 2 0 1 1 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3h.1a1.7 1.7 0 0 0 1-1.6V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.6h.1a1.7 1.7 0 0 0 1.9-.3l.1-.1A2 2 0 1 1 19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9v.1a1.7 1.7 0 0 0 1.6 1h.1a2 2 0 1 1 0 4H21a1.7 1.7 0 0 0-1.6 1Z"/>',
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

        self.setWindowTitle("Private VoiceChat")
        self.resize(1180, 720)
        self.setMinimumSize(900, 560)
        self.setCentralWidget(self.build_ui())

        self.timer = QTimer(self)
        self.timer.setInterval(2500)
        self.timer.timeout.connect(self.refresh_voice)
        self.timer.start()

        self.reload_all()

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
        self.mute_button = icon_button("mic", "Выключить микрофон")
        self.mute_button.clicked.connect(self.toggle_mute)
        self.deafen_button = icon_button("headphones", "Отключить входящий звук")
        self.deafen_button.clicked.connect(self.toggle_deafen)
        self.settings_button = icon_button("settings", "Настройки")
        create_user = QPushButton("Создать пользователя")
        create_user.setObjectName("secondary")
        create_user.clicked.connect(self.create_user)
        refresh = icon_button("refresh", "Обновить")
        refresh.clicked.connect(self.reload_all)
        layout.addWidget(self.user_label)
        layout.addStretch()
        layout.addWidget(self.mute_button)
        layout.addWidget(self.deafen_button)
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
            "Вы подключены. Здесь видны участники, локальные состояния микрофона и готовность будущего аудио."
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
                self.api.disconnect(channel_id)
                self.connected_channel_id = None
                self.channel_status.setText("Отключено")
            else:
                state = self.api.connect(channel_id, self.muted, self.deafened)
                self.connected_channel_id = state["channel_id"]
                self.channel_status.setText("Подключено")
            self.refresh_space_voice_cache()
            self.select_channel(self.current_channel)
        except ApiError as exc:
            self.show_error(str(exc))

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
            self.add_stage_empty("Для текстовых каналов скоро появится полноценный чат.")
            return
        try:
            states = self.api.voice_states(self.current_channel["id"])
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
            if self.current_channel and self.current_channel["type"] == "voice":
                self.member_list.addItem("Не удалось обновить участников")
                self.add_stage_empty("Не удалось обновить участников. Проверьте соединение с сервером.")

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
        row.setObjectName("memberRow")
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
        card.setObjectName("voiceCard")
        card.setFixedSize(210, 150)
        card.setContextMenuPolicy(Qt.CustomContextMenu)
        card.customContextMenuRequested.connect(partial(self.show_member_menu, card, state))
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 14, 14, 14)
        card_layout.setSpacing(8)
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
        badge = QLabel("MUTE" if state["muted"] else "LIVE")
        badge.setObjectName("pillMuted" if state["muted"] else "pillLive")
        bottom.addWidget(badge)
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
