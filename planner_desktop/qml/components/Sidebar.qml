import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

// Навигационная панель приложения: бренд-марка, пункты меню с линейными
// иконками, активным индикатором-полоской и hover-состоянием, футер.
Rectangle {
    id: sidebar
    implicitWidth: 236
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
        anchors.topMargin: Theme.spacingXl
        anchors.bottomMargin: Theme.spacingLg
        anchors.leftMargin: Theme.spacingLg
        anchors.rightMargin: Theme.spacingLg
        spacing: Theme.spacingXs

        // ---- бренд ----
        RowLayout {
            spacing: Theme.spacingSm
            Layout.leftMargin: Theme.spacingSm
            Layout.bottomMargin: Theme.spacingXl

            Rectangle {
                implicitWidth: 36
                implicitHeight: 36
                radius: Theme.radiusSmall + 2
                gradient: Gradient {
                    GradientStop { position: 0.0; color: Theme.accentGradTop }
                    GradientStop { position: 1.0; color: Theme.accentGradBottom }
                }
                AppIcon {
                    anchors.centerIn: parent
                    name: "sparkle"
                    color: Theme.textOnAccent
                    size: 20
                }
            }
            ColumnLayout {
                spacing: 0
                Label {
                    text: "Planner"
                    font.pixelSize: 19
                    font.family: Theme.fontFamily
                    font.weight: Font.DemiBold
                    color: Theme.textPrimary
                }
                Label {
                    text: "экспериментальная версия"
                    font.pixelSize: 11
                    font.family: Theme.fontFamily
                    color: Theme.textMuted
                }
            }
        }

        // ---- пункты навигации ----
        Repeater {
            model: [
                { icon: "today", label: "Сегодня" },
                { icon: "calendar", label: "Календарь" },
                { icon: "history", label: "История" },
                { icon: "settings", label: "Настройки" }
            ]

            delegate: Rectangle {
                id: navItem
                required property var modelData
                required property int index

                readonly property bool active: sidebar.currentIndex === index

                Layout.fillWidth: true
                implicitHeight: 44
                radius: Theme.radiusSmall + 2
                color: active ? Theme.accentSoft
                     : navMouse.containsMouse ? Theme.surfaceHover : "transparent"

                Behavior on color { ColorAnimation { duration: 110 } }

                // активный индикатор-полоска слева
                Rectangle {
                    anchors.left: parent.left
                    anchors.leftMargin: 3
                    anchors.verticalCenter: parent.verticalCenter
                    width: 3
                    height: navItem.active ? 20 : 0
                    radius: 2
                    color: Theme.accent
                    Behavior on height { NumberAnimation { duration: 150; easing.type: Easing.OutCubic } }
                }

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: Theme.spacingLg
                    anchors.rightMargin: Theme.spacingMd
                    spacing: Theme.spacingMd

                    AppIcon {
                        name: navItem.modelData.icon
                        size: 20
                        color: navItem.active ? Theme.accent : Theme.textSecondary
                        strokeWidth: navItem.active ? 2.1 : 1.9
                    }
                    Label {
                        text: navItem.modelData.label
                        font.pixelSize: Theme.fontBody
                        font.family: Theme.fontFamily
                        font.weight: navItem.active ? Font.DemiBold : Font.Medium
                        color: navItem.active ? Theme.accent : Theme.textSecondary
                        Layout.fillWidth: true
                    }
                }

                MouseArea {
                    id: navMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: sidebar.pageSelected(navItem.index)
                }
            }
        }

        Item { Layout.fillHeight: true }

        // ---- футер ----
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: footerRow.implicitHeight + Theme.spacingMd * 2
            radius: Theme.radiusSmall
            color: Theme.surfaceMuted
            border.color: Theme.border
            border.width: 1

            RowLayout {
                id: footerRow
                anchors.fill: parent
                anchors.margins: Theme.spacingMd
                spacing: Theme.spacingSm

                AppIcon {
                    name: "info"
                    size: 16
                    color: Theme.textMuted
                    Layout.alignment: Qt.AlignTop
                }
                Label {
                    text: "Изолированная локальная БД.\nСинк с Google — только вручную."
                    font.pixelSize: 11
                    font.family: Theme.fontFamily
                    color: Theme.textMuted
                    lineHeight: 1.25
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
            }
        }
    }
}
