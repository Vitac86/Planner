import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

// Подтверждение необратимого действия (удаление задачи).
Dialog {
    id: dialog

    property string headerText: "Удалить задачу?"
    property string message: "Действие нельзя отменить."
    property string confirmText: "Удалить"
    property string targetUid: ""

    signal confirmed(string uid)

    function openFor(uid) {
        targetUid = uid
        open()
    }

    parent: Overlay.overlay
    anchors.centerIn: parent
    modal: true
    focus: true
    width: 380
    padding: Theme.spacingXl
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

    background: Rectangle {
        radius: Theme.radiusLarge
        color: Theme.surface
        border.color: Theme.border
        border.width: 1
    }

    contentItem: ColumnLayout {
        spacing: Theme.spacingMd

        Label {
            text: dialog.headerText
            font.pixelSize: Theme.fontSubtitle
            font.weight: Font.DemiBold
            color: Theme.textPrimary
        }
        Label {
            text: dialog.message
            font.pixelSize: Theme.fontBody
            color: Theme.textSecondary
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        RowLayout {
            spacing: Theme.spacingSm
            Layout.topMargin: Theme.spacingXs
            Layout.fillWidth: true

            Item { Layout.fillWidth: true }
            AppButton {
                text: "Отмена"
                variant: "ghost"
                onClicked: dialog.close()
            }
            AppButton {
                text: dialog.confirmText
                variant: "danger"
                onClicked: {
                    dialog.close()
                    dialog.confirmed(dialog.targetUid)
                }
            }
        }
    }
}
