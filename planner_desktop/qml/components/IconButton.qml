import QtQuick
import QtQuick.Controls

import "../theme"

// Круглая иконка-кнопка с текстовым глифом (без картинок-ассетов).
AbstractButton {
    id: control

    property string glyph: "✕"
    property color glyphColor: Theme.textSecondary
    property color hoverGlyphColor: glyphColor
    property color hoverBg: Theme.surfacePressed
    property string tip: ""
    property int glyphSize: Theme.fontBody

    implicitWidth: 30
    implicitHeight: 30
    hoverEnabled: true

    HoverHandler { cursorShape: Qt.PointingHandCursor }

    ToolTip.visible: hovered && tip.length > 0
    ToolTip.text: tip
    ToolTip.delay: 600

    background: Rectangle {
        radius: width / 2
        color: control.down ? Qt.darker(control.hoverBg, 1.05)
             : control.hovered ? control.hoverBg : "transparent"
        Behavior on color { ColorAnimation { duration: 90 } }
    }

    contentItem: Label {
        text: control.glyph
        font.pixelSize: control.glyphSize
        color: control.hovered ? control.hoverGlyphColor : control.glyphColor
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        opacity: control.enabled ? 1.0 : 0.5
    }
}
