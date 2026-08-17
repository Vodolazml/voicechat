APP_STYLE = """
QWidget {
    background: #313338;
    color: #dbdee1;
    font-family: "Segoe UI";
    font-size: 14px;
}
QLineEdit, QComboBox, QSpinBox {
    background: #1e1f22;
    border: 1px solid #1a1b1e;
    border-radius: 6px;
    padding: 8px 10px;
    color: #f2f3f5;
}
QPushButton {
    background: #5865f2;
    border: none;
    border-radius: 6px;
    padding: 8px 12px;
    color: #ffffff;
    font-weight: 600;
}
QPushButton:hover { background: #6873f5; }
QPushButton:pressed { background: #4752c4; }
QPushButton:disabled {
    background: #3a3c42;
    color: #80848e;
}
QPushButton#secondary {
    background: #3f4147;
    color: #dbdee1;
}
QPushButton#secondary:hover {
    background: #4e5058;
}
QPushButton#voiceConnected {
    background: #248046;
}
QPushButton#voiceConnected:hover {
    background: #1a6334;
}
QPushButton#danger {
    background: #d83c3e;
}
QToolButton#iconButton {
    background: transparent;
    border: none;
    border-radius: 16px;
    padding: 7px;
    min-width: 32px;
    min-height: 32px;
}
QToolButton#iconButton:hover {
    background: #3f4147;
}
QToolButton#iconDanger {
    background: #da373c;
    border: none;
    border-radius: 16px;
    padding: 7px;
    min-width: 32px;
    min-height: 32px;
}
QToolButton#iconDanger:hover {
    background: #a12828;
}
QListWidget {
    background: transparent;
    border: none;
    outline: none;
}
QListWidget::item {
    border-radius: 6px;
    padding: 0;
    margin: 1px 6px;
}
QListWidget::item:selected {
    background: transparent;
}
QFrame#sidebar {
    background: #1e1f22;
}
QFrame#panel {
    background: #2b2d31;
}
QFrame#mainArea {
    background: #313338;
}
QFrame#rightPanel {
    background: #2b2d31;
}
QFrame#voiceStage {
    background: #2b2d31;
    border: 1px solid #3f4147;
    border-radius: 8px;
}
QFrame#bottomBar {
    background: #232428;
    border-top: 1px solid #1e1f22;
}
QLabel#title {
    font-size: 20px;
    font-weight: 700;
}
QLabel#muted {
    color: #949ba4;
}
QLabel#ok {
    color: #23a559;
}
QLabel#warn {
    color: #f0b232;
}
QLabel#section {
    color: #949ba4;
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
}
QLabel#stageTitle {
    color: #f2f3f5;
    font-size: 28px;
    font-weight: 800;
}
QLabel#stageMemberName {
    color: #f2f3f5;
    font-size: 17px;
    font-weight: 700;
}
QLabel#channelName {
    color: #dbdee1;
    font-size: 15px;
}
QLabel#channelMeta {
    color: #949ba4;
    font-size: 12px;
}
QLabel#menuLabel {
    color: #dbdee1;
    padding: 8px 12px 4px 12px;
}
QLabel#voiceBadge {
    background: #248046;
    border-radius: 8px;
    padding: 2px 8px;
    color: #ffffff;
    font-size: 12px;
    font-weight: 700;
}
QFrame#channelRow {
    background: transparent;
    border-radius: 6px;
}
QFrame#channelRow:hover {
    background: #35373c;
}
QFrame#channelRowActive {
    background: #404249;
    border-radius: 6px;
}
QFrame#memberRow {
    background: transparent;
    border-radius: 6px;
}
QFrame#memberRow:hover {
    background: #35373c;
}
QLabel#avatar {
    background: #5865f2;
    color: white;
    border-radius: 14px;
    min-width: 28px;
    min-height: 28px;
    max-width: 28px;
    max-height: 28px;
    font-weight: 700;
}
QLabel#bigAvatar {
    background: #5865f2;
    color: white;
    border-radius: 24px;
    min-width: 48px;
    min-height: 48px;
    max-width: 48px;
    max-height: 48px;
    font-size: 18px;
    font-weight: 800;
}
QLabel#statusDot {
    color: #23a559;
    font-size: 16px;
}
QLabel#emptyText {
    color: #949ba4;
    font-size: 16px;
    padding: 28px;
}
QLabel#pillLive {
    background: #248046;
    border-radius: 10px;
    color: #ffffff;
    font-size: 11px;
    font-weight: 800;
    padding: 4px 10px;
}
QLabel#pillMuted {
    background: #4e5058;
    border-radius: 10px;
    color: #dbdee1;
    font-size: 11px;
    font-weight: 800;
    padding: 4px 10px;
}
QFrame#voiceCard {
    background: #35373c;
    border: 1px solid #404249;
    border-radius: 8px;
}
QFrame#voiceCard:hover {
    background: #3a3c42;
}
QFrame#emptyState {
    background: #2b2d31;
    border: 1px solid #3f4147;
    border-radius: 8px;
}
QScrollArea {
    background: transparent;
    border: none;
}
QScrollArea QWidget {
    background: transparent;
}
QMenu {
    background: #111214;
    color: #dbdee1;
    border: 1px solid #3f4147;
    border-radius: 6px;
    padding: 6px;
}
QMenu::item {
    padding: 8px 22px;
    border-radius: 4px;
}
QMenu::item:selected {
    background: #5865f2;
}
QCheckBox {
    spacing: 8px;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
}
QSlider::groove:horizontal {
    height: 4px;
    background: #4e5058;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #f2f3f5;
    width: 12px;
    height: 12px;
    margin: -4px 0;
    border-radius: 6px;
}
"""
