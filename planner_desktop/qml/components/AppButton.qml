import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

// Кнопка дизайн-системы. variant: "primary" | "secondary" | "ghost" | "danger".
// iconName (необязательно) рисует линейную иконку перед текстом.
// loading блокирует кнопку и показывает многоточие вместо текста.
Button {
    id: control

    property string variant: "secondary"
    property bool loading: false
    property string iconName: ""

    readonly property bool _solid: variant === "primary" || variant === "danger"
    readonly property color _fg: _solid ? Theme.textOnAccent
                                : variant === "ghost" ? Theme.accent : Theme.textPrimary

    enabled: !loading
    hoverEnabled: true
    font.pixelSize: Theme.fontBody
    font.family: Theme.fontFamily
    font.weight: _solid ? Font.DemiBold : Font.Medium
    leftPadding: 15
    rightPadding: 15
    topPadding: 9
    bottomPadding: 9

    HoverHandler { cursorShape: Qt.PointingHandCursor }

    contentItem: RowLayout {
        spacing: 6
        opacity: control.enabled ? 1.0 : 0.6

        AppIcon {
            visible: control.iconName.length > 0 && !control.loading
            name: control.iconName
            color: control._fg
            size: 16
            Layout.alignment: Qt.AlignVCenter
        }
        Label {
            text: control.loading ? "…" : control.text
            font: control.font
            color: control._fg
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            Layout.alignment: Qt.AlignVCenter
        }
    }

    background: Rectangle {
        implicitHeight: 36
        radius: Theme.radiusSmall
        opacity: control.enabled ? 1.0 : 0.55
        color: {
            if (control.variant === "primary")
                return control.down ? Theme.accentPressed
                     : control.hovered ? Theme.accentHover : Theme.accent
            if (control.variant === "danger")
                return control.down ? Theme.dangerHover
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

        // фокус-обводка для доступности с клавиатуры
        Rectangle {
            anchors.fill: parent
            anchors.margins: -3
            radius: parent.radius + 3
            color: "transparent"
            border.color: Theme.focusRing
            border.width: 2
            visible: control.visualFocus
        }
    }
}
