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
            subtitle: "Данные хранятся локально на этом компьютере · синхронизация с Google — только вручную"
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

                // Разбивка ожидающих операций по типу (наглядный статус синка).
                RowLayout {
                    visible: settingsVm.hasSyncQueue
                    Layout.fillWidth: true
                    spacing: Theme.spacingSm

                    component OpChip: Rectangle {
                        property string label: ""
                        property int value: 0
                        implicitHeight: 52
                        Layout.fillWidth: true
                        radius: Theme.radiusMedium
                        color: Theme.surfaceMuted
                        border.color: Theme.border
                        border.width: 1
                        ColumnLayout {
                            anchors.centerIn: parent
                            spacing: 0
                            Label {
                                Layout.alignment: Qt.AlignHCenter
                                text: String(parent.parent.value)
                                font.pixelSize: Theme.fontSubtitle
                                font.family: Theme.fontFamily
                                font.weight: Font.DemiBold
                                color: parent.parent.value > 0 ? Theme.warningText : Theme.textPrimary
                            }
                            Label {
                                Layout.alignment: Qt.AlignHCenter
                                text: parent.parent.label
                                font.pixelSize: Theme.fontCaption
                                font.family: Theme.fontFamily
                                color: Theme.textMuted
                            }
                        }
                    }
                    OpChip { label: "создать"; value: settingsVm.pendingCreateCount }
                    OpChip { label: "обновить"; value: settingsVm.pendingUpdateCount }
                    OpChip { label: "удалить"; value: settingsVm.pendingDeleteCount }
                    OpChip { label: "dead-letter"; value: settingsVm.terminalOpsCount }
                }

                GridLayout {
                    visible: settingsVm.hasSyncQueue
                    columns: 2
                    columnSpacing: Theme.spacingLg
                    rowSpacing: Theme.spacingSm
                    Layout.fillWidth: true

                    Label {
                        text: "Всего операций в очереди:"
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
                        text: "Последнее локальное изменение:"
                        font.pixelSize: Theme.fontBody
                        font.family: Theme.fontFamily
                        color: Theme.textSecondary
                    }
                    Label {
                        text: settingsVm.lastLocalChange
                        font.pixelSize: Theme.fontBody
                        font.family: Theme.fontFamily
                        color: Theme.textPrimary
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

                // Заглушка ручного синка: настоящего Google-шлюза ещё нет.
                RowLayout {
                    Layout.fillWidth: true
                    Layout.topMargin: Theme.spacingXs
                    spacing: Theme.spacingSm

                    AppButton {
                        text: "Синхронизировать сейчас"
                        variant: "primary"
                        iconName: "refresh"
                        enabled: settingsVm.manualSyncEnabled
                        ToolTip.visible: hovered
                        ToolTip.text: settingsVm.manualSyncNote
                    }
                    Label {
                        text: settingsVm.manualSyncNote
                        font.pixelSize: Theme.fontCaption
                        font.family: Theme.fontFamily
                        color: Theme.textMuted
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }
                }
            }
        }

        // ---- Диагностика (локально, без токенов) ----
        Panel {
            Layout.fillWidth: true
            implicitHeight: diagColumn.implicitHeight + 2 * Theme.spacingLg

            ColumnLayout {
                id: diagColumn
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
                        color: Theme.surfaceMuted
                        border.color: Theme.border
                        border.width: 1
                        AppIcon { anchors.centerIn: parent; name: "info"; color: Theme.textSecondary; size: 18 }
                    }
                    Label {
                        text: "Диагностика"
                        font.pixelSize: Theme.fontSubtitle
                        font.family: Theme.fontFamily
                        font.weight: Font.DemiBold
                        color: Theme.textPrimary
                        Layout.alignment: Qt.AlignVCenter
                    }
                    Item { Layout.fillWidth: true }
                    AppButton {
                        id: copyButton
                        text: copyButton.copied ? "Скопировано" : "Копировать"
                        property bool copied: false
                        variant: "secondary"
                        iconName: "check"
                        onClicked: {
                            diagText.selectAll()
                            diagText.copy()
                            diagText.deselect()
                            copyButton.copied = true
                            copyResetTimer.restart()
                        }
                        Timer {
                            id: copyResetTimer
                            interval: 1600
                            onTriggered: copyButton.copied = false
                        }
                    }
                }

                GridLayout {
                    columns: 2
                    columnSpacing: Theme.spacingLg
                    rowSpacing: Theme.spacingSm
                    Layout.fillWidth: true

                    component DiagKey: Label {
                        font.pixelSize: Theme.fontBody
                        font.family: Theme.fontFamily
                        color: Theme.textSecondary
                    }
                    component DiagVal: Label {
                        font.pixelSize: Theme.fontBody
                        font.family: Theme.fontFamily
                        font.weight: Font.DemiBold
                        color: Theme.textPrimary
                    }

                    DiagKey { text: "Версия схемы БД:" }
                    DiagVal { text: String(settingsVm.schemaVersion) }
                    DiagKey { text: "Задач (активных):" }
                    DiagVal { text: String(settingsVm.taskCount) }
                    DiagKey { text: "Ежедневных задач:" }
                    DiagVal { text: String(settingsVm.dailyTaskCount) }
                    DiagKey { text: "Операций в очереди:" }
                    DiagVal { text: String(settingsVm.pendingOpsCount) }
                    DiagKey { text: "Dead-letter:" }
                    DiagVal { text: String(settingsVm.terminalOpsCount) }
                }

                // Скрытый носитель текста для «Копировать» (буфер обмена через
                // TextEdit.copy — без зависимости от Python-clipboard).
                TextEdit {
                    id: diagText
                    text: settingsVm.diagnosticsText
                    visible: false
                    readOnly: true
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
