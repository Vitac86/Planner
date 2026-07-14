import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

// Пустое состояние списка: крупный символ + приглушённый текст + подсказка
// и необязательная кнопка действия («Создать первую задачу»), чтобы пустой
// экран помогал начать, а не выглядел заглушкой.
ColumnLayout {
    id: empty

    // iconName — линейная иконка AppIcon (единая иконографика);
    // glyph оставлен для обратной совместимости, но не используется,
    // если задан iconName.
    property string iconName: "note"
    property string glyph: ""
    property string text: "Здесь пока пусто"
    property string hint: ""
    property string actionText: ""
    property string actionIcon: "plus"

    signal actionClicked()

    spacing: Theme.spacingXs

    Rectangle {
        visible: empty.glyph.length === 0
        implicitWidth: 52
        implicitHeight: 52
        radius: height / 2
        color: Theme.surfaceMuted
        border.color: Theme.border
        border.width: 1
        Layout.alignment: Qt.AlignHCenter
        Layout.bottomMargin: Theme.spacingXs

        AppIcon {
            anchors.centerIn: parent
            name: empty.iconName
            size: 24
            color: Theme.textMuted
        }
    }
    Label {
        visible: empty.glyph.length > 0
        text: empty.glyph
        font.pixelSize: 30
        Layout.alignment: Qt.AlignHCenter
        opacity: 0.6
    }
    Label {
        text: empty.text
        font.pixelSize: Theme.fontBody
        font.family: Theme.fontFamily
        color: Theme.textMuted
        Layout.alignment: Qt.AlignHCenter
    }
    Label {
        visible: empty.hint.length > 0
        text: empty.hint
        font.pixelSize: Theme.fontCaption
        font.family: Theme.fontFamily
        color: Theme.textMuted
        opacity: 0.8
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.WordWrap
        Layout.maximumWidth: 320
        Layout.alignment: Qt.AlignHCenter
    }
    AppButton {
        visible: empty.actionText.length > 0
        text: empty.actionText
        variant: "primary"
        iconName: empty.actionIcon
        Layout.alignment: Qt.AlignHCenter
        Layout.topMargin: Theme.spacingSm
        onClicked: empty.actionClicked()
    }
}
