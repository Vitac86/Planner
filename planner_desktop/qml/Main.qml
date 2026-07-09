import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

import "components"
import "pages"
import "theme"

ApplicationWindow {
    id: root
    visible: true
    width: 1200
    height: 780
    minimumWidth: 900
    minimumHeight: 620
    title: "Planner — экспериментальный десктоп (PySide6/QML)"

    Material.theme: Material.Light
    Material.accent: Theme.accent
    Material.primary: Theme.accent
    color: Theme.background

    property int currentPage: 0

    RowLayout {
        anchors.fill: parent
        spacing: 0

        Sidebar {
            Layout.fillHeight: true
            currentIndex: root.currentPage
            onPageSelected: index => root.currentPage = index
        }

        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: root.currentPage

            TodayPage {}
            CalendarPage {}
            HistoryPage {}
            SettingsPage {}
        }
    }

    // ---- всплывашка «Сохранено»/«Удалено» ----
    Rectangle {
        id: toast

        property string message: ""
        function show(text) {
            message = text
            toastTimer.restart()
        }

        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 28
        radius: height / 2
        color: "#2A2F40"
        implicitHeight: 36
        implicitWidth: toastLabel.implicitWidth + 44
        opacity: toastTimer.running ? 0.96 : 0
        visible: opacity > 0
        z: 900

        Behavior on opacity { NumberAnimation { duration: 160 } }

        Label {
            id: toastLabel
            anchors.centerIn: parent
            text: toast.message
            color: "#FFFFFF"
            font.pixelSize: 13
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
