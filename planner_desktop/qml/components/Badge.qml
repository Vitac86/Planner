import QtQuick
import QtQuick.Controls

import "../theme"

// Маленькая пилюля со статусом («Весь день», «Синк…», счётчик и т.п.).
Rectangle {
    id: badge

    property string text: ""
    property color fg: Theme.textSecondary
    property color bg: Theme.surfaceHover
    property color borderColor: bg  // по умолчанию рамка сливается с фоном
    property int fontSize: Theme.fontCaption

    radius: height / 2
    color: bg
    border.color: borderColor
    border.width: 1
    implicitHeight: 22
    implicitWidth: label.implicitWidth + 18
    visible: text.length > 0

    Label {
        id: label
        anchors.centerIn: parent
        text: badge.text
        font.pixelSize: badge.fontSize
        font.family: Theme.fontFamily
        font.weight: Font.Medium
        color: badge.fg
    }
}
