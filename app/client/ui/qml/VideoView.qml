import QtQuick
import QtMultimedia
import VoiceChat

// `VideoView { userId: 5 }` shows that person's stream, letterboxed. Qt draws it on the graphics card:
// the frame goes up as YUV, and colour conversion and scaling happen there, not on the processor.
Item {
    id: view
    property int userId: 0
    readonly property bool hasFrame: feed.hasFrame

    VideoOutput {
        id: output
        anchors.fill: parent
        fillMode: VideoOutput.PreserveAspectFit
    }

    VideoFeed {
        id: feed
        userId: view.userId
        videoSink: output.videoSink
    }
}
