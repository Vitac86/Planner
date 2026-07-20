import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

// Сводка плана удалённого разделения «этот и будущие» (Phase 3.2B3C1).
// Чисто отображение: данные приходят из vm.remoteSplitPreflight(...) или
// строки плана; никаких сетевых вызовов.
ColumnLayout {
    id: summary

    // Карта preflight: seriesTitle, seriesSummary, targetSlot,
    // occurrencesBeforeTarget, successorTitle, successorSummary,
    // changedFields, twoMastersWarning.
    property var info: ({})

    spacing: Theme.spacingSm

    component SummaryLine: RowLayout {
        property string label: ""
        property string value: ""
        visible: value !== ""
        spacing: Theme.spacingSm
        Layout.fillWidth: true
        Label {
            text: label
            font.pixelSize: Theme.fontCaption
            font.family: Theme.fontFamily
            color: Theme.textMuted
            Layout.preferredWidth: 190
            wrapMode: Text.WordWrap
        }
        Label {
            text: value
            font.pixelSize: Theme.fontBody
            font.family: Theme.fontFamily
            color: Theme.textPrimary
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
        }
    }

    SummaryLine {
        label: "Исходная серия"
        value: (summary.info && summary.info.seriesTitle) ? summary.info.seriesTitle : ""
    }
    SummaryLine {
        label: "Правило исходной серии"
        value: (summary.info && summary.info.seriesSummary) ? summary.info.seriesSummary : ""
    }
    SummaryLine {
        label: "Целевой исходный слот"
        value: (summary.info && summary.info.targetSlot) ? summary.info.targetSlot : ""
    }
    SummaryLine {
        label: "Экземпляров останется в исходной"
        value: (summary.info && summary.info.occurrencesBeforeTarget !== undefined)
               ? String(summary.info.occurrencesBeforeTarget) : ""
    }
    SummaryLine {
        label: "Серия-преемник"
        value: (summary.info && summary.info.successorTitle) ? summary.info.successorTitle : ""
    }
    SummaryLine {
        label: "Правило преемника"
        value: (summary.info && summary.info.successorSummary) ? summary.info.successorSummary : ""
    }
    SummaryLine {
        label: "Изменяется с целевого экземпляра"
        value: (summary.info && summary.info.changedFields
                && summary.info.changedFields.length > 0)
               ? summary.info.changedFields.join(", ")
               : ((summary.info && summary.info.successorTitle) ? "ничего (та же серия)" : "")
    }

    Label {
        visible: !!(summary.info && summary.info.twoMastersWarning)
        text: (summary.info && summary.info.twoMastersWarning) ? summary.info.twoMastersWarning : ""
        font.pixelSize: Theme.fontCaption
        font.family: Theme.fontFamily
        color: Theme.warningText
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    }
}
