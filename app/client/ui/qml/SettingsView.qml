import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Full-window settings that slide in over the app.
Rectangle {
    id: root
    color: Theme.bg
    property int tab: 0
    readonly property var s: backend.settings
    signal closed()
    signal openSafety()

    readonly property var tabs: [
        {icon: "mic", title: "Голос"},
        {icon: "monitor", title: "Экран"},
        {icon: "keyboard", title: "Горячие клавиши"},
        {icon: "layers", title: "Оверлей"},
        {icon: "bell", title: "Уведомления"},
        {icon: "shield", title: "Аккаунт и устройства"},
        {icon: "palette", title: "Внешний вид"},
        {icon: "bug", title: "О программе"}
    ]

    onVisibleChanged: {
        backend.setMicTest(visible && tab === 0)
        if (visible) { backend.refreshDevices(); backend.loadSessions() }
    }
    onTabChanged: backend.setMicTest(visible && tab === 0)
    Keys.onEscapePressed: root.closed()

    RowLayout {
        anchors.fill: parent
        spacing: 0

        Rectangle {
            Layout.fillHeight: true; Layout.preferredWidth: 250
            color: Theme.panel
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 14; anchors.topMargin: 28; spacing: 2
                Text { text: "НАСТРОЙКИ"; color: Theme.faint; font.family: Theme.font; font.pixelSize: 11; font.weight: Font.Bold; Layout.leftMargin: 10; Layout.bottomMargin: 8 }
                Repeater {
                    model: root.tabs
                    delegate: Rectangle {
                        Layout.fillWidth: true; height: 38; radius: 8
                        color: index === root.tab ? Theme.cardHover : (h.hovered ? Theme.card : "transparent")
                        RowLayout {
                            anchors.fill: parent; anchors.leftMargin: 10; spacing: 10
                            Icon { name: modelData.icon; color: index === root.tab ? Theme.accent2 : Theme.muted; width: 18; height: 18 }
                            Text { text: modelData.title; color: index === root.tab ? Theme.text : Theme.muted; font.family: Theme.font; font.pixelSize: 14; Layout.fillWidth: true }
                        }
                        HoverHandler { id: h; cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: root.tab = index }
                    }
                }
                Item { Layout.fillHeight: true }
                Rectangle {
                    Layout.fillWidth: true; height: 38; radius: 8; color: lh.hovered ? Qt.rgba(1, 0.3, 0.42, 0.12) : "transparent"
                    RowLayout { anchors.fill: parent; anchors.leftMargin: 10; spacing: 10
                        Icon { name: "logout"; color: Theme.bad; width: 18; height: 18 }
                        Text { text: "Выйти из аккаунта"; color: Theme.bad; font.family: Theme.font; font.pixelSize: 14 } }
                    HoverHandler { id: lh; cursorShape: Qt.PointingHandCursor }
                    TapHandler { onTapped: { root.closed(); backend.logout() } }
                }
            }
        }

        Flickable {
            Layout.fillWidth: true; Layout.fillHeight: true
            contentHeight: page.implicitHeight + 80
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar { width: 8 }

            ColumnLayout {
                id: page
                x: 40; y: 32
                width: Math.min(680, parent.width - 120)
                spacing: 14

                Text { text: root.tabs[root.tab].title; color: Theme.text; font.family: Theme.font; font.pixelSize: 24; font.weight: Font.Bold; Layout.bottomMargin: 6 }

                // ---------------------------------------------------------------- voice
                Loader {
                    active: root.tab === 0; visible: active; Layout.fillWidth: true
                    sourceComponent: ColumnLayout {
                    spacing: 14
                    RowLayout {
                        Layout.fillWidth: true; spacing: 14
                        Select {
                            title: "Микрофон"
                            options: backend.inputDevices.map(function(n) { return {value: n, title: n === "" ? "Как в Windows (по умолчанию)" : n} })
                            current: root.s.input_device
                            onPicked: (v) => backend.setSetting("input_device", v)
                        }
                        Select {
                            title: "Наушники / динамики"
                            options: backend.outputDevices.map(function(n) { return {value: n, title: n === "" ? "Как в Windows (по умолчанию)" : n} })
                            current: root.s.output_device
                            onPicked: (v) => backend.setSetting("output_device", v)
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 28
                        SliderRow { title: "Громкость микрофона"; from: 0; to: 2; value: root.s.input_gain; onMoved: (v) => backend.setSetting("input_gain", v) }
                        SliderRow { title: "Громкость звука"; from: 0; to: 2; value: root.s.output_volume; onMoved: (v) => backend.setSetting("output_volume", v) }
                    }
                    Card {
                        ColumnLayout {
                            Layout.fillWidth: true; spacing: 10
                            RowLayout {
                                Text { text: "Проверка микрофона"; color: Theme.text; font.family: Theme.font; font.pixelSize: 14; Layout.fillWidth: true }
                                Text { text: backend.transmitting ? "Вас слышно" : "Тишина"; color: backend.transmitting ? Theme.good : Theme.faint; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.DemiBold }
                            }
                            LevelMeter {
                                Layout.fillWidth: true
                                level: backend.micLevel
                                active: backend.transmitting
                                threshold: root.s.mode === "voice" && !root.s.auto_threshold ? (root.s.threshold_db + 60) / 60 : 0
                            }
                            Text { text: "Говорите: полоска покажет уровень, а зелёный цвет значит, что голос уходит друзьям."; color: Theme.faint; font.family: Theme.font; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                        }
                    }
                    Segmented {
                        title: "Режим передачи"
                        options: [{value: "voice", title: "По голосу"}, {value: "ptt", title: "По кнопке (push-to-talk)"}]
                        current: root.s.mode
                        onPicked: (v) => backend.setSetting("mode", v)
                    }
                    KeyCapture {
                        visible: root.s.mode === "ptt"
                        title: "Кнопка рации"; hint: "Работает поверх игр: держите и говорите. Подойдут боковые кнопки мыши."
                        value: root.s.key_ptt; onChanged: (v) => backend.setSetting("key_ptt", v)
                    }
                    Toggle { visible: root.s.mode === "voice"; title: "Определять порог голоса автоматически"; hint: "Подстраивается под шум комнаты"; checked: root.s.auto_threshold; onToggled: (v) => backend.setSetting("auto_threshold", v) }
                    SliderRow {
                        visible: root.s.mode === "voice" && !root.s.auto_threshold
                        title: "Порог голоса"; from: -70; to: -10; stepSize: 1; value: root.s.threshold_db; suffix: " дБ"; displayScale: 1
                        onMoved: (v) => backend.setSetting("threshold_db", v)
                    }
                    Segmented {
                        title: "Шумоподавление"
                        options: [{value: "off", title: "Выключено"}, {value: "standard", title: "Стандартное"}, {value: "strong", title: "Сильное (ИИ)"}]
                        current: root.s.noise
                        onPicked: (v) => backend.setSetting("noise", v)
                    }
                    Toggle { title: "Эхоподавление"; hint: "Друзья не услышат себя, если вы без наушников"; checked: root.s.echo_cancellation; onToggled: (v) => backend.setSetting("echo_cancellation", v) }
                    Toggle { title: "Автоусиление микрофона"; hint: "Держит громкость вашего голоса ровной"; checked: root.s.auto_gain; onToggled: (v) => backend.setSetting("auto_gain", v) }
                    Toggle { title: "Выравнивать громкость друзей"; hint: "Тихих делает громче, громких — тише. Громкость каждого можно поднять до 300% правой кнопкой по нему"; checked: root.s.normalize_voices; onToggled: (v) => backend.setSetting("normalize_voices", v) }
                                    }
                }

                // ---------------------------------------------------------------- screen
                Loader {
                    active: root.tab === 1; visible: active; Layout.fillWidth: true
                    sourceComponent: ColumnLayout {
                    spacing: 14
                    Segmented {
                        title: "Качество по умолчанию"
                        options: backend.screenPresets.map(function(p) { return {value: p.key, title: p.title} })
                        current: root.s.screen_preset
                        onPicked: (v) => backend.setSetting("screen_preset", v)
                    }
                    Text { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted; font.family: Theme.font; font.pixelSize: 13
                        text: "Смотреть трансляцию друга: кнопка «Смотреть» на его плитке. Двойной клик разворачивает картинку, F11 — на весь экран. Пока вы не смотрите, видео не скачивается и не тратит интернет." }
                                    }
                }

                // ---------------------------------------------------------------- hotkeys
                Loader {
                    active: root.tab === 2; visible: active; Layout.fillWidth: true
                    sourceComponent: ColumnLayout {
                    spacing: 6
                    Text { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted; font.family: Theme.font; font.pixelSize: 13; Layout.bottomMargin: 8
                        text: "Клавиши работают, пока вы в голосовом канале, даже когда игра на весь экран. Игра продолжит получать нажатия как обычно." }
                    KeyCapture { title: "Рация (push-to-talk)"; value: root.s.key_ptt; onChanged: (v) => backend.setSetting("key_ptt", v) }
                    KeyCapture { title: "Выключить / включить микрофон"; value: root.s.key_mute; onChanged: (v) => backend.setSetting("key_mute", v) }
                    KeyCapture { title: "Выключить / включить звук"; value: root.s.key_deafen; onChanged: (v) => backend.setSetting("key_deafen", v) }
                    KeyCapture { title: "Показать / скрыть оверлей"; value: root.s.key_overlay; onChanged: (v) => backend.setSetting("key_overlay", v) }
                                    }
                }

                // ---------------------------------------------------------------- overlay
                Loader {
                    active: root.tab === 3; visible: active; Layout.fillWidth: true
                    sourceComponent: ColumnLayout {
                    spacing: 14
                    Toggle { title: "Оверлей поверх игры"; hint: "Маленький список: кто в канале и кто говорит. Мышь проходит сквозь него, фокус он не забирает"; checked: root.s.overlay_enabled; onToggled: (v) => backend.setSetting("overlay_enabled", v) }
                    Toggle { title: "Показывать только говорящих"; checked: root.s.overlay_speakers_only; onToggled: (v) => backend.setSetting("overlay_speakers_only", v) }
                    Segmented {
                        title: "Угол экрана"
                        options: [{value: "top-left", title: "Слева сверху"}, {value: "top-right", title: "Справа сверху"}, {value: "bottom-left", title: "Слева снизу"}, {value: "bottom-right", title: "Справа снизу"}]
                        current: root.s.overlay_corner
                        onPicked: (v) => backend.setSetting("overlay_corner", v)
                    }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 28
                        SliderRow { title: "Размер"; from: 0.7; to: 1.6; value: root.s.overlay_scale; onMoved: (v) => backend.setSetting("overlay_scale", v) }
                        SliderRow { title: "Прозрачность фона"; from: 0.2; to: 1; value: root.s.overlay_opacity; onMoved: (v) => backend.setSetting("overlay_opacity", v) }
                    }
                    Text { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.faint; font.family: Theme.font; font.pixelSize: 12
                        text: "Оверлей виден в играх в режиме «Оконный без рамки» / «Borderless». В эксклюзивном полноэкранном режиме Windows не позволяет рисовать поверх игры." }
                                    }
                }

                // ---------------------------------------------------------------- notifications
                Loader {
                    active: root.tab === 4; visible: active; Layout.fillWidth: true
                    sourceComponent: ColumnLayout {
                    spacing: 6
                    Toggle { title: "Звук, когда кто-то заходит или выходит"; checked: root.s.sound_join_leave; onToggled: (v) => backend.setSetting("sound_join_leave", v) }
                    Toggle { title: "Звук новых сообщений в канале звонка"; checked: root.s.sound_messages; onToggled: (v) => backend.setSetting("sound_messages", v) }
                    Toggle { title: "Уведомлять, когда меня упомянули (@логин)"; checked: root.s.notify_mentions; onToggled: (v) => backend.setSetting("notify_mentions", v) }
                    Text { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.faint; font.family: Theme.font; font.pixelSize: 12; Layout.topMargin: 8
                        text: "Приложение никогда не показывает всплывающие окна поверх игры и не забирает фокус. Ошибки и события появляются здесь, в правом верхнем углу." }
                                    }
                }

                // ---------------------------------------------------------------- account
                Loader {
                    active: root.tab === 5; visible: active; Layout.fillWidth: true
                    sourceComponent: ColumnLayout {
                    spacing: 14
                    Card {
                        RowLayout {
                            Layout.fillWidth: true; spacing: 14
                            Avatar { size: 56; initials: backend.me.initials || "?"; color: backend.me.color || Theme.accent }
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 2
                                Text { text: backend.me.display_name || ""; color: Theme.text; font.family: Theme.font; font.pixelSize: 17; font.weight: Font.Bold }
                                Text { text: "@" + (backend.me.username || ""); color: Theme.muted; font.family: Theme.font; font.pixelSize: 13 }
                            }
                            PrimaryButton { secondary: true; text: "Коды безопасности"; onClicked: root.openSafety() }
                        }
                    }
                    Text { text: "УСТРОЙСТВА, ГДЕ ВЫ ВОШЛИ"; color: Theme.muted; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.DemiBold }
                    Repeater {
                        model: backend.sessions
                        delegate: Rectangle {
                            Layout.fillWidth: true; height: 58; radius: 12; color: Theme.panel2
                            RowLayout {
                                anchors.fill: parent; anchors.leftMargin: 14; anchors.rightMargin: 10; spacing: 12
                                Icon { name: "monitor"; color: modelData.current ? Theme.good : Theme.muted; width: 22; height: 22 }
                                ColumnLayout {
                                    Layout.fillWidth: true; spacing: 1
                                    Text { text: (modelData.device_name || "Устройство") + (modelData.current ? " · это устройство" : ""); color: Theme.text; font.family: Theme.font; font.pixelSize: 13; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
                                    Text { text: (modelData.ip_address || "") + " · активность " + (modelData.last_used_at || "").replace("T", " ").substring(0, 16); color: Theme.faint; font.family: Theme.font; font.pixelSize: 11 }
                                }
                                PrimaryButton { visible: !modelData.current; implicitHeight: 32; secondary: true; text: "Отключить"; onClicked: backend.revokeSession(modelData.id) }
                            }
                        }
                    }
                    PrimaryButton { secondary: true; text: "Выйти на всех других устройствах"; onClicked: backend.revokeOtherSessions() }
                    Text { text: "СМЕНА ПАРОЛЯ"; color: Theme.muted; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.DemiBold; Layout.topMargin: 10 }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 10
                        Field { id: oldPwd; Layout.fillWidth: true; placeholderText: "Текущий пароль"; echoMode: TextInput.Password }
                        Field { id: newPwd; Layout.fillWidth: true; placeholderText: "Новый пароль"; echoMode: TextInput.Password }
                        PrimaryButton { text: "Сменить"; enabled: oldPwd.text && newPwd.text.length >= 8; onClicked: backend.changePassword(oldPwd.text, newPwd.text) }
                    }
                                    }
                }

                // ---------------------------------------------------------------- appearance
                Loader {
                    active: root.tab === 6; visible: active; Layout.fillWidth: true
                    sourceComponent: ColumnLayout {
                    spacing: 14
                    Text { text: "АКЦЕНТНЫЙ ЦВЕТ"; color: Theme.muted; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.DemiBold }
                    Row {
                        spacing: 12
                        Repeater {
                            model: ["#7c5cff", "#3ea6ff", "#22c3a6", "#2fd67b", "#ffb020", "#ff7849", "#ff3d9a", "#e0e0f0"]
                            delegate: Rectangle {
                                width: 40; height: 40; radius: 20; color: modelData
                                border.width: root.s.accent === modelData ? 3 : 0; border.color: "white"
                                scale: sh.hovered ? 1.1 : 1
                                Behavior on scale { NumberAnimation { duration: Theme.fast } }
                                HoverHandler { id: sh; cursorShape: Qt.PointingHandCursor }
                                TapHandler { onTapped: backend.setSetting("accent", modelData) }
                            }
                        }
                    }
                    Toggle { title: "Закрытие окна сворачивает в трей"; hint: "Звонок продолжается, приложение остаётся у часов"; checked: root.s.close_to_tray; onToggled: (v) => backend.setSetting("close_to_tray", v) }
                    Toggle { title: "Запускать вместе с Windows"; hint: "Сразу в трее, без окна"; checked: root.s.autostart; onToggled: (v) => backend.setSetting("autostart", v) }
                                    }
                }

                // ---------------------------------------------------------------- about
                Loader {
                    active: root.tab === 7; visible: active; Layout.fillWidth: true
                    sourceComponent: ColumnLayout {
                    spacing: 14
                    Card {
                        RowLayout {
                            Layout.fillWidth: true
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 2
                                Text { text: "VoiceChat " + backend.version; color: Theme.text; font.family: Theme.font; font.pixelSize: 16; font.weight: Font.Bold }
                                Text { text: backend.updateInfo.version ? "Доступна версия " + backend.updateInfo.version : "У вас последняя версия"; color: backend.updateInfo.version ? Theme.accent2 : Theme.muted; font.family: Theme.font; font.pixelSize: 13 }
                            }
                            PrimaryButton { visible: !!backend.updateInfo.version; text: backend.updateProgress >= 0 ? Math.round(backend.updateProgress * 100) + "%" : "Обновить"; enabled: backend.updateProgress < 0; onClicked: backend.installUpdate() }
                        }
                    }
                    Card {
                        RowLayout {
                            Layout.fillWidth: true
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 2
                                Text { text: "Что-то не работает?"; color: Theme.text; font.family: Theme.font; font.pixelSize: 14; font.weight: Font.DemiBold }
                                Text { Layout.fillWidth: true; wrapMode: Text.WordWrap; text: "Сохраните отчёт и отправьте администратору. В нём журнал и настройки звука, без паролей и ключей."; color: Theme.muted; font.family: Theme.font; font.pixelSize: 12 }
                            }
                            PrimaryButton { secondary: true; text: "Сохранить отчёт"; onClicked: backend.saveReport() }
                        }
                    }
                                    }
                }
            }
        }
    }

    IconButton {
        anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 24
        glyph: "close"; size: 40; tip: "Закрыть (Esc)"
        onClicked: root.closed()
    }
}
