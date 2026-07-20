import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Effects

import "../theme"

// Явный выбор области изменений экземпляра локальной серии.
//
// Появляется ПЕРЕД сохранением: случайного «применить ко всем будущим»
// нет — обе кнопки равноправны, Enter срабатывает только на кнопке
// с фокусом, Esc закрывает без изменений (данные не трогаются).
Dialog {
    id: dialog

    // Изменилось ли расписание/правило: «только этот» остаётся доступным
    // (экземпляр станет exception), но описания подчёркивают последствия.
    property bool scheduleChanged: false
    property bool ruleChanged: false
    property bool linkedGoogle: false
    // Phase 3.2B3C1: у чистой Planner-owned связанной серии «Этот и будущие»
    // доступен как удалённое разделение на два мастера Google.
    property bool remoteSplitEligible: false
    property bool remoteSplitPending: false
    property string remoteSplitStatusText: ""

    signal scopeChosen(string scope)

    parent: Overlay.overlay
    anchors.centerIn: parent
    modal: true
    focus: true
    width: Math.min(480, (parent ? parent.width : 480) - 48)
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

    function openForSave(scheduleChangedFlag, ruleChangedFlag, linkedGoogleFlag,
                         remoteSplitEligibleFlag, remoteSplitPendingFlag,
                         remoteSplitStatus) {
        dialog.scheduleChanged = !!scheduleChangedFlag
        dialog.ruleChanged = !!ruleChangedFlag
        dialog.linkedGoogle = !!linkedGoogleFlag
        dialog.remoteSplitEligible = !!remoteSplitEligibleFlag
        dialog.remoteSplitPending = !!remoteSplitPendingFlag
        dialog.remoteSplitStatusText = remoteSplitStatus || ""
        open()
        onlyThisButton.forceActiveFocus()
    }

    contentItem: ColumnLayout {
        spacing: Theme.spacingMd

        RowLayout {
            spacing: Theme.spacingSm
            Layout.fillWidth: true

            AppIcon { name: "repeat"; size: 18; color: Theme.accent }
            Label {
                text: "Область изменений серии"
                font.pixelSize: Theme.fontTitle
                font.family: Theme.fontFamily
                font.weight: Font.DemiBold
                color: Theme.textPrimary
                Layout.fillWidth: true
            }
        }

        Label {
            text: dialog.ruleChanged || dialog.scheduleChanged
                  ? "Вы меняете расписание или правило повторения. Выберите, "
                    + "к чему применить изменения."
                  : "Задача — экземпляр локальной повторяющейся серии. "
                    + "Выберите, к чему применить изменения."
            font.pixelSize: Theme.fontBody
            font.family: Theme.fontFamily
            color: Theme.textSecondary
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        // ---- «Только этот» ----
        AppButton {
            id: onlyThisButton
            text: "Только этот экземпляр"
            variant: "secondary"
            enabled: true
            Layout.fillWidth: true
            Accessible.description:
                "Изменится только выбранный экземпляр; он станет исключением "
                + "и не будет перезаписан при обновлении серии"
            onClicked: {
                dialog.close()
                dialog.scopeChosen("this_occurrence")
            }
        }
        Label {
            text: dialog.linkedGoogle
                  ? "Изменится только выбранный экземпляр Google; родительская серия не изменится."
                  : "Изменится только выбранный экземпляр. Он станет "
                    + "исключением: правки серии его больше не перезапишут."
            font.pixelSize: Theme.fontCaption
            font.family: Theme.fontFamily
            color: Theme.textMuted
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        // ---- «Этот и будущие» ----
        AppButton {
            id: allFutureButton
            text: "Этот и будущие"
            variant: "secondary"
            enabled: !dialog.linkedGoogle
                     || (dialog.remoteSplitEligible && !dialog.remoteSplitPending)
            Layout.fillWidth: true
            Accessible.description:
                dialog.linkedGoogle
                ? "Связанная серия Google разделится на два мастера: прошлые "
                  + "экземпляры останутся в исходном, будущие перейдут в новый"
                : "Серия разделится: прошлые экземпляры и история сохранятся, "
                  + "будущие будут созданы по новому правилу"
            onClicked: {
                dialog.close()
                dialog.scopeChosen("this_and_future")
            }
        }
        Label {
            text: {
                if (!dialog.linkedGoogle)
                    return "Серия разделится на этом экземпляре: прошлые "
                         + "экземпляры и выполненная история сохранятся, "
                         + "будущие невыполненные будут заменены по новому правилу."
                if (dialog.remoteSplitPending)
                    return "Для серии уже выполняется разделение («"
                         + dialog.remoteSplitStatusText
                         + "»): дождитесь завершения или откатите план в настройках."
                if (dialog.remoteSplitEligible)
                    return "После подтверждения и следующей ручной синхронизации "
                         + "в Google будут ДВА повторяющихся события: исходное "
                         + "(прошлые экземпляры) и новое с этого экземпляра."
                return "Удалённое разделение доступно только для "
                     + "синхронизированной Planner-owned серии без конфликтов "
                     + "и незавершённых операций."
            }
            font.pixelSize: Theme.fontCaption
            font.family: Theme.fontFamily
            color: Theme.textMuted
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        AppButton {
            id: entireSeriesButton
            text: "Вся серия"
            variant: "primary"
            Layout.fillWidth: true
            Accessible.description:
                "Изменить локальное определение серии; для связанной серии будет поставлено одно обновление мастера Google"
            onClicked: {
                dialog.close()
                dialog.scopeChosen("entire_series")
            }
        }
        Label {
            text: "Название, заметки, расписание и правило изменятся у всей "
                  + "локальной серии. Если серия связана, будет поставлено "
                  + "одно обновление мастера для следующего ручного синка."
            font.pixelSize: Theme.fontCaption
            font.family: Theme.fontFamily
            color: Theme.textMuted
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            AppButton {
                text: "Отмена (Esc)"
                variant: "ghost"
                onClicked: dialog.close()
            }
        }
    }
}
