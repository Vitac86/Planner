import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

// Explicit resolution of one Planner-owned Google master conflict
// (Phase 3.2B3A).  Opening the dialog reads only local SQLite state; the
// only Google write ("Оставить версию Planner") happens later, inside the
// next manual sync.  No action is pre-selected and Enter on the initial
// focus never confirms anything; Esc cancels.
Dialog {
    id: dialog

    property var vm
    property string seriesUid: ""
    property var conflictData: ({})
    readonly property bool compactLayout: width < 620

    function refreshData() {
        conflictData = vm ? vm.seriesConflictData(seriesUid) : ({})
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
    width: Math.min(760, (parent ? parent.width : 760) - 32)
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

        // Initial focus lands on the heading, not on any action button, so
        // pressing Enter right after opening cannot confirm anything.
        Label {
            id: conflictTitle
            text: "Конфликт серии с Google Calendar"
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
            visible: (dialog.conflictData.conflictReason || "").length > 0
            text: dialog.conflictData.conflictReason || ""
            font.pixelSize: Theme.fontCaption
            font.family: Theme.fontFamily
            color: Theme.textSecondary
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            Accessible.role: Accessible.StaticText
            Accessible.name: "Причина конфликта: " + text
        }

        Label {
            text: dialog.conflictData.ownershipText || ""
            visible: (dialog.conflictData.ownershipText || "").length > 0
            font.pixelSize: Theme.fontCaption
            font.family: Theme.fontFamily
            color: dialog.conflictData.ownershipOk === true
                   ? Theme.textSecondary : Theme.danger
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            Accessible.role: Accessible.StaticText
            Accessible.name: "Владение мастером: " + text
        }

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: comparisonColumn.implicitHeight + 2 * Theme.spacingMd
            radius: Theme.radiusSmall
            color: Theme.surfaceMuted
            border.color: Theme.border
            border.width: 1

            ColumnLayout {
                id: comparisonColumn
                anchors.fill: parent
                anchors.margins: Theme.spacingMd
                SeriesConflictComparison {
                    Layout.fillWidth: true
                    localData: dialog.conflictData.local || ({})
                    remoteData: dialog.conflictData.remote || ({})
                    compact: dialog.compactLayout
                }
            }
        }

        Label {
            visible: (dialog.conflictData.pendingResolutionText || "").length > 0
            text: "Ожидает ручной синхронизации: "
                  + (dialog.conflictData.pendingResolutionText || "")
            font.pixelSize: Theme.fontCaption
            font.family: Theme.fontFamily
            color: Theme.accent
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            Accessible.role: Accessible.StaticText
            Accessible.name: text
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingXs
            visible: !dialog.conflictData.canUseGoogle
                     && ((dialog.conflictData.useGoogleErrors || []).length > 0)
            Label {
                text: "«Использовать версию Google» недоступно:"
                font.pixelSize: Theme.fontCaption
                font.family: Theme.fontFamily
                font.weight: Font.DemiBold
                color: Theme.danger
            }
            Repeater {
                model: dialog.conflictData.useGoogleErrors || []
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

        GridLayout {
            Layout.fillWidth: true
            columns: dialog.compactLayout ? 1 : 3
            columnSpacing: Theme.spacingSm
            rowSpacing: Theme.spacingSm

            AppButton {
                text: "Оставить версию Planner"
                iconName: "repeat"
                variant: "secondary"
                enabled: !!dialog.conflictData.canKeepPlanner
                         && dialog.vm && !dialog.vm.busy
                Layout.fillWidth: true
                Accessible.description:
                    "Перезапишет мастер Google текущей локальной серией при "
                    + "следующей ручной синхронизации. Локальная серия не меняется."
                onClicked: keepPlannerConfirm.openFor(dialog.seriesUid)
            }
            AppButton {
                text: "Использовать версию Google"
                iconName: "repeat"
                variant: "secondary"
                enabled: !!dialog.conflictData.canUseGoogle
                         && dialog.vm && !dialog.vm.busy
                Layout.fillWidth: true
                Accessible.description:
                    "Заменит определение локальной серии версией Google без "
                    + "сети. Google не меняется; выполненная история сохраняется."
                onClicked: useGoogleConfirm.openFor(dialog.seriesUid)
            }
            AppButton {
                text: "Отключить и сохранить обе"
                iconName: "close"
                variant: "secondary"
                enabled: !!dialog.conflictData.canDisconnect
                         && dialog.vm && !dialog.vm.busy
                Layout.fillWidth: true
                Accessible.description:
                    "Разорвёт связь. Ни локальная серия, ни мастер Google "
                    + "не изменятся."
                onClicked: disconnectConfirm.openFor(dialog.seriesUid)
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
        id: keepPlannerConfirm
        headerText: "Оставить версию Planner?"
        message: "Изменится серия Google: её мастер будет перезаписан текущей "
                 + "локальной серией при следующей ручной синхронизации. Если "
                 + "Google изменится ещё раз до синхронизации, перезапись "
                 + "остановится и потребуется новое решение."
        confirmText: "Оставить версию Planner"
        onConfirmed: uid => {
            if (dialog.vm.resolveConflictKeepPlanner(uid)) dialog.refreshData()
        }
    }
    ConfirmDialog {
        id: useGoogleConfirm
        objectName: "useGoogleConfirmDialog"
        headerText: "Использовать версию Google?"
        message: "Изменится локальная серия: название, расписание и правило "
                 + "будут заменены версией Google. Выполненная история, прошлые "
                 + "исключения и удалённые слоты сохранятся; теги останутся "
                 + "локальными. Google Calendar не изменится."
        confirmText: "Использовать версию Google"
        onConfirmed: uid => {
            if (dialog.vm.resolveConflictUseGoogle(uid)) dialog.refreshData()
        }
    }
    ConfirmDialog {
        id: disconnectConfirm
        headerText: "Отключить и сохранить обе версии?"
        message: "Связь будет разорвана. Локальная серия останется локальной, "
                 + "мастер Google останется без изменений. История связи "
                 + "сохранится в диагностике."
        confirmText: "Отключить"
        onConfirmed: uid => {
            if (dialog.vm.resolveConflictDisconnect(uid)) {
                dialog.refreshData()
                dialog.close()
            }
        }
    }
}
