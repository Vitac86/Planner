import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

Rectangle {
    id: sidebar
    implicitWidth: 224
    color: Theme.surface

    property int currentIndex: 0
    signal pageSelected(int index)

    // тонкая линия-разделитель справа
    Rectangle {
        anchors.right: parent.right
        width: 1
        height: parent.height
        color: Theme.border
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.spacingLg
        spacing: Theme.spacingXs

        Label {
            text: "Planner"
            font.pixelSize: 22
            font.weight: Font.DemiBold
            color: Theme.textPrimary
            Layout.bottomMargin: 2
        }
        Label {
            text: "экспериментальная версия"
            font.pixelSize: 11
            color: Theme.textMuted
            Layout.bottomMargin: 18
        }

        Repeater {
            model: [
                { icon: "☀️", label: "Сегодня" },
                { icon: "📅", label: "Календарь" },
                { icon: "🕘", label: "История" },
                { icon: "⚙️", label: "Настройки" }
            ]

            delegate: Rectangle {
                required property var modelData
                required property int index

                Layout.fillWidth: true
                implicitHeight: 42
                radius: Theme.radiusSmall + 2
                color: sidebar.currentIndex === index ? Theme.accentSoft
                     : navMouse.containsMouse ? Theme.surfaceHover : "transparent"

                Behavior on color { ColorAnimation { duration: 90 } }

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: Theme.spacingMd
                    anchors.rightMargin: Theme.spacingMd
                    spacing: Theme.spacingSm + 2

                    Label { text: modelData.icon; font.pixelSize: 15 }
                    Label {
                        text: modelData.label
                        font.pixelSize: Theme.fontBody
                        font.weight: sidebar.currentIndex === index
                                     ? Font.DemiBold : Font.Normal
                        color: sidebar.currentIndex === index
                               ? Theme.accent : Theme.textSecondary
                        Layout.fillWidth: true
                    }
                }

                MouseArea {
                    id: navMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: sidebar.pageSelected(index)
                }
            }
        }

        Item { Layout.fillHeight: true }

        Label {
            text: "Изолированная локальная БД.\nСинк с Google — только вручную."
            font.pixelSize: 11
            color: Theme.textMuted
            lineHeight: 1.2
        }
    }
}
