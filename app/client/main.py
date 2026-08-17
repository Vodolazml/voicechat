from __future__ import annotations

import sys

from PySide6.QtCore import QSize, QTimer, Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from .api import ApiClient, ApiError
from .styles import APP_STYLE


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
        self.space_list.setFixedWidth(78)
        self.space_list.itemClicked.connect(self.select_space_item)
        space_frame = QFrame()
        space_frame.setObjectName("sidebar")
        space_layout = QVBoxLayout(space_frame)
        space_layout.setContentsMargins(8, 8, 8, 8)
        add_space = QPushButton("+")
        add_space.setToolTip("Создать пространство")
        add_space.clicked.connect(self.create_space)
        space_layout.addWidget(self.space_list)
        space_layout.addWidget(add_space)

        self.channel_list = QListWidget()
        self.channel_list.itemClicked.connect(self.select_channel_item)
        channel_frame = QFrame()
        channel_frame.setObjectName("panel")
        channel_frame.setFixedWidth(250)
        channel_layout = QVBoxLayout(channel_frame)
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
        center_layout.setContentsMargins(22, 16, 22, 0)
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
        self.placeholder = QLabel("Выберите голосовой канал слева, чтобы подключиться и увидеть участников.")
        self.placeholder.setWordWrap(True)
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setObjectName("muted")
        self.empty_frame = QFrame()
        self.empty_frame.setObjectName("emptyState")
        empty_layout = QVBoxLayout(self.empty_frame)
        empty_layout.setContentsMargins(28, 28, 28, 28)
        empty_layout.addStretch()
        empty_layout.addWidget(self.placeholder)
        empty_layout.addStretch()
        top = QHBoxLayout()
        top.addWidget(self.channel_title)
        top.addWidget(self.voice_badge)
        top.addStretch()
        top.addWidget(self.connect_button)
        center_layout.addLayout(top)
        center_layout.addWidget(self.channel_status)
        center_layout.addWidget(self.empty_frame, 1)
        center_layout.addWidget(self.bottom_bar())

        member_frame = QFrame()
        member_frame.setObjectName("rightPanel")
        member_frame.setFixedWidth(260)
        member_layout = QVBoxLayout(member_frame)
        member_title = QLabel("Участники")
        member_title.setObjectName("title")
        self.member_list = QListWidget()
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
        self.user_label = QLabel("Пользователь")
        self.user_label.setObjectName("ok")
        self.mute_box = QCheckBox("Микрофон выключен")
        self.mute_box.stateChanged.connect(self.update_voice_flags)
        self.deafen_box = QCheckBox("Без звука")
        self.deafen_box.stateChanged.connect(self.update_voice_flags)
        create_user = QPushButton("Создать пользователя")
        create_user.setObjectName("secondary")
        create_user.clicked.connect(self.create_user)
        refresh = QPushButton("Обновить")
        refresh.setObjectName("secondary")
        refresh.clicked.connect(self.reload_all)
        layout.addWidget(self.user_label)
        layout.addStretch()
        layout.addWidget(self.mute_box)
        layout.addWidget(self.deafen_box)
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
                item = QListWidgetItem(space["name"][:2].upper())
                item.setToolTip(space["name"])
                item.setData(Qt.UserRole, space)
                self.space_list.addItem(item)
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
        self.placeholder.setText(
            "Голосовой канал готов. Участники отображаются слева под каналом и справа в списке."
            if channel["type"] == "voice"
            else "Текстовый чат будет добавлен следующим этапом. Сейчас этот канал уже участвует в системе доступа."
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

    def update_voice_flags(self) -> None:
        self.muted = self.mute_box.isChecked()
        self.deafened = self.deafen_box.isChecked()
        if self.deafened and not self.muted:
            self.mute_box.blockSignals(True)
            self.mute_box.setChecked(True)
            self.mute_box.blockSignals(False)
            self.muted = True
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
        if not self.current_channel or self.current_channel["type"] != "voice":
            return
        try:
            states = self.api.voice_states(self.current_channel["id"])
            self.voice_cache[self.current_channel["id"]] = states
            if not states:
                item = QListWidgetItem()
                item.setSizeHint(QSize(220, 46))
                self.member_list.addItem(item)
                self.member_list.setItemWidget(item, self.member_widget("Пока никого нет", "Ожидание подключения", muted=True))
                return
            for state in states:
                item = QListWidgetItem()
                item.setSizeHint(QSize(220, 58))
                self.member_list.addItem(item)
                self.member_list.setItemWidget(item, self.voice_member_widget(state, compact=False))
            self.render_channels()
        except ApiError:
            if self.current_channel and self.current_channel["type"] == "voice":
                self.member_list.addItem("Не удалось обновить участников")

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
        item.setSizeHint(QSize(220, 34))
        self.channel_list.addItem(item)
        self.channel_list.setItemWidget(item, self.voice_member_widget(state, compact=True))

    def channel_widget(self, channel: dict, *, active: bool, connected: bool) -> QWidget:
        row = QFrame()
        row.setObjectName("channelRowActive" if active else "channelRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(10, 6, 8, 6)
        icon = QLabel("#" if channel["type"] == "text" else "🔊")
        icon.setObjectName("muted")
        name = QLabel(channel["name"])
        name.setObjectName("channelName")
        layout.addWidget(icon)
        layout.addWidget(name, 1)
        if connected:
            badge = QLabel("●")
            badge.setObjectName("ok")
            badge.setToolTip("Вы подключены")
            layout.addWidget(badge)
        count = len(self.voice_cache.get(channel["id"], [])) if channel["type"] == "voice" else 0
        if count:
            meta = QLabel(str(count))
            meta.setObjectName("channelMeta")
            layout.addWidget(meta)
        return row

    def voice_member_widget(self, state: dict, *, compact: bool) -> QWidget:
        subtitle = self.state_text(state)
        if compact:
            return self.member_widget(f"    {state['display_name']}", subtitle, muted=state["muted"] or state["deafened"], compact=True)
        return self.member_widget(state["display_name"], subtitle, muted=state["muted"] or state["deafened"], compact=False)

    def member_widget(self, name: str, subtitle: str, *, muted: bool, compact: bool = False) -> QWidget:
        row = QFrame()
        row.setObjectName("memberRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 4 if compact else 8, 8, 4 if compact else 8)
        if not compact:
            avatar = QLabel(self.initials(name))
            avatar.setObjectName("avatar")
            avatar.setAlignment(Qt.AlignCenter)
            layout.addWidget(avatar)
        texts = QVBoxLayout()
        title = QLabel(name)
        title.setObjectName("channelName")
        sub = QLabel(subtitle)
        sub.setObjectName("muted" if muted else "ok")
        texts.addWidget(title)
        texts.addWidget(sub)
        layout.addLayout(texts, 1)
        if not compact:
            volume = QSlider(Qt.Horizontal)
            volume.setRange(0, 200)
            volume.setValue(100)
            volume.setToolTip("Локальная громкость участника")
            volume.setFixedWidth(80)
            layout.addWidget(volume)
        return row

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
