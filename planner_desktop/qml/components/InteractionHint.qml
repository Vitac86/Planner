import QtQuick
import QtQuick.Controls

import "../theme"

Rectangle {
    id: hint
    property string text: ""
    property bool valid: true

    visible: text.length > 0
    implicitHeight: label.implicitHeight + 10
    implicitWidth: label.implicitWidth + 18
    radius: Theme.radiusSmall
    color: valid ? Theme.accentSoft : Theme.dangerSoft
    border.color: valid ? Theme.accentSoftBorder : Theme.danger
    border.width: 1

    Label {
        id: label
        anchors.fill: parent
        anchors.margins: 5
        text: (hint.valid ? "✓ " : "! ") + hint.text
        color: hint.valid ? Theme.accent : Theme.danger
        font.pixelSize: Theme.fontCaption
        font.family: Theme.fontFamily
        font.weight: Font.DemiBold
        elide: Text.ElideRight
        verticalAlignment: Text.AlignVCenter
    }

    Accessible.role: Accessible.StaticText
    Accessible.name: text
    Accessible.description: valid ? "Допустимое действие" : "Недопустимое действие"
}
