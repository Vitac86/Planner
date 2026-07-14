import QtQuick
import QtQuick.Controls

import "../theme"

Rectangle {
    id: preview
    property var previewData: ({ visible: false, valid: false, message: "" })

    visible: previewData.visible === true
    radius: Theme.radiusSmall
    color: previewData.valid === true ? Theme.accentSoft : Theme.dangerSoft
    border.color: previewData.valid === true ? Theme.accent : Theme.danger
    border.width: 2
    opacity: 0.82
    z: 40

    Label {
        anchors.fill: parent
        anchors.margins: 5
        text: (preview.previewData.valid === true ? "✓ " : "! ")
              + (preview.previewData.message || "")
        font.pixelSize: Theme.fontCaption
        font.family: Theme.fontFamily
        font.weight: Font.DemiBold
        color: preview.previewData.valid === true ? Theme.accent : Theme.danger
        wrapMode: Text.WordWrap
        elide: Text.ElideRight
        verticalAlignment: Text.AlignVCenter
    }

    Accessible.role: Accessible.StaticText
    Accessible.name: previewData.message || "Предпросмотр переноса"
}
