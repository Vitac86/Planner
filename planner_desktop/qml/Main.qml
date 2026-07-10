import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts
import QtQuick.Effects

import "components"
import "pages"
import "theme"

ApplicationWindow {
    id: root
    visible: true
    width: 1240
    height: 800
    minimumWidth: 900
    minimumHeight: 620
    title: "Planner — экспериментальный десктоп (PySide6/QML)"

    Material.theme: Material.Light
    Material.accent: Theme.accent
    Material.primary: Theme.accent
    color: Theme.background
    font.family: Theme.fontFamily

    property int currentPage: 0

    RowLayout {
        anchors.fill: parent
        spacing: 0

        Sidebar {
            Layout.fillHeight: true
            currentIndex: root.currentPage
            onPageSelected: index => root.currentPage = index
        }

        // Область контента с мягким вертикальным градиентом фона.
        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            Rectangle {
                anchors.fill: parent
                gradient: Gradient {
                    GradientStop { position: 0.0; color: Theme.background }
                    GradientStop { position: 1.0; color: Theme.backgroundAlt }
                }
            }

            StackLayout {
                anchors.fill: parent
                currentIndex: root.currentPage

                TodayPage {}
                CalendarPage {}
                HistoryPage {}
                SettingsPage {}
            }
        }
    }

    // ---- всплывашка «Сохранено»/«Удалено» ----
    Item {
        id: toast

        property string message: ""
        property string iconName: "check"
        property color iconColor: Theme.success

        function show(text) {
            message = text
            if (text.indexOf("далена") >= 0 || text.indexOf("далён") >= 0) {
                iconName = "trash"; iconColor = "#FF9B9B"
            } else {
                iconName = "check"; iconColor = "#7CE6A6"
            }
            toastTimer.restart()
        }

        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: toastTimer.running ? 30 : 18
        width: toastBg.width
        height: toastBg.height
        opacity: toastTimer.running ? 1.0 : 0
        visible: opacity > 0
        z: 900

        Behavior on opacity { NumberAnimation { duration: 180 } }
        Behavior on anchors.bottomMargin { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }

        Rectangle {
            id: toastBg
            radius: height / 2
            color: Theme.scrim
            implicitHeight: 40
            implicitWidth: toastRow.implicitWidth + 34

            layer.enabled: true
            layer.effect: MultiEffect {
                shadowEnabled: true
                shadowColor: Theme.shadowColor
                blurMax: Theme.shadowBlurMax
                shadowBlur: Theme.elevDialogBlur
                shadowVerticalOffset: 8
                shadowOpacity: 0.34
                autoPaddingEnabled: true
            }

            RowLayout {
                id: toastRow
                anchors.centerIn: parent
                spacing: Theme.spacingSm

                AppIcon {
                    name: toast.iconName
                    color: toast.iconColor
                    size: 17
                }
                Label {
                    text: toast.message
                    color: "#FFFFFF"
                    font.pixelSize: 13
                    font.family: Theme.fontFamily
                    font.weight: Font.Medium
                }
            }
        }

        Timer {
            id: toastTimer
            interval: 2200
        }
    }

    Connections {
        target: todayVm
        function onToastMessage(text) { toast.show(text) }
    }
    Connections {
        target: calendarVm
        function onToastMessage(text) { toast.show(text) }
    }
}
