import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

// Компактный индикатор состояния одного плана удалённого разделения.
// planData — строка из settingsVm.remoteSplitRows.
RowLayout {
    id: progress

    property var planData: ({})

    readonly property string planState: (planData && planData.state) ? planData.state : ""

    spacing: Theme.spacingSm

    Badge {
        text: (progress.planData && progress.planData.statusText)
              ? progress.planData.statusText : "—"
        bg: {
            switch (progress.planState) {
            case "completed": return Theme.successSoft
            case "rolled_back": return Theme.successSoft
            case "conflict": return Theme.dangerSoft
            case "terminal_error": return Theme.dangerSoft
            default: return Theme.accentSoft
            }
        }
        fg: {
            switch (progress.planState) {
            case "completed": return Theme.success
            case "rolled_back": return Theme.success
            case "conflict": return Theme.danger
            case "terminal_error": return Theme.danger
            default: return Theme.accent
            }
        }
    }

    Label {
        visible: !!(progress.planData && progress.planData.attempts > 0)
        text: "попыток: " + ((progress.planData && progress.planData.attempts)
                            ? progress.planData.attempts : 0)
        font.pixelSize: Theme.fontCaption
        font.family: Theme.fontFamily
        color: Theme.textMuted
    }

    Label {
        visible: !!(progress.planData && progress.planData.lastError)
        text: (progress.planData && progress.planData.lastError)
              ? progress.planData.lastError : ""
        font.pixelSize: Theme.fontCaption
        font.family: Theme.fontFamily
        color: Theme.danger
        elide: Text.ElideRight
        Layout.fillWidth: true
    }

    Item { Layout.fillWidth: true; visible: !(progress.planData && progress.planData.lastError) }
}
