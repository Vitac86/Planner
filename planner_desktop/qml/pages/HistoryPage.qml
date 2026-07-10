import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../components"
import "../theme"

Item {
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.spacingXl
        spacing: Theme.spacingLg

        PageHeader {
            title: "История"
            subtitle: "Журнал выполненных задач"
            Layout.fillWidth: true
        }

        Panel {
            Layout.fillWidth: true
            Layout.fillHeight: true

            EmptyState {
                anchors.centerIn: parent
                glyph: "🕘"
                text: "История выполненных задач появится здесь"
                hint: "Страница ещё не реализована (следующая фаза)"
            }
        }
    }
}
