import QtQuick
import QtQuick.Controls

import "../theme"

// Круглая иконка-кнопка. Предпочтительно задавать iconName (линейная
// иконка из AppIcon); glyph оставлен для обратной совместимости.
AbstractButton {
    id: control

    property string iconName: ""
    property string glyph: ""
    property color glyphColor: Theme.textMuted
    property color hoverGlyphColor: Theme.textSecondary
    property color hoverBg: Theme.surfacePressed
    property string tip: ""
    property int glyphSize: Theme.fontBody

    implicitWidth: 32
    implicitHeight: 32
    hoverEnabled: true

    HoverHandler { cursorShape: Qt.PointingHandCursor }

    ToolTip.visible: hovered && tip.length > 0
    ToolTip.text: tip
    ToolTip.delay: 500

    background: Rectangle {
        radius: width / 2
        color: control.down ? Qt.darker(control.hoverBg, 1.06)
             : control.hovered ? control.hoverBg : "transparent"
        Behavior on color { ColorAnimation { duration: 90 } }
    }

    contentItem: Item {
        AppIcon {
            anchors.centerIn: parent
            visible: control.iconName.length > 0
            name: control.iconName
            size: control.glyphSize + 3
            color: control.hovered ? control.hoverGlyphColor : control.glyphColor
            opacity: control.enabled ? 1.0 : 0.5
        }
        Label {
            anchors.centerIn: parent
            visible: control.iconName.length === 0
            text: control.glyph
            font.pixelSize: control.glyphSize
            font.family: Theme.fontFamily
            color: control.hovered ? control.hoverGlyphColor : control.glyphColor
            opacity: control.enabled ? 1.0 : 0.5
        }
    }
}
