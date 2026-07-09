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

    // Карточка «название настройки + значение».
    component SettingRow: Panel {
        property string name: ""
        property string value: ""

        Layout.fillWidth: true
        implicitHeight: settingColumn.implicitHeight + 28

        ColumnLayout {
            id: settingColumn
            anchors.fill: parent
            anchors.margins: 14
            spacing: Theme.spacingXs

            Label {
                text: name
                font.pixelSize: Theme.fontCaption
                color: Theme.textMuted
            }
            TextEdit {
                text: value
                readOnly: true
                selectByMouse: true
                font.pixelSize: Theme.fontBody
                color: Theme.textPrimary
                wrapMode: TextEdit.WrapAnywhere
                Layout.fillWidth: true
            }
        }
    }

    ColumnLayout {
        width: Math.min(page.availableWidth - 48, 760)
        x: 24
        spacing: Theme.spacingMd

        Item { implicitHeight: 4 }

        Label {
            text: "Настройки"
            font.pixelSize: Theme.fontDisplay
            font.weight: Font.DemiBold
            color: Theme.textPrimary
        }

        SettingRow {
            name: "Режим приложения"
            value: settingsVm.appMode
        }

        SettingRow {
            name: "Локальная база данных (изолирована от старого app.db)"
            value: settingsVm.dbPath
        }

        // ---- Статус очереди Calendar-синхронизации ----
        Panel {
            Layout.fillWidth: true
            implicitHeight: syncColumn.implicitHeight + 28

            ColumnLayout {
                id: syncColumn
                anchors.fill: parent
                anchors.margins: 14
                spacing: Theme.spacingSm

                RowLayout {
                    Layout.fillWidth: true

                    Label {
                        text: "Синхронизация с Google Calendar"
                        font.pixelSize: Theme.fontSubtitle
                        font.weight: Font.DemiBold
                        color: Theme.textPrimary
                    }
                    Item { Layout.fillWidth: true }
                    AppButton {
                        text: "Обновить"
                        variant: "ghost"
                        onClicked: settingsVm.refresh()
                    }
                }

                GridLayout {
                    visible: settingsVm.hasSyncQueue
                    columns: 2
                    columnSpacing: Theme.spacingLg
                    rowSpacing: Theme.spacingXs
                    Layout.fillWidth: true

                    Label {
                        text: "Операций ждёт отправки:"
                        font.pixelSize: Theme.fontBody
                        color: Theme.textSecondary
                    }
                    RowLayout {
                        spacing: Theme.spacingSm
                        Label {
                            text: String(settingsVm.pendingOpsCount)
                            font.pixelSize: Theme.fontBody
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
                        color: Theme.textSecondary
                    }
                    Label {
                        text: String(settingsVm.terminalOpsCount)
                        font.pixelSize: Theme.fontBody
                        font.weight: Font.DemiBold
                        color: settingsVm.terminalOpsCount > 0
                               ? Theme.danger : Theme.textPrimary
                    }

                    Label {
                        text: "Курсор pull-а:"
                        font.pixelSize: Theme.fontBody
                        color: Theme.textSecondary
                    }
                    Label {
                        text: settingsVm.syncCursor
                        font.pixelSize: Theme.fontBody
                        color: Theme.textPrimary
                        elide: Text.ElideMiddle
                        Layout.fillWidth: true
                    }
                }

                Label {
                    visible: !settingsVm.hasSyncQueue
                    text: "Очередь синхронизации не создана (демо-режим в памяти)."
                    font.pixelSize: Theme.fontBody
                    color: Theme.textMuted
                }
            }
        }

        // ---- Предупреждение: автосинка нет ----
        Rectangle {
            Layout.fillWidth: true
            radius: Theme.radiusMedium
            implicitHeight: noteLabel.implicitHeight + 24
            color: Theme.warningSoft
            border.color: Theme.warningSoftBorder
            border.width: 1

            Label {
                id: noteLabel
                anchors.fill: parent
                anchors.margins: 12
                text: "ℹ️ " + settingsVm.syncNote
                wrapMode: Text.WordWrap
                font.pixelSize: Theme.fontCaption + 1
                color: Theme.warningText
            }
        }

        SettingRow {
            name: "Движок по умолчанию"
            value: "legacy — старое Flet-приложение (main.py) остаётся основным и не изменялось"
        }

        SettingRow {
            name: "Мобильная версия"
            value: "приложение Google Calendar на телефоне (двусторонняя синхронизация — в будущих фазах)"
        }

        Item { implicitHeight: Theme.spacingXl }
    }
}
