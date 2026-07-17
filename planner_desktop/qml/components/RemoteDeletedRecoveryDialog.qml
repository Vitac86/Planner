import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

// Explicit recovery choices after the linked Google master was deleted
// remotely (Phase 3.2B3A).  Opening reads only local SQLite state; the only
// queued Google operation ("Создать серию в Google заново") executes during
// the next manual sync with a new deterministic link generation.  Nothing is
// pre-selected; Enter on initial focus confirms nothing; Esc cancels.
Dialog {
    id: dialog

    property var vm
    property string seriesUid: ""
    property var recoveryData: ({})
    readonly property bool compactLayout: width < 560

    function refreshData() {
        recoveryData = vm ? vm.seriesRemoteDeletedData(seriesUid) : ({})
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
    width: Math.min(600, (parent ? parent.width : 600) - 32)
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
            text: "Серия удалена в Google Calendar"
            font.pixelSize: Theme.fontSubtitle + 1
            font.family: Theme.fontFamily
            font.weight: Font.DemiBold
            color: Theme.textPrimary
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            focus: true
            activeFocusOnTab: true
            Accessible.role: Accessible.Heading
            Accessible.name: text
        }

        Label {
            text: (dialog.recoveryData.title || "")
                  + " — локальная серия и её история сохранены. Выберите, что "
                  + "делать дальше; без вашего решения ничего не изменится."
            font.pixelSize: Theme.fontBody
            font.family: Theme.fontFamily
            color: Theme.textSecondary
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
        }

        Label {
            visible: dialog.recoveryData.reappeared === true
            text: "Внимание: мастер снова появился в Google по старому "
                  + "идентификатору. Автоматическое переподключение отключено — "
                  + "проверьте серию в Google Calendar перед решением."
            font.pixelSize: Theme.fontCaption
            font.family: Theme.fontFamily
            color: Theme.danger
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            Accessible.role: Accessible.AlertMessage
        }

        Label {
            text: "Поколение связи: " + (dialog.recoveryData.linkGeneration || 0)
                  + (dialog.recoveryData.canRecreate
                     ? " · пересоздание создаст поколение "
                       + dialog.recoveryData.nextGeneration
                     : "")
            font.pixelSize: Theme.fontCaption
            font.family: Theme.fontFamily
            color: Theme.textMuted
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
        }

        // Full-width stacked actions: the Russian labels stay readable at
        // every window size and no action is visually pre-selected.
        ColumnLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingSm

            AppButton {
                text: "Оставить локальной"
                iconName: "check"
                variant: "secondary"
                enabled: dialog.vm && !dialog.vm.busy
                Layout.fillWidth: true
                Accessible.description:
                    "Отключит мёртвую связь. Локальная серия останется активной "
                    + "и полностью локальной; история сохранится."
                onClicked: keepLocalConfirm.openFor(dialog.seriesUid)
            }
            AppButton {
                text: "Создать серию в Google заново"
                iconName: "repeat"
                variant: "secondary"
                enabled: !!dialog.recoveryData.canRecreate
                         && dialog.vm && !dialog.vm.busy
                Layout.fillWidth: true
                Accessible.description:
                    "Изменится Google: при следующей ручной синхронизации будет "
                    + "создан новый мастер с новым стабильным идентификатором."
                onClicked: recreateConfirm.openFor(dialog.seriesUid)
            }
            AppButton {
                text: "Удалить локальную серию"
                iconName: "trash"
                variant: "secondary"
                enabled: !!dialog.recoveryData.canDeleteLocal
                         && dialog.vm && !dialog.vm.busy
                Layout.fillWidth: true
                Accessible.description:
                    "Изменится Planner: определение серии будет удалено. "
                    + "Выполненная история сохранится. Google не затрагивается — "
                    + "мастер уже отсутствует."
                onClicked: deleteLocalConfirm.openFor(dialog.seriesUid)
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            AppButton {
                text: "Закрыть"
                variant: "ghost"
                onClicked: dialog.close()
            }
        }
    }

    ConfirmDialog {
        id: keepLocalConfirm
        headerText: "Оставить серию локальной?"
        message: "Связь с удалённым мастером будет отключена. Локальная серия, "
                 + "её история и исключения сохранятся."
        confirmText: "Оставить локальной"
        onConfirmed: uid => {
            if (dialog.vm.recoverRemoteDeletedKeepLocal(uid)) {
                dialog.refreshData()
                dialog.close()
            }
        }
    }
    ConfirmDialog {
        id: recreateConfirm
        headerText: "Создать серию в Google заново?"
        message: "При следующей ручной синхронизации в Google будет создан "
                 + "новый мастер (новое поколение связи с новым стабильным "
                 + "идентификатором). Повторные нажатия не создадут дубликатов."
        confirmText: "Создать заново"
        onConfirmed: uid => {
            if (dialog.vm.recoverRemoteDeletedRecreate(uid)) dialog.refreshData()
        }
    }
    ConfirmDialog {
        id: deleteLocalConfirm
        headerText: "Удалить локальную серию?"
        message: "Определение серии будет удалено локально; выполненная история "
                 + "останется. Операция в Google не выполняется — мастер уже "
                 + "удалён."
        confirmText: "Удалить локальную"
        onConfirmed: uid => {
            if (dialog.vm.deleteRemoteDeletedLocalSeries(uid)) {
                dialog.refreshData()
                dialog.close()
            }
        }
    }
}
