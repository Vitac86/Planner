import QtQuick
import QtQuick.Controls

import "../theme"

// Кнопка дизайн-системы. variant: "primary" | "secondary" | "ghost" | "danger".
// loading блокирует кнопку и показывает многоточие вместо текста.
Button {
    id: control

    property string variant: "secondary"
    property bool loading: false

    enabled: !loading
    hoverEnabled: true
    font.pixelSize: Theme.fontBody
    leftPadding: 14
    rightPadding: 14
    topPadding: 8
    bottomPadding: 8

    HoverHandler { cursorShape: Qt.PointingHandCursor }

    contentItem: Label {
        text: control.loading ? "…" : control.text
        font: control.font
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        color: control.variant === "primary" || control.variant === "danger"
               ? Theme.textOnAccent
               : control.variant === "ghost" ? Theme.accent : Theme.textPrimary
        opacity: control.enabled ? 1.0 : 0.6
    }

    background: Rectangle {
        implicitHeight: 34
        radius: Theme.radiusSmall
        opacity: control.enabled ? 1.0 : 0.55
        color: {
            if (control.variant === "primary")
                return control.down ? Theme.accentPressed
                     : control.hovered ? Theme.accentHover : Theme.accent
            if (control.variant === "danger")
                return control.down ? "#AE262D"
                     : control.hovered ? Theme.dangerHover : Theme.danger
            if (control.variant === "ghost")
                return control.down ? Theme.accentSoft
                     : control.hovered ? Theme.surfaceHover : "transparent"
            return control.down ? Theme.surfacePressed
                 : control.hovered ? Theme.surfaceHover : Theme.surface
        }
        border.color: control.variant === "secondary" ? Theme.borderStrong : "transparent"
        border.width: control.variant === "secondary" ? 1 : 0

        Behavior on color { ColorAnimation { duration: 90 } }
    }
}
