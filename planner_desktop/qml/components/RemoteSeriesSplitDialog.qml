import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Effects

import "../theme"

// Подтверждение удалённого разделения связанной серии «этот и будущие»
// (Phase 3.2B3C1). Открытие диалога выполняет ТОЛЬКО локальный preflight
// (vm.remoteSplitPreflight) — ноль вызовов Google. Подтверждение создаёт
// durable-план; сетевые шаги выполняет следующая ручная синхронизация.
// Деструктивное действие НЕ выбрано по умолчанию: фокус на «Отмена».
Dialog {
    id: dialog

    property var vm: null
    property string taskUid: ""
    property var payload: ({})
    property var preflight: ({})

    signal planCreated()

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

    function openFor(uid, formPayload) {
        dialog.taskUid = uid
        dialog.payload = formPayload || {}
        dialog.preflight = dialog.vm
            ? dialog.vm.remoteSplitPreflight(uid, dialog.payload)
            : ({ ok: false, errors: ["ViewModel недоступна."] })
        open()
        cancelButton.forceActiveFocus()
    }

    contentItem: ColumnLayout {
        spacing: Theme.spacingMd

        RowLayout {
            spacing: Theme.spacingSm
            Layout.fillWidth: true
            AppIcon { name: "repeat"; size: 18; color: Theme.accent }
            Label {
                text: "Разделить серию Google с этого экземпляра"
                font.pixelSize: Theme.fontTitle
                font.family: Theme.fontFamily
                font.weight: Font.DemiBold
                color: Theme.textPrimary
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
            }
        }

        RemoteSeriesSplitSummary {
            Layout.fillWidth: true
            info: dialog.preflight
        }

        // Preflight будущих исключений: точные причины блокировки; ничего
        // не удаляется и не сбрасывается автоматически.
        ColumnLayout {
            visible: !!(dialog.preflight && dialog.preflight.errors
                        && dialog.preflight.errors.length > 0)
            spacing: Theme.spacingXs
            Layout.fillWidth: true
            Label {
                text: dialog.preflight && dialog.preflight.routeToEntireSeries
                      ? "Разделение недоступно: выбран первый экземпляр."
                      : "Разделение заблокировано:"
                font.pixelSize: Theme.fontBody
                font.family: Theme.fontFamily
                font.weight: Font.DemiBold
                color: Theme.danger
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
            }
            Repeater {
                model: (dialog.preflight && dialog.preflight.errors)
                       ? dialog.preflight.errors : []
                delegate: Label {
                    required property string modelData
                    text: "• " + modelData
                    font.pixelSize: Theme.fontCaption
                    font.family: Theme.fontFamily
                    color: Theme.textSecondary
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
            }
            Label {
                visible: !!(dialog.preflight && dialog.preflight.routeToEntireSeries)
                text: "Для первого экземпляра используйте «Вся серия» — "
                      + "два мастера Google не создаются."
                font.pixelSize: Theme.fontCaption
                font.family: Theme.fontFamily
                color: Theme.textMuted
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
        }

        Label {
            text: "Сетевые действия произойдут только при следующей ручной "
                  + "синхронизации. До этого план можно отменить без "
                  + "обращений к Google."
            font.pixelSize: Theme.fontCaption
            font.family: Theme.fontFamily
            color: Theme.textMuted
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingSm
            Item { Layout.fillWidth: true }
            AppButton {
                id: cancelButton
                text: "Отмена (Esc)"
                variant: "ghost"
                onClicked: dialog.close()
            }
            AppButton {
                id: confirmButton
                objectName: "remoteSplitConfirmButton"
                text: "Создать план разделения"
                variant: "primary"
                enabled: !!(dialog.preflight && dialog.preflight.ok)
                Accessible.description:
                    "Создаёт локальный план: исходная серия Google будет "
                    + "сокращена, появится второй мастер-преемник"
                onClicked: {
                    if (!dialog.vm)
                        return
                    if (dialog.vm.createRemoteSplitPlan(dialog.taskUid, dialog.payload)) {
                        dialog.close()
                        dialog.planCreated()
                    }
                }
            }
        }
    }
}
