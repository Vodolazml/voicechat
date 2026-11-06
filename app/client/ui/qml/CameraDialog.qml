import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtMultimedia

// Pick a camera and see yourself before friends do.
Modal {
    id: dlg
    title: "Включить камеру"
    subtitle: "Так вас увидят друзья. Видео зашифровано сквозным шифрованием, как и голос."
    width: Math.min(560, parent ? parent.width - 48 : 560)
    property var cameras: []
    property string cameraId: ""
    onAboutToShow: {
        cameras = backend.cameras()
        const saved = backend.settings.camera_id || ""
        const found = cameras.find(c => c.id === saved) || cameras.find(c => c.isDefault) || cameras[0]
        cameraId = found ? found.id : ""
    }

    MediaDevices { id: devices }
    CaptureSession {
        id: session
        camera: Camera {
            id: cam
            active: dlg.visible && dlg.cameraId !== ""
            cameraDevice: {
                const chosen = dlg.cameras.find(c => c.id === dlg.cameraId)
                const list = devices.videoInputs
                return chosen && chosen.index < list.length ? list[chosen.index] : devices.defaultVideoInput
            }
        }
        videoOutput: view
    }

    Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: width * 9 / 16
        radius: 14; color: "black"; clip: true
        VideoOutput {
            id: view
            anchors.fill: parent
            fillMode: VideoOutput.PreserveAspectCrop
        }
        Column {
            anchors.centerIn: parent; spacing: 8
            visible: dlg.cameras.length === 0 || cam.error !== Camera.NoError
            Icon { anchors.horizontalCenter: parent.horizontalCenter; name: "videocam_off"; color: Theme.faint; width: 40; height: 40 }
            Text {
                text: dlg.cameras.length === 0 ? "Камера не найдена" : "Камера занята другой программой или недоступна"
                color: Theme.muted; font.family: Theme.font; font.pixelSize: 13
            }
        }
    }

    Select {
        visible: dlg.cameras.length > 1
        title: "Камера"
        current: dlg.cameraId
        options: dlg.cameras.map(c => ({value: c.id, title: c.title}))
        onPicked: (v) => dlg.cameraId = v
    }
    Text {
        visible: !backend.cameraAllowed
        Layout.fillWidth: true; wrapMode: Text.WordWrap
        text: "В этом пространстве камеры выключены в его настройках."
        color: Theme.warn; font.family: Theme.font; font.pixelSize: 12
    }
    PrimaryButton {
        Layout.fillWidth: true
        text: "Включить камеру"
        enabled: dlg.cameraId !== "" && backend.cameraAllowed && backend.inCall
        // The preview here lets go of the camera first, then the call opens it.
        onClicked: { const id = dlg.cameraId; dlg.close(); Qt.callLater(() => backend.startCamera(id)) }
    }
}
