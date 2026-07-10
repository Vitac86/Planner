import QtQuick

import "../theme"

// Кольцевой индикатор прогресса дня (выполнено / всего). Рисуется на
// Canvas: серый трек + акцентная дуга со скруглёнными концами.
Item {
    id: ring

    property int value: 0
    property int total: 0
    property color barColor: Theme.accent
    property color trackColor: Theme.ringTrack
    property real thickness: 8
    property real fraction: total > 0 ? Math.max(0, Math.min(1, value / total)) : 0

    implicitWidth: 120
    implicitHeight: 120

    onValueChanged: canvas.requestPaint()
    onTotalChanged: canvas.requestPaint()
    onBarColorChanged: canvas.requestPaint()

    Canvas {
        id: canvas
        anchors.fill: parent
        antialiasing: true
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()

        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            var cx = width / 2, cy = height / 2
            var r = Math.min(width, height) / 2 - ring.thickness / 2 - 1
            var start = -Math.PI / 2

            ctx.lineWidth = ring.thickness
            ctx.lineCap = "round"

            ctx.strokeStyle = ring.trackColor
            ctx.beginPath(); ctx.arc(cx, cy, r, 0, 2 * Math.PI); ctx.stroke()

            if (ring.fraction > 0) {
                ctx.strokeStyle = ring.barColor
                ctx.beginPath()
                ctx.arc(cx, cy, r, start, start + 2 * Math.PI * ring.fraction)
                ctx.stroke()
            }
        }
    }
}
