import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

Dialog {
    id: dialog

    property var vm
    property string seriesUid: ""
    property var linkData: ({})

    function refreshData() {
        linkData = vm ? vm.seriesGoogleLinkData(seriesUid) : ({})
    }

    function openFor(uid) {
        seriesUid = uid
        refreshData()
        open()
    }

    parent: Overlay.overlay
    anchors.centerIn: parent
    modal: true
    focus: true
    width: Math.min(560, (parent ? parent.width : 560) - 32)
    padding: Theme.spacingXl
    closePolicy: Popup.CloseOnEscape

    background: Rectangle {
        radius: Theme.radiusLarge
        color: Theme.surface
        border.color: Theme.border
        border.width: 1
    }

    contentItem: ColumnLayout {
        spacing: Theme.spacingMd

        Label {
            text: "Связь серии с Google Calendar"
            font.pixelSize: Theme.fontSubtitle + 1
            font.family: Theme.fontFamily
            font.weight: Font.DemiBold
            color: Theme.textPrimary
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
        }

        Label {
            text: "Статус: " + (dialog.linkData.statusText || "Локальная серия")
            font.pixelSize: Theme.fontBody
            font.family: Theme.fontFamily
            color: Theme.textPrimary
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            Accessible.name: text
        }

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: transferColumn.implicitHeight + 2 * Theme.spacingMd
            radius: Theme.radiusSmall
            color: Theme.surfaceMuted
            border.color: Theme.border
            border.width: 1

            ColumnLayout {
                id: transferColumn
                anchors.fill: parent
                anchors.margins: Theme.spacingMd
                spacing: Theme.spacingSm
                Label {
                    text: "Будет отправлено: " + (dialog.linkData.whatSent || "")
                    font.pixelSize: Theme.fontBody
                    font.family: Theme.fontFamily
                    color: Theme.textPrimary
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                }
                Label {
                    text: "Останется только в Planner: "
                          + (dialog.linkData.whatLocal || "")
                    font.pixelSize: Theme.fontBody
                    font.family: Theme.fontFamily
                    color: Theme.textSecondary
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            visible: (dialog.linkData.validationErrors || []).length > 0
            spacing: Theme.spacingXs
            Label {
                text: "Подключение пока недоступно:"
                font.pixelSize: Theme.fontCaption
                font.family: Theme.fontFamily
                font.weight: Font.DemiBold
                color: Theme.danger
            }
            Repeater {
                model: dialog.linkData.validationErrors || []
                delegate: Label {
                    required property var modelData
                    text: "• " + modelData
                    font.pixelSize: Theme.fontCaption
                    font.family: Theme.fontFamily
                    color: Theme.danger
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                    Accessible.role: Accessible.AlertMessage
                }
            }
        }

        Label {
            visible: (dialog.linkData.lastError || "").length > 0
            text: dialog.linkData.lastError || ""
            font.pixelSize: Theme.fontCaption
            font.family: Theme.fontFamily
            color: Theme.danger
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            Accessible.role: Accessible.AlertMessage
        }

        // Phase 3.2B3A: explicit resolution entry points.
        AppButton {
            visible: dialog.linkData.status === "conflict"
            text: "Разрешить конфликт…"
            iconName: "edit"
            variant: "primary"
            enabled: dialog.vm && !dialog.vm.busy
            Layout.fillWidth: true
            Accessible.description:
                "Откроет сравнение версий Planner и Google и явные действия."
            onClicked: conflictDialog.openFor(dialog.seriesUid)
        }
        AppButton {
            visible: dialog.linkData.status === "remote_deleted"
            text: "Восстановление после удаления в Google…"
            iconName: "edit"
            variant: "primary"
            enabled: dialog.vm && !dialog.vm.busy
            Layout.fillWidth: true
            Accessible.description:
                "Откроет явный выбор: оставить локальной, пересоздать в Google "
                + "или удалить локальную серию."
            onClicked: recoveryDialog.openFor(dialog.seriesUid)
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingSm

            AppButton {
                visible: !dialog.linkData.linked
                text: "Подключить к Google Calendar"
                iconName: "repeat"
                variant: "primary"
                enabled: !!dialog.linkData.canConnect && dialog.vm && !dialog.vm.busy
                Accessible.description:
                    "Создаёт локальную операцию. Сеть будет вызвана только ручной синхронизацией."
                onClicked: {
                    if (dialog.vm.connectSeriesToGoogle(dialog.seriesUid))
                        dialog.refreshData()
                }
            }

            AppButton {
                visible: !!dialog.linkData.linked
                text: "Действия…"
                iconName: "snooze"
                variant: "secondary"
                enabled: dialog.vm && !dialog.vm.busy
                onClicked: linkMenu.open()

                Menu {
                    id: linkMenu
                    y: parent.height
                    MenuItem {
                        text: "Отключить и оставить серию Google"
                        onTriggered: disconnectConfirm.openFor(dialog.seriesUid)
                    }
                    MenuItem {
                        text: "Удалить из Google, локальную оставить"
                        onTriggered: remoteDeleteConfirm.openFor(dialog.seriesUid)
                    }
                    MenuItem {
                        text: "Удалить локальную и Google-серию"
                        onTriggered: bothDeleteConfirm.openFor(dialog.seriesUid)
                    }
                }
            }

            Item { Layout.fillWidth: true }
            AppButton {
                text: "Закрыть"
                variant: "ghost"
                onClicked: dialog.close()
            }
        }
    }

    ConfirmDialog {
        id: disconnectConfirm
        headerText: "Отключить связь?"
        message: "Серия Google останется без изменений. Локальная серия снова станет локальной."
        confirmText: "Отключить"
        onConfirmed: uid => {
            if (dialog.vm.disconnectSeriesKeepGoogle(uid)) dialog.refreshData()
        }
    }
    ConfirmDialog {
        id: remoteDeleteConfirm
        headerText: "Удалить серию из Google?"
        message: "Удаление будет выполнено только при следующей ручной синхронизации. Локальная серия сохранится."
        confirmText: "Удалить из Google"
        onConfirmed: uid => {
            if (dialog.vm.deleteGoogleSeriesKeepLocal(uid)) dialog.refreshData()
        }
    }
    ConfirmDialog {
        id: bothDeleteConfirm
        headerText: "Удалить локальную и Google-серию?"
        message: "Google-мастер будет удалён ручной синхронизацией. Выполненная локальная история сохранится."
        confirmText: "Удалить обе"
        onConfirmed: uid => {
            if (dialog.vm.deleteLocalAndGoogleSeries(uid)) {
                dialog.close()
            }
        }
    }

    SeriesConflictDialog {
        id: conflictDialog
        objectName: "seriesConflictDialog"
        vm: dialog.vm
        onClosed: dialog.refreshData()
    }
    RemoteDeletedRecoveryDialog {
        id: recoveryDialog
        objectName: "remoteDeletedRecoveryDialog"
        vm: dialog.vm
        onClosed: dialog.refreshData()
    }
}
