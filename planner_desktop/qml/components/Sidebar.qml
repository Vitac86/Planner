import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: sidebar
    implicitWidth: 224
    color: "#FFFFFF"

    property int currentIndex: 0
    signal pageSelected(int index)

    // тонкая линия-разделитель справа
    Rectangle {
        anchors.right: parent.right
        width: 1
        height: parent.height
        color: "#E6E8F0"
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 4

        Label {
            text: "Planner"
            font.pixelSize: 22
            font.weight: Font.DemiBold
            color: "#23283D"
            Layout.bottomMargin: 2
        }
        Label {
            text: "экспериментальная версия"
            font.pixelSize: 11
            color: "#8A90A6"
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
                radius: 10
                color: sidebar.currentIndex === index ? "#EEF1FE"
                     : navMouse.containsMouse ? "#F6F7FB" : "transparent"

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 12
                    anchors.rightMargin: 12
                    spacing: 10

                    Label { text: modelData.icon; font.pixelSize: 15 }
                    Label {
                        text: modelData.label
                        font.pixelSize: 14
                        font.weight: sidebar.currentIndex === index
                                     ? Font.DemiBold : Font.Normal
                        color: sidebar.currentIndex === index
                               ? "#4F6BED" : "#3C4257"
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
            text: "Фейковые данные,\nбез синхронизации"
            font.pixelSize: 11
            color: "#A6ABBF"
            lineHeight: 1.2
        }
    }
}
