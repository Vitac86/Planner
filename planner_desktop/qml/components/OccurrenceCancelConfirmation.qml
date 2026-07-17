import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

Dialog {
    id: dialog
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
    closePolicy: Popup.CloseOnEscape
    width: Math.min(480, (parent ? parent.width : 480) - 32)
    title: "Отменить только этот экземпляр?"

    contentItem: ColumnLayout {
        Accessible.name: dialog.title
        spacing: Theme.spacingMd
        Label {
            text: "Локальная запись останется как tombstone, а отмена будет отправлена только при ручной синхронизации. Мастер серии не изменится."
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        RowLayout {
            Layout.alignment: Qt.AlignRight
            AppButton {
                text: "Отмена"
                variant: "ghost"
                onClicked: dialog.close()
            }
            AppButton {
                text: "Отменить экземпляр"
                variant: "danger"
                onClicked: {
                    dialog.confirmed(dialog.targetUid)
                    dialog.close()
                }
            }
        }
    }
}
