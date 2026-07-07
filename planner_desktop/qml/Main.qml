import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

import "components"
import "pages"

ApplicationWindow {
    id: root
    visible: true
    width: 1200
    height: 780
    minimumWidth: 900
    minimumHeight: 620
    title: "Planner — экспериментальный десктоп (PySide6/QML, фейковые данные)"

    Material.theme: Material.Light
    Material.accent: "#4F6BED"
    Material.primary: "#4F6BED"
    color: "#F4F5FA"

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
}
