import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

Dialog {
    id: dialog
    property var conflictData: ({})
    signal useGoogleRequested(int changeId)
    signal keepPlannerRequested(int changeId)
    signal keepBothRequested(int changeId)

    parent: Overlay.overlay
    anchors.centerIn: parent
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape
    width: Math.min(680, (parent ? parent.width : 680) - 32)
    title: "Конфликт экземпляра серии Google"

    contentItem: ColumnLayout {
        Accessible.name: dialog.title
        spacing: Theme.spacingMd
        OccurrenceConflictComparison {
            Layout.fillWidth: true
            localData: dialog.conflictData.local || ({})
            googleData: dialog.conflictData.google || ({})
        }
        Label {
            visible: !!dialog.conflictData.useGoogleDisabledReason
            text: dialog.conflictData.useGoogleDisabledReason || ""
            color: Theme.warningText
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        RowLayout {
            Layout.fillWidth: true
            AppButton {
                text: "Использовать Google"
                enabled: dialog.conflictData.canUseGoogle !== false
                onClicked: {
                    dialog.useGoogleRequested(dialog.conflictData.id || 0)
                    dialog.close()
                }
            }
            AppButton {
                text: "Оставить Planner"
                variant: "secondary"
                onClicked: dialog.keepPlannerRequested(
                    dialog.conflictData.id || 0)
            }
            AppButton {
                text: "Оставить оба локально"
                variant: "secondary"
                onClicked: {
                    dialog.keepBothRequested(dialog.conflictData.id || 0)
                    dialog.close()
                }
            }
            Item { Layout.fillWidth: true }
            AppButton {
                text: "Пока не решать"
                variant: "ghost"
                onClicked: dialog.close()
            }
        }
    }
}
