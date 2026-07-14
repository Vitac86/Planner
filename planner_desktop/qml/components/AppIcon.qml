import QtQuick

// Монохромные линейные иконки одного стиля (единый штрих, скруглённые
// концы). Рисуются на Canvas в сетке 24×24 — без картинок-ассетов и
// без цветных эмодзи, поэтому весь UI выглядит согласованно.
Item {
    id: icon

    property string name: ""
    property color color: "#333A4D"
    property real size: 20
    property real strokeWidth: 1.9

    implicitWidth: size
    implicitHeight: size

    onColorChanged: canvas.requestPaint()
    onNameChanged: canvas.requestPaint()
    onSizeChanged: canvas.requestPaint()
    onStrokeWidthChanged: canvas.requestPaint()

    Canvas {
        id: canvas
        anchors.fill: parent
        antialiasing: true
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()

        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            var s = Math.min(width, height) / 24
            ctx.lineWidth = icon.strokeWidth
            ctx.strokeStyle = icon.color
            ctx.fillStyle = icon.color
            ctx.lineCap = "round"
            ctx.lineJoin = "round"

            function p(v) { return v * s }
            function line(x1, y1, x2, y2) {
                ctx.beginPath(); ctx.moveTo(p(x1), p(y1)); ctx.lineTo(p(x2), p(y2)); ctx.stroke()
            }
            function poly(pts, close) {
                ctx.beginPath(); ctx.moveTo(p(pts[0][0]), p(pts[0][1]))
                for (var i = 1; i < pts.length; i++) ctx.lineTo(p(pts[i][0]), p(pts[i][1]))
                if (close) ctx.closePath()
                ctx.stroke()
            }
            function circle(cx, cy, r) {
                ctx.beginPath(); ctx.arc(p(cx), p(cy), p(r), 0, 2 * Math.PI); ctx.stroke()
            }
            function dot(cx, cy, r) {
                ctx.beginPath(); ctx.arc(p(cx), p(cy), p(r), 0, 2 * Math.PI); ctx.fill()
            }
            function arc(cx, cy, r, a0, a1) {
                ctx.beginPath(); ctx.arc(p(cx), p(cy), p(r), a0, a1); ctx.stroke()
            }
            function rr(x, y, w, h, r) {
                var x0 = p(x), y0 = p(y), x1 = p(x + w), y1 = p(y + h), rr0 = p(r)
                ctx.beginPath()
                ctx.moveTo(x0 + rr0, y0)
                ctx.arcTo(x1, y0, x1, y1, rr0)
                ctx.arcTo(x1, y1, x0, y1, rr0)
                ctx.arcTo(x0, y1, x0, y0, rr0)
                ctx.arcTo(x0, y0, x1, y0, rr0)
                ctx.closePath()
                ctx.stroke()
            }

            var n = icon.name
            if (n === "today" || n === "sun") {
                circle(12, 12, 4.1)
                var rays = [[12, 2.2, 12, 4.2], [12, 19.8, 12, 21.8],
                            [2.2, 12, 4.2, 12], [19.8, 12, 21.8, 12],
                            [5.2, 5.2, 6.6, 6.6], [17.4, 17.4, 18.8, 18.8],
                            [5.2, 18.8, 6.6, 17.4], [17.4, 6.6, 18.8, 5.2]]
                for (var r0 = 0; r0 < rays.length; r0++)
                    line(rays[r0][0], rays[r0][1], rays[r0][2], rays[r0][3])
            } else if (n === "calendar") {
                rr(3.2, 5, 17.6, 16, 2.6)
                line(3.2, 9.2, 20.8, 9.2)
                line(8, 3, 8, 6.4); line(16, 3, 16, 6.4)
            } else if (n === "history") {
                arc(12, 12, 8, Math.PI * 0.65, Math.PI * 2.55)
                poly([[4.1, 6.2], [4.1, 9.6], [7.5, 9.6]], false)
                line(12, 7.6, 12, 12); line(12, 12, 15.4, 13.6)
            } else if (n === "settings" || n === "sliders") {
                line(3.5, 7, 20.5, 7); line(3.5, 12, 20.5, 12); line(3.5, 17, 20.5, 17)
                dot(9, 7, 2.4); dot(15.5, 12, 2.4); dot(8, 17, 2.4)
            } else if (n === "plus") {
                line(12, 5.5, 12, 18.5); line(5.5, 12, 18.5, 12)
            } else if (n === "check") {
                poly([[5, 12.6], [9.8, 17.4], [19, 7]], false)
            } else if (n === "edit" || n === "pencil") {
                poly([[4, 20], [5, 15.8], [16.3, 4.5], [19.5, 7.7], [8.2, 19], [4, 20]], false)
                line(14.6, 6.2, 17.8, 9.4)
            } else if (n === "trash") {
                line(3.6, 6, 20.4, 6)
                poly([[9, 6], [9, 3.8], [15, 3.8], [15, 6]], false)
                poly([[5.6, 6], [6.6, 20.4], [17.4, 20.4], [18.4, 6]], false)
                line(10, 9.6, 10, 17); line(14, 9.6, 14, 17)
            } else if (n === "chevron-left") {
                poly([[14.5, 5], [8.5, 12], [14.5, 19]], false)
            } else if (n === "chevron-right") {
                poly([[9.5, 5], [15.5, 12], [9.5, 19]], false)
            } else if (n === "chevron-down") {
                poly([[6, 9.5], [12, 15.5], [18, 9.5]], false)
            } else if (n === "inbox") {
                poly([[3.2, 13], [6.4, 4.8], [17.6, 4.8], [20.8, 13]], false)
                rr(3.2, 13, 17.6, 6.6, 2.4)
                poly([[3.2, 13], [8, 13], [9.6, 15.6], [14.4, 15.6], [16, 13], [20.8, 13]], false)
            } else if (n === "clock") {
                circle(12, 12, 8)
                line(12, 7.4, 12, 12); line(12, 12, 15.6, 13.6)
            } else if (n === "refresh") {
                arc(12, 12, 7, Math.PI * 1.15, Math.PI * 2.65)
                poly([[17.6, 6.4], [18.7, 9.1], [15.9, 9.6]], false)
                arc(12, 12, 7, Math.PI * 0.15, Math.PI * 1.65)
                poly([[6.4, 17.6], [5.3, 14.9], [8.1, 14.4]], false)
            } else if (n === "sparkle") {
                ctx.beginPath()
                for (var k = 0; k < 8; k++) {
                    var ang = (-90 + k * 45) * Math.PI / 180
                    var rad = (k % 2 === 0) ? 9 : 2.7
                    var px = p(12 + rad * Math.cos(ang))
                    var py = p(12 + rad * Math.sin(ang))
                    if (k === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py)
                }
                ctx.closePath(); ctx.fill()
            } else if (n === "info") {
                circle(12, 12, 8.2)
                dot(12, 8, 1.05); line(12, 11.4, 12, 16.4)
            } else if (n === "flag") {
                line(6, 21, 6, 4)
                poly([[6, 4.5], [17.5, 4.5], [14.8, 8], [17.5, 11.5], [6, 11.5]], false)
            } else if (n === "close" || n === "x") {
                line(6.5, 6.5, 17.5, 17.5); line(17.5, 6.5, 6.5, 17.5)
            } else if (n === "link") {
                arc(9.6, 12, 4.4, Math.PI * 0.45, Math.PI * 1.55)
                arc(14.4, 12, 4.4, Math.PI * 1.45, Math.PI * 2.55)
                line(9.2, 12, 14.8, 12)
            } else if (n === "circle") {
                circle(12, 12, 8)
            } else if (n === "note") {
                rr(4.5, 3.5, 15, 17, 2.6)
                line(8, 9, 16, 9); line(8, 12.5, 16, 12.5); line(8, 16, 13, 16)
            } else if (n === "search") {
                circle(10.5, 10.5, 6.2)
                line(15.2, 15.2, 20, 20)
            } else if (n === "snooze") {
                // будильник-циферблат со стрелками «отложить»
                circle(12, 13, 7.4)
                line(12, 9.4, 12, 13); line(12, 13, 15, 14.4)
                line(5.4, 5.8, 8, 3.6); line(18.6, 5.8, 16, 3.6)
            } else if (n === "chevron-up") {
                poly([[6, 14.5], [12, 8.5], [18, 14.5]], false)
            }
        }
    }
}
