import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

ColumnLayout {
    id: panel
    property var vm
    property var settingsModel
    visible: !!vm && vm.hasErrors
    spacing: Theme.spacingMd

    Label {
        text: "Не удалось синхронизировать"
        font.pixelSize: Theme.fontBody
        font.family: Theme.fontFamily
        font.bold: true
        color: Theme.textPrimary
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    }

    Label {
        visible: text.length > 0
        text: panel.vm ? panel.vm.cycleErrorReason : ""
        font.pixelSize: Theme.fontBody
        font.family: Theme.fontFamily
        color: Theme.textSecondary
        textFormat: Text.PlainText
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    }

    AppButton {
        text: "Подключить Google заново"
        visible: !!panel.vm && panel.vm.reconnectNeeded
        enabled: !!panel.settingsModel && panel.settingsModel.connectEnabled
        onClicked: panel.settingsModel.connectGoogle()
    }

    Label {
        visible: !!panel.vm && panel.vm.reconnectNeeded
                 && !!panel.settingsModel && !panel.settingsModel.hasClientSecret
        text: "Сначала добавьте файл подключения Google — путь указан в разделе подключения."
        font.pixelSize: Theme.fontCaption
        font.family: Theme.fontFamily
        color: Theme.textSecondary
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    }

    Label {
        visible: text.length > 0
        text: panel.vm ? panel.vm.errorText : ""
        color: Theme.danger
        font.pixelSize: Theme.fontBody
        font.family: Theme.fontFamily
        textFormat: Text.PlainText
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    }

    Repeater {
        model: panel.vm ? panel.vm.rows : []
        delegate: Rectangle {
            id: errorCard
            required property var modelData
            Layout.fillWidth: true
            implicitHeight: cardContent.implicitHeight + 2 * Theme.spacingMd
            radius: Theme.radiusSmall
            color: Theme.surface
            border.color: Theme.border

            ColumnLayout {
                id: cardContent
                anchors.fill: parent
                anchors.margins: Theme.spacingMd
                spacing: Theme.spacingSm

                Label {
                    text: errorCard.modelData.title
                    font.pixelSize: Theme.fontBody
                    font.family: Theme.fontFamily
                    font.bold: true
                    color: Theme.textPrimary
                    textFormat: Text.PlainText
                    wrapMode: Text.WrapAnywhere
                    Layout.fillWidth: true
                }
                Label {
                    text: errorCard.modelData.action + " · " + errorCard.modelData.statusText
                    font.pixelSize: Theme.fontCaption
                    font.family: Theme.fontFamily
                    color: Theme.textMuted
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
                Label {
                    visible: text.length > 0
                    text: errorCard.modelData.taskState
                    font.pixelSize: Theme.fontCaption
                    font.family: Theme.fontFamily
                    color: Theme.textMuted
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
                Label {
                    text: errorCard.modelData.reason + " " + errorCard.modelData.hint
                    font.pixelSize: Theme.fontBody
                    font.family: Theme.fontFamily
                    color: Theme.textSecondary
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }

                Flow {
                    Layout.fillWidth: true
                    spacing: Theme.spacingSm

                    AppButton {
                        text: "Открыть задачу"
                        visible: errorCard.modelData.canOpen
                        onClicked: panel.vm.openTask(errorCard.modelData.taskUid)
                    }
                    AppButton {
                        text: "Повторить отправку"
                        visible: errorCard.modelData.canRetry
                        enabled: !!panel.vm && !panel.vm.busy
                                 && !!panel.settingsModel && panel.settingsModel.manualSyncEnabled
                        onClicked: panel.vm.retryOperation(errorCard.modelData.operationId)
                    }
                }
            }
        }
    }

    Label {
        text: "Ошибки повторяющихся серий и их экземпляров разбираются "
              + "в соответствующих разделах настроек. Удаления в Google остаются в очереди, "
              + "пока не завершатся безопасно."
        font.pixelSize: Theme.fontCaption
        font.family: Theme.fontFamily
        color: Theme.textMuted
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    }
}
