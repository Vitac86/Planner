import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

Panel {
    id: row
    property var occurrence: ({})
    signal resolveRequested(var occurrence)
    implicitHeight: content.implicitHeight + 2 * Theme.spacingMd
    Accessible.name: (occurrence.title || "Экземпляр")
                     + ", " + (occurrence.status || "")

    RowLayout {
        id: content
        anchors.fill: parent
        anchors.margins: Theme.spacingMd
        spacing: Theme.spacingMd

        ColumnLayout {
            Layout.fillWidth: true
            Label {
                text: row.occurrence.title || "Экземпляр без названия"
                font.weight: Font.DemiBold
                elide: Text.ElideRight
                Layout.fillWidth: true
            }
            Label {
                text: (row.occurrence.occurrenceKey || "")
                      + (row.occurrence.remoteCancelled ? " · Отменён в Google"
                                                       : " · Изменён в Google")
                color: Theme.textSecondary
                font.pixelSize: Theme.fontCaption
            }
        }
        AppButton {
            text: "Разрешить"
            variant: "secondary"
            Accessible.name: "Разрешить конфликт экземпляра"
            onClicked: row.resolveRequested(row.occurrence)
        }
    }
}
