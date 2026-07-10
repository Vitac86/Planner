import QtQuick
import QtQuick.Controls.Basic as B

import "../theme"

// Однострочное поле ввода дизайн-системы: скруглённый фон, тонкая рамка,
// акцентная рамка в фокусе. На Basic-стиле, чтобы placeholder вёл себя
// предсказуемо (по центру, исчезает при вводе) внутри рамочного бокса —
// без «плавающей» подписи Material, которая обрезалась о верхнюю рамку.
B.TextField {
    id: field

    font.pixelSize: Theme.fontBody
    font.family: Theme.fontFamily
    color: Theme.textPrimary
    placeholderTextColor: Theme.textMuted
    selectionColor: Theme.accentSoft
    selectedTextColor: Theme.textPrimary
    selectByMouse: true
    hoverEnabled: true
    leftPadding: 12
    rightPadding: 12
    topPadding: 9
    bottomPadding: 9

    background: Rectangle {
        radius: Theme.radiusSmall
        color: field.enabled ? Theme.surface : Theme.surfaceMuted
        border.color: field.activeFocus ? Theme.accent
                    : field.hovered ? Theme.borderStrong : Theme.border
        border.width: field.activeFocus ? 1.6 : 1
        Behavior on border.color { ColorAnimation { duration: 100 } }
    }
}
