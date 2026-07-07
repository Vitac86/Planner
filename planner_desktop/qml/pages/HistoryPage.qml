import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 24
        spacing: 12

        Label {
            text: "История"
            font.pixelSize: 26
            font.weight: Font.DemiBold
            color: "#23283D"
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: 12
            color: "#FFFFFF"
            border.color: "#E6E8F0"
            border.width: 1

            ColumnLayout {
                anchors.centerIn: parent
                spacing: 8

                Label {
                    text: "🕘"
                    font.pixelSize: 40
                    Layout.alignment: Qt.AlignHCenter
                }
                Label {
                    text: "История выполненных задач появится здесь"
                    font.pixelSize: 15
                    color: "#5A6072"
                    Layout.alignment: Qt.AlignHCenter
                }
                Label {
                    text: "Заглушка: в скелете страница ещё не реализована."
                    font.pixelSize: 12
                    color: "#8A90A6"
                    Layout.alignment: Qt.AlignHCenter
                }
            }
        }
    }
}
