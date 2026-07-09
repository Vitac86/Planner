import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

// Пустое состояние списка: крупный символ + приглушённый текст.
ColumnLayout {
    id: empty

    property string glyph: "🗒"
    property string text: "Здесь пока пусто"
    property string hint: ""

    spacing: Theme.spacingXs

    Label {
        text: empty.glyph
        font.pixelSize: 30
        Layout.alignment: Qt.AlignHCenter
        opacity: 0.6
    }
    Label {
        text: empty.text
        font.pixelSize: Theme.fontBody
        color: Theme.textMuted
        Layout.alignment: Qt.AlignHCenter
    }
    Label {
        visible: empty.hint.length > 0
        text: empty.hint
        font.pixelSize: Theme.fontCaption
        color: Theme.textMuted
        opacity: 0.8
        Layout.alignment: Qt.AlignHCenter
    }
}
