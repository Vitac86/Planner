import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

// Side-by-side Planner vs Google comparison of one conflicted series.
// Pure presentation: data arrives as plain maps from the ViewModel; the
// component never touches repositories or gateways.
ColumnLayout {
    id: comparison

    property var localData: ({})
    property var remoteData: ({})
    property bool compact: false

    spacing: Theme.spacingSm

    function minutesText(minutes) {
        return minutes && minutes > 0 ? (minutes + " мин") : "—"
    }

    function rowsModel() {
        var local = localData || {}
        var remote = remoteData || {}
        return [
            { name: "Название", local: local.title || "—",
              remote: remote.title || "—" },
            { name: "Заметки", local: local.notesPresent ? "есть" : "нет",
              remote: remote.notesPresent ? "есть" : "нет" },
            { name: "Форма", local: local.formText || "—",
              remote: remote.formText || "—" },
            { name: "Дата начала", local: local.startDate || "—",
              remote: remote.startDate || "—" },
            { name: "Время начала", local: local.startTime || "—",
              remote: remote.startTime || "—" },
            { name: "Длительность",
              local: minutesText(local.durationMinutes),
              remote: minutesText(remote.durationMinutes) },
            { name: "Часовой пояс", local: local.timezone || "—",
              remote: remote.timezone || "—" },
            { name: "Повторение", local: local.ruleSummary || "—",
              remote: remote.ruleSummary || "—" },
            { name: "Версия",
              local: "ревизия " + (local.revision !== undefined
                                   ? local.revision : "—"),
              remote: remote.updatedAt
                      ? ("обновлено " + remote.updatedAt) : "—" }
        ]
    }

    // Flattened cell list keeps the GridLayout ordering deterministic in
    // both the three-column normal form and the stacked compact form.
    function cellsModel() {
        var rows = rowsModel()
        var cells = []
        if (!compact) {
            cells.push({ text: "", kind: "name" })
            cells.push({ text: "Planner (локальная)", kind: "header" })
            cells.push({ text: "Google (удалённая)", kind: "header" })
        }
        for (var i = 0; i < rows.length; i++) {
            cells.push({ text: rows[i].name, kind: "name" })
            if (compact) {
                cells.push({
                    text: "Planner: " + rows[i].local
                          + " · Google: " + rows[i].remote,
                    kind: "value"
                })
            } else {
                cells.push({ text: rows[i].local, kind: "value" })
                cells.push({ text: rows[i].remote, kind: "value" })
            }
        }
        return cells
    }

    GridLayout {
        Layout.fillWidth: true
        columns: comparison.compact ? 1 : 3
        columnSpacing: Theme.spacingMd
        rowSpacing: comparison.compact ? 2 : Theme.spacingXs

        Repeater {
            model: comparison.cellsModel()
            delegate: Label {
                required property var modelData
                text: modelData.text
                font.pixelSize: Theme.fontCaption
                font.family: Theme.fontFamily
                font.weight: modelData.kind === "value"
                             ? Font.Normal : Font.DemiBold
                color: modelData.kind === "name"
                       ? Theme.textSecondary : Theme.textPrimary
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
                Accessible.name: modelData.kind === "name"
                                 ? ("Поле: " + modelData.text)
                                 : modelData.text
            }
        }
    }

    // Support status is written out, never colour-only.
    Label {
        visible: (comparison.remoteData || {}).available === true
        text: (comparison.remoteData || {}).supported
              ? "Правило Google поддерживается Planner без потерь."
              : ("Правило Google не поддерживается: "
                 + ((comparison.remoteData || {}).unsupportedReason
                    || "причина неизвестна"))
        font.pixelSize: Theme.fontCaption
        font.family: Theme.fontFamily
        color: (comparison.remoteData || {}).supported
               ? Theme.textSecondary : Theme.danger
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
        Accessible.role: Accessible.StaticText
        Accessible.name: text
    }

    ColumnLayout {
        Layout.fillWidth: true
        spacing: 2
        visible: ((comparison.remoteData || {}).rawRecurrence || []).length > 0
                 && !((comparison.remoteData || {}).supported)
        Label {
            text: "Исходные строки повторения Google:"
            font.pixelSize: Theme.fontCaption
            font.family: Theme.fontFamily
            font.weight: Font.DemiBold
            color: Theme.textSecondary
        }
        Repeater {
            model: (comparison.remoteData || {}).rawRecurrence || []
            delegate: Label {
                required property var modelData
                text: modelData
                font.pixelSize: Theme.fontCaption
                font.family: "Consolas"
                color: Theme.textPrimary
                wrapMode: Text.WrapAnywhere
                Layout.fillWidth: true
            }
        }
    }
}
