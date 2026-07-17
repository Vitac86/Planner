import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

RowLayout {
    id: status
    property string statusText: ""
    property string statusCode: ""
    spacing: Theme.spacingXs
    Accessible.name: statusText

    AppIcon {
        name: status.statusCode === "conflict"
              || status.statusCode === "remote_changed"
              || status.statusCode === "remote_cancelled"
              ? "warning" : "sync"
        size: 14
        color: status.statusCode === "conflict"
               || status.statusCode === "remote_changed"
               || status.statusCode === "remote_cancelled"
               ? Theme.warningText : Theme.textSecondary
    }
    Label {
        text: status.statusText
        color: Theme.textSecondary
        font.pixelSize: Theme.fontCaption
        font.family: Theme.fontFamily
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    }
}
