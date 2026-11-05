import QtQuick

// Slowly drifting colour glow behind the sign-in card.
Rectangle {
    id: root
    color: Theme.bg
    clip: true

    component Blob: Canvas {
        id: blob
        property color tint: Theme.accent
        width: 720; height: 720
        opacity: 0.55
        onTintChanged: requestPaint()
        onPaint: {
            var ctx = getContext("2d");
            ctx.reset();
            var g = ctx.createRadialGradient(width / 2, height / 2, 0, width / 2, height / 2, width / 2);
            g.addColorStop(0, Qt.rgba(tint.r, tint.g, tint.b, 0.55));
            g.addColorStop(1, Qt.rgba(tint.r, tint.g, tint.b, 0));
            ctx.fillStyle = g;
            ctx.fillRect(0, 0, width, height);
        }
    }

    Blob {
        tint: Theme.accent
        x: root.width * 0.05 - 260; y: root.height * 0.1 - 300
        SequentialAnimation on x { loops: Animation.Infinite
            NumberAnimation { to: root.width * 0.25 - 260; duration: 14000; easing.type: Easing.InOutSine }
            NumberAnimation { to: root.width * 0.05 - 260; duration: 14000; easing.type: Easing.InOutSine } }
    }
    Blob {
        tint: "#ff3d9a"
        x: root.width * 0.65 - 360; y: root.height * 0.55 - 360
        opacity: 0.4
        SequentialAnimation on y { loops: Animation.Infinite
            NumberAnimation { to: root.height * 0.35 - 360; duration: 17000; easing.type: Easing.InOutSine }
            NumberAnimation { to: root.height * 0.55 - 360; duration: 17000; easing.type: Easing.InOutSine } }
    }
    Blob {
        tint: "#22c3e6"
        x: root.width * 0.85 - 360; y: -260
        opacity: 0.3
        SequentialAnimation on x { loops: Animation.Infinite
            NumberAnimation { to: root.width * 0.6 - 360; duration: 19000; easing.type: Easing.InOutSine }
            NumberAnimation { to: root.width * 0.85 - 360; duration: 19000; easing.type: Easing.InOutSine } }
    }
}
