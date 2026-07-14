import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

Rectangle {
    id: chip
    property string name: ""
    property bool removable: false
    property bool selected: false
    property bool compact: false
    signal clicked(string name)
    signal removeRequested(string name)

    implicitHeight: compact ? 26 : 30
    implicitWidth: row.implicitWidth + (compact ? 14 : 18)
    radius: Theme.radiusPill
    color: selected ? Theme.accentSoft : Theme.surfacePressed
    border.color: selected ? Theme.accentSoftBorder : Theme.border
    border.width: 1
    activeFocusOnTab: true

    Accessible.role: Accessible.Button
    Accessible.name: "Тег: " + name
    Accessible.description: removable
        ? "Нажмите, чтобы выбрать; отдельная кнопка удаляет тег из задачи"
        : "Нажмите, чтобы отфильтровать задачи по тегу"
    Accessible.focusable: true

    RowLayout {
        id: row
        anchors.centerIn: parent
        spacing: Theme.spacingXs
        Label {
            text: chip.name
            font.pixelSize: Theme.fontCaption
            font.family: Theme.fontFamily
            font.weight: Font.Medium
            color: chip.selected ? Theme.accent : Theme.textSecondary
            elide: Text.ElideRight
        }
        IconButton {
            visible: chip.removable
            implicitWidth: 24
            implicitHeight: 24
            iconName: "close"
            glyphSize: 10
            tip: "Убрать тег «" + chip.name + "»"
            Accessible.name: "Убрать тег «" + chip.name + "»"
            onClicked: chip.removeRequested(chip.name)
        }
    }

    TapHandler {
        onTapped: {
            chip.forceActiveFocus()
            chip.clicked(chip.name)
        }
    }
    Keys.onReturnPressed: chip.clicked(chip.name)
    Keys.onSpacePressed: chip.clicked(chip.name)

    Rectangle {
        anchors.fill: parent
        anchors.margins: -2
        radius: parent.radius
        color: "transparent"
        border.color: Theme.focusRing
        border.width: 2
        visible: chip.activeFocus
    }
}
