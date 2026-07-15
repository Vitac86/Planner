import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Effects

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
    width: 400
    padding: Theme.spacingXl
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

    enter: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 0.0; to: 1.0; duration: 150 }
            NumberAnimation { property: "scale"; from: 0.96; to: 1.0; duration: 180; easing.type: Easing.OutCubic }
        }
    }
    exit: Transition {
        NumberAnimation { property: "opacity"; from: 1.0; to: 0.0; duration: 110 }
    }

    Overlay.modal: Rectangle {
        color: Qt.rgba(0.09, 0.10, 0.16, 0.42)
    }

    background: Rectangle {
        radius: Theme.radiusLarge
        color: Theme.surface
        border.color: Theme.border
        border.width: 1
        layer.enabled: true
        layer.effect: MultiEffect {
            shadowEnabled: true
            shadowColor: Theme.shadowColor
            blurMax: Theme.shadowBlurMax
            shadowBlur: Theme.elevDialogBlur
            shadowVerticalOffset: Theme.elevDialogY
            shadowOpacity: Theme.elevDialogOpacity
            autoPaddingEnabled: true
        }
    }

    contentItem: ColumnLayout {
        spacing: Theme.spacingMd

        RowLayout {
            spacing: Theme.spacingMd
            Layout.fillWidth: true

            Rectangle {
                implicitWidth: 40
                implicitHeight: 40
                radius: Theme.radiusSmall + 2
                color: Theme.dangerSoft
                Layout.alignment: Qt.AlignTop
                AppIcon {
                    anchors.centerIn: parent
                    name: "trash"
                    color: Theme.danger
                    size: 20
                }
            }

            ColumnLayout {
                spacing: 4
                Layout.fillWidth: true

                Label {
                    text: dialog.headerText
                    font.pixelSize: Theme.fontSubtitle + 1
                    font.family: Theme.fontFamily
                    font.weight: Font.DemiBold
                    color: Theme.textPrimary
                }
                Label {
                    text: dialog.message
                    font.pixelSize: Theme.fontBody
                    font.family: Theme.fontFamily
                    color: Theme.textSecondary
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
            }
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
                iconName: "trash"
                onClicked: {
                    dialog.close()
                    dialog.confirmed(dialog.targetUid)
                }
            }
        }
    }
}
