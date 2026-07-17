import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

GridLayout {
    id: comparison
    property var localData: ({})
    property var googleData: ({})
    columns: width < 560 ? 1 : 2
    columnSpacing: Theme.spacingMd
    rowSpacing: Theme.spacingMd

    Panel {
        Layout.fillWidth: true
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: Theme.spacingMd
            Label { text: "Planner"; font.weight: Font.DemiBold }
            Label {
                text: comparison.localData.title || "—"
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            Label {
                text: comparison.localData.schedule || ""
                color: Theme.textSecondary
            }
        }
    }
    Panel {
        Layout.fillWidth: true
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: Theme.spacingMd
            Label { text: "Google"; font.weight: Font.DemiBold }
            Label {
                text: comparison.googleData.title || "—"
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            Label {
                text: comparison.googleData.schedule || ""
                color: Theme.textSecondary
            }
        }
    }
}
