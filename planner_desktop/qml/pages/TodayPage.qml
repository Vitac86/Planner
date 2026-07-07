import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../components"

ScrollView {
    id: page
    contentWidth: availableWidth
    clip: true

    ColumnLayout {
        width: Math.min(page.availableWidth - 48, 860)
        x: 24
        spacing: 18

        Item { implicitHeight: 6 }

        Label {
            text: "Сегодня"
            font.pixelSize: 26
            font.weight: Font.DemiBold
            color: "#23283D"
        }

        // ---- Ежедневные задачи ----
        Label {
            text: "Ежедневные задачи"
            font.pixelSize: 15
            font.weight: Font.DemiBold
            color: "#5A6072"
        }
        Flow {
            spacing: 8
            Layout.fillWidth: true

            Repeater {
                model: todayVm.dailyTasks
                delegate: Rectangle {
                    required property var modelData
                    radius: 16
                    implicitHeight: 32
                    implicitWidth: dailyRow.implicitWidth + 24
                    color: modelData.done ? "#EAF6EE" : "#FFFFFF"
                    border.color: modelData.done ? "#BFE3CC" : "#E6E8F0"
                    border.width: 1

                    Row {
                        id: dailyRow
                        anchors.centerIn: parent
                        spacing: 6
                        Label {
                            text: modelData.done ? "✓" : "○"
                            color: modelData.done ? "#2E7D46" : "#A6ABBF"
                            font.pixelSize: 13
                        }
                        Label {
                            text: modelData.title
                            font.pixelSize: 13
                            color: modelData.done ? "#2E7D46" : "#3C4257"
                        }
                    }
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: todayVm.toggleDaily(modelData.title)
                    }
                }
            }
        }

        // ---- Быстрое добавление ----
        Label {
            text: "Быстрое добавление"
            font.pixelSize: 15
            font.weight: Font.DemiBold
            color: "#5A6072"
        }
        QuickAdd { Layout.fillWidth: true }

        // ---- Задачи на сегодня ----
        Label {
            text: "Задачи на сегодня"
            font.pixelSize: 15
            font.weight: Font.DemiBold
            color: "#5A6072"
        }
        ColumnLayout {
            spacing: 8
            Layout.fillWidth: true

            Repeater {
                model: todayVm.todayTasks
                delegate: TaskCard {
                    required property var modelData
                    Layout.fillWidth: true
                    uid: modelData.uid
                    title: modelData.title
                    notes: modelData.notes
                    timeLabel: modelData.timeLabel
                    isAllDay: modelData.isAllDay
                    priority: modelData.priority
                    completed: modelData.completed
                    onToggled: uid => todayVm.toggleCompleted(uid)
                }
            }
            Label {
                visible: todayVm.todayTasks.length === 0
                text: "На сегодня задач нет."
                color: "#8A90A6"
                font.pixelSize: 13
            }
        }

        // ---- Без даты ----
        Label {
            text: "Без даты"
            font.pixelSize: 15
            font.weight: Font.DemiBold
            color: "#5A6072"
        }
        ColumnLayout {
            spacing: 8
            Layout.fillWidth: true

            Repeater {
                model: todayVm.undatedTasks
                delegate: TaskCard {
                    required property var modelData
                    Layout.fillWidth: true
                    uid: modelData.uid
                    title: modelData.title
                    notes: modelData.notes
                    timeLabel: modelData.timeLabel
                    isAllDay: modelData.isAllDay
                    priority: modelData.priority
                    completed: modelData.completed
                    onToggled: uid => todayVm.toggleCompleted(uid)
                }
            }
            Label {
                visible: todayVm.undatedTasks.length === 0
                text: "Задач без даты нет."
                color: "#8A90A6"
                font.pixelSize: 13
            }
        }

        Item { implicitHeight: 24 }
    }
}
