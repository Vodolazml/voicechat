import QtQuick

Item {
    id: root
    property int size: 24
    property color color: "white"
    width: size; height: size
    Canvas {
        anchors.fill: parent
        onPaint: {
            var ctx = getContext("2d");
            ctx.reset();
            ctx.lineWidth = Math.max(2, root.size / 9);
            ctx.lineCap = "round";
            ctx.strokeStyle = root.color;
            ctx.beginPath();
            ctx.arc(width / 2, height / 2, width / 2 - ctx.lineWidth, 0, Math.PI * 1.4);
            ctx.stroke();
        }
        RotationAnimation on rotation { from: 0; to: 360; duration: 800; loops: Animation.Infinite; running: root.visible }
    }
}
