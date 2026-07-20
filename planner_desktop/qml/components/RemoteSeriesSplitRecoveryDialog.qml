import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Effects

import "../theme"

// Восстановление плана удалённого разделения: повтор, отмена до удалённых
// шагов и ЯВНЫЙ откат после частичного выполнения. Все кнопки — локальные
// операции; удалённые шаги отката выполняет следующая ручная синхронизация.
Dialog {
    id: dialog

    // settingsVm (слоты retryRemoteSplit/rollbackRemoteSplit/cancelRemoteSplit).
    property var vm: null
    property var planData: ({})

    parent: Overlay.overlay
    anchors.centerIn: parent
    modal: true
    focus: true
    width: Math.min(560, (parent ? parent.width : 560) - 48)
    padding: Theme.spacingXl
    closePolicy: Popup.CloseOnEscape

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

    function openFor(plan) {
        dialog.planData = plan || {}
        open()
        closeButton.forceActiveFocus()
    }

    contentItem: ColumnLayout {
        spacing: Theme.spacingMd

        RowLayout {
            spacing: Theme.spacingSm
            Layout.fillWidth: true
            AppIcon { name: "info"; size: 18; color: Theme.warningText }
            Label {
                text: "План разделения серии"
                font.pixelSize: Theme.fontTitle
                font.family: Theme.fontFamily
                font.weight: Font.DemiBold
                color: Theme.textPrimary
                Layout.fillWidth: true
            }
        }

        Label {
            text: (dialog.planData && dialog.planData.seriesTitle)
                  ? dialog.planData.seriesTitle : ""
            visible: text !== ""
            font.pixelSize: Theme.fontBody
            font.family: Theme.fontFamily
            color: Theme.textPrimary
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        Label {
            text: "Целевой слот: " + ((dialog.planData && dialog.planData.targetSlot)
                                      ? dialog.planData.targetSlot : "—")
            font.pixelSize: Theme.fontCaption
            font.family: Theme.fontFamily
            color: Theme.textMuted
            Layout.fillWidth: true
        }

        RemoteSeriesSplitProgress {
            Layout.fillWidth: true
            planData: dialog.planData
        }

        Label {
            visible: !!(dialog.planData && dialog.planData.lastError)
            text: (dialog.planData && dialog.planData.lastError)
                  ? dialog.planData.lastError : ""
            font.pixelSize: Theme.fontCaption
            font.family: Theme.fontFamily
            color: Theme.danger
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        Label {
            text: "Повтор выполняется при следующей ручной синхронизации. "
                  + "Откат сначала проверяет, что оба мастера Google не "
                  + "менялись извне; изменённые мастера не перезаписываются "
                  + "и не удаляются."
            font.pixelSize: Theme.fontCaption
            font.family: Theme.fontFamily
            color: Theme.textMuted
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingSm

            AppButton {
                objectName: "remoteSplitRetryButton"
                text: "Повторить"
                variant: "secondary"
                enabled: !!(dialog.planData && dialog.planData.canRetry)
                onClicked: {
                    if (dialog.vm && dialog.vm.retryRemoteSplit(dialog.planData.id))
                        dialog.close()
                }
            }
            AppButton {
                objectName: "remoteSplitCancelButton"
                text: "Отменить план"
                variant: "secondary"
                enabled: !!(dialog.planData && dialog.planData.canCancel)
                Accessible.description:
                    "Доступно только до каких-либо удалённых шагов; ноль вызовов Google"
                onClicked: {
                    if (dialog.vm && dialog.vm.cancelRemoteSplit(dialog.planData.id))
                        dialog.close()
                }
            }
            AppButton {
                objectName: "remoteSplitRollbackButton"
                text: "Откатить разделение"
                variant: "danger"
                enabled: !!(dialog.planData && dialog.planData.canRollback)
                Accessible.description:
                    "Явный откат частично выполненного разделения при следующей ручной синхронизации"
                onClicked: {
                    if (dialog.vm && dialog.vm.rollbackRemoteSplit(dialog.planData.id))
                        dialog.close()
                }
            }
            Item { Layout.fillWidth: true }
            AppButton {
                id: closeButton
                text: "Закрыть (Esc)"
                variant: "ghost"
                onClicked: dialog.close()
            }
        }
    }
}
