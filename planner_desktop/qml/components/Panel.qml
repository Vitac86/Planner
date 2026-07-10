import QtQuick
import QtQuick.Effects

import "../theme"

// Базовая «карточка»-поверхность: скруглённая, с тонкой рамкой и мягкой
// тенью. Тень рисуется через layer.effect на фоновом прямоугольнике,
// поэтому вложенный контент (текст) не растеризуется в слой и остаётся
// чётким. Дочерние элементы кладутся поверх фона как обычно.
Item {
    id: panel

    property alias color: bg.color
    property alias radius: bg.radius
    property color borderColor: Theme.border
    property int borderWidth: 1
    property real elevationBlur: Theme.elevCardBlur
    property real elevationOpacity: Theme.elevCardOpacity
    property int elevationY: Theme.elevCardY

    implicitWidth: 120
    implicitHeight: 80

    Rectangle {
        id: bg
        anchors.fill: parent
        radius: Theme.radiusMedium
        color: Theme.surface
        border.color: panel.borderColor
        border.width: panel.borderWidth

        layer.enabled: true
        layer.effect: MultiEffect {
            shadowEnabled: true
            shadowColor: Theme.shadowColor
            blurMax: Theme.shadowBlurMax
            shadowBlur: panel.elevationBlur
            shadowVerticalOffset: panel.elevationY
            shadowOpacity: panel.elevationOpacity
            autoPaddingEnabled: true
        }

        Behavior on border.color { ColorAnimation { duration: 120 } }
    }
}
