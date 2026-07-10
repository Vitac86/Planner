import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../components"
import "../theme"

ScrollView {
    id: page
    contentWidth: availableWidth
    clip: true

    // Счётчики очереди могли измениться на других страницах.
    onVisibleChanged: if (visible) settingsVm.refresh()

    // Карточка «иконка + название настройки + значение».
    component SettingRow: Panel {
        property string name: ""
        property string value: ""
        property string iconName: "info"

        Layout.fillWidth: true
        implicitHeight: settingRow.implicitHeight + 2 * Theme.spacingLg

        RowLayout {
            id: settingRow
            anchors.fill: parent
            anchors.margins: Theme.spacingLg
            spacing: Theme.spacingMd

            Rectangle {
                implicitWidth: 36
                implicitHeight: 36
                radius: Theme.radiusSmall + 2
                color: Theme.surfaceMuted
                border.color: Theme.border
                border.width: 1
                Layout.alignment: Qt.AlignTop
                AppIcon {
                    anchors.centerIn: parent
                    name: iconName
                    color: Theme.textSecondary
                    size: 18
                }
            }

            ColumnLayout {
                spacing: 3
                Layout.fillWidth: true
                Label {
                    text: name
                    font.pixelSize: Theme.fontCaption
                    font.family: Theme.fontFamily
                    color: Theme.textMuted
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                }
                TextEdit {
                    text: value
                    readOnly: true
                    selectByMouse: true
                    font.pixelSize: Theme.fontBody
                    font.family: Theme.fontFamily
                    color: Theme.textPrimary
                    wrapMode: TextEdit.WrapAnywhere
                    Layout.fillWidth: true
                }
            }
        }
    }

    ColumnLayout {
        width: Math.min(page.availableWidth - 48, 780)
        x: 24
        spacing: Theme.spacingMd

        Item { implicitHeight: 20 }

        PageHeader {
            title: "Настройки"
            subtitle: "Локальный режим · автосинхронизации нет"
            Layout.fillWidth: true
        }

        Item { implicitHeight: Theme.spacingXs }

        SettingRow {
            name: "Режим приложения"
            value: settingsVm.appMode
            iconName: "sparkle"
        }

        SettingRow {
            name: "Локальная база данных (изолирована от старого app.db)"
            value: settingsVm.dbPath
            iconName: "note"
        }

        // ---- Статус очереди Calendar-синхронизации ----
        Panel {
            Layout.fillWidth: true
            implicitHeight: syncColumn.implicitHeight + 2 * Theme.spacingLg

            ColumnLayout {
                id: syncColumn
                anchors.fill: parent
                anchors.margins: Theme.spacingLg
                spacing: Theme.spacingMd

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.spacingSm

                    Rectangle {
                        implicitWidth: 36
                        implicitHeight: 36
                        radius: Theme.radiusSmall + 2
                        color: Theme.accentSoft
                        AppIcon {
                            anchors.centerIn: parent
                            name: "refresh"
                            color: Theme.accent
                            size: 18
                        }
                    }
                    Label {
                        text: "Синхронизация с Google Calendar"
                        font.pixelSize: Theme.fontSubtitle
                        font.family: Theme.fontFamily
                        font.weight: Font.DemiBold
                        color: Theme.textPrimary
                        Layout.alignment: Qt.AlignVCenter
                    }
                    Item { Layout.fillWidth: true }
                    AppButton {
                        text: "Обновить"
                        variant: "secondary"
                        iconName: "refresh"
                        onClicked: settingsVm.refresh()
                    }
                }

                GridLayout {
                    visible: settingsVm.hasSyncQueue
                    columns: 2
                    columnSpacing: Theme.spacingLg
                    rowSpacing: Theme.spacingSm
                    Layout.fillWidth: true

                    Label {
                        text: "Операций ждёт отправки:"
                        font.pixelSize: Theme.fontBody
                        font.family: Theme.fontFamily
                        color: Theme.textSecondary
                    }
                    RowLayout {
                        spacing: Theme.spacingSm
                        Label {
                            text: String(settingsVm.pendingOpsCount)
                            font.pixelSize: Theme.fontBody
                            font.family: Theme.fontFamily
                            font.weight: Font.DemiBold
                            color: settingsVm.pendingOpsCount > 0
                                   ? Theme.warningText : Theme.textPrimary
                        }
                        Badge {
                            visible: settingsVm.pendingOpsCount > 0
                            text: "ждёт ручного синка"
                            fg: Theme.warningText
                            bg: Theme.warningSoft
                        }
                    }

                    Label {
                        text: "Dead-letter (постоянные ошибки):"
                        font.pixelSize: Theme.fontBody
                        font.family: Theme.fontFamily
                        color: Theme.textSecondary
                    }
                    Label {
                        text: String(settingsVm.terminalOpsCount)
                        font.pixelSize: Theme.fontBody
                        font.family: Theme.fontFamily
                        font.weight: Font.DemiBold
                        color: settingsVm.terminalOpsCount > 0
                               ? Theme.danger : Theme.textPrimary
                    }

                    Label {
                        text: "Курсор pull-а:"
                        font.pixelSize: Theme.fontBody
                        font.family: Theme.fontFamily
                        color: Theme.textSecondary
                    }
                    Label {
                        text: settingsVm.syncCursor
                        font.pixelSize: Theme.fontBody
                        font.family: Theme.fontFamily
                        color: Theme.textPrimary
                        elide: Text.ElideMiddle
                        Layout.fillWidth: true
                    }
                }

                Label {
                    visible: !settingsVm.hasSyncQueue
                    text: "Очередь синхронизации не создана (демо-режим в памяти)."
                    font.pixelSize: Theme.fontBody
                    font.family: Theme.fontFamily
                    color: Theme.textMuted
                }
            }
        }

        // ---- Предупреждение: автосинка нет ----
        Rectangle {
            Layout.fillWidth: true
            radius: Theme.radiusMedium
            implicitHeight: noteRow.implicitHeight + 2 * Theme.spacingMd
            color: Theme.warningSoft
            border.color: Theme.warningSoftBorder
            border.width: 1

            RowLayout {
                id: noteRow
                anchors.fill: parent
                anchors.margins: Theme.spacingMd
                spacing: Theme.spacingSm

                AppIcon {
                    name: "info"
                    size: 18
                    color: Theme.warningText
                    Layout.alignment: Qt.AlignTop
                }
                Label {
                    text: settingsVm.syncNote
                    wrapMode: Text.WordWrap
                    font.pixelSize: Theme.fontCaption + 1
                    font.family: Theme.fontFamily
                    color: Theme.warningText
                    Layout.fillWidth: true
                }
            }
        }

        SettingRow {
            name: "Движок по умолчанию"
            value: "legacy — старое Flet-приложение (main.py) остаётся основным и не изменялось"
            iconName: "settings"
        }

        SettingRow {
            name: "Мобильная версия"
            value: "приложение Google Calendar на телефоне (двусторонняя синхронизация — в будущих фазах)"
            iconName: "calendar"
        }

        Item { implicitHeight: Theme.spacingXl }
    }
}
