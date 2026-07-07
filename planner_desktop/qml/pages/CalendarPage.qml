import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Заглушка календаря: недельная сетка + список выбранного дня.
// Будущий источник данных — Google Calendar (см. sync/calendar_contract.py).
Item {
    id: page

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 24
        spacing: 16

        Label {
            text: "Календарь"
            font.pixelSize: 26
            font.weight: Font.DemiBold
            color: "#23283D"
        }

        Rectangle {
            Layout.fillWidth: true
            radius: 10
            implicitHeight: noteLabel.implicitHeight + 20
            color: "#FFF7E6"
            border.color: "#F1DFB2"
            border.width: 1

            Label {
                id: noteLabel
                anchors.fill: parent
                anchors.margins: 10
                text: "🔄 " + calendarVm.syncSourceNote
                wrapMode: Text.WordWrap
                font.pixelSize: 12
                color: "#8A6D1F"
            }
        }

        // ---- недельная сетка (заглушка) ----
        RowLayout {
            spacing: 8
            Layout.fillWidth: true

            Repeater {
                model: calendarVm.weekDays
                delegate: Rectangle {
                    required property var modelData
                    required property int index

                    Layout.fillWidth: true
                    implicitHeight: 96
                    radius: 12
                    color: modelData.isSelected ? "#EEF1FE" : "#FFFFFF"
                    border.color: modelData.isToday ? "#4F6BED" : "#E6E8F0"
                    border.width: modelData.isToday ? 2 : 1

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 10
                        spacing: 2

                        Label {
                            text: modelData.label
                            font.pixelSize: 12
                            color: "#8A90A6"
                        }
                        Label {
                            text: modelData.dateText
                            font.pixelSize: 16
                            font.weight: Font.DemiBold
                            color: modelData.isToday ? "#4F6BED" : "#23283D"
                        }
                        Item { Layout.fillHeight: true }
                        Label {
                            text: modelData.taskCount > 0
                                  ? modelData.taskCount + " задач(и)" : "—"
                            font.pixelSize: 11
                            color: modelData.taskCount > 0 ? "#4F6BED" : "#C2C6D6"
                        }
                    }

                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: calendarVm.selectDay(index)
                    }
                }
            }
        }

        // ---- список выбранного дня (заглушка) ----
        Label {
            text: calendarVm.selectedDayTitle
            font.pixelSize: 15
            font.weight: Font.DemiBold
            color: "#5A6072"
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: 12
            color: "#FFFFFF"
            border.color: "#E6E8F0"
            border.width: 1

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 16
                spacing: 8

                Repeater {
                    model: calendarVm.selectedDayTasks
                    delegate: RowLayout {
                        required property var modelData
                        spacing: 10
                        Layout.fillWidth: true

                        Label {
                            text: modelData.timeLabel
                            font.pixelSize: 13
                            color: "#4F6BED"
                            Layout.preferredWidth: 90
                        }
                        Label {
                            text: modelData.title
                            font.pixelSize: 14
                            color: "#23283D"
                            elide: Text.ElideRight
                            Layout.fillWidth: true
                        }
                    }
                }

                Label {
                    visible: calendarVm.selectedDayTasks.length === 0
                    text: "На этот день задач нет."
                    color: "#8A90A6"
                    font.pixelSize: 13
                }

                Item { Layout.fillHeight: true }
            }
        }
    }
}
