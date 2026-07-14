import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../components"
import "../theme"

ScrollView {
    id: page
    contentWidth: availableWidth
    clip: true
    readonly property bool compact: availableWidth < 700

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
                Layout.minimumWidth: 0
                Label {
                    text: name
                    font.pixelSize: Theme.fontCaption
                    font.family: Theme.fontFamily
                    color: Theme.textMuted
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
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
                    Layout.minimumWidth: 0
                }
            }
        }
    }

    ColumnLayout {
        width: Math.min(page.availableWidth - (page.compact ? 32 : 48), 780)
        x: Math.max(page.compact ? 16 : 24, (page.availableWidth - width) / 2)
        spacing: Theme.spacingMd

        Item { implicitHeight: 20 }

        PageHeader {
            title: "Настройки"
            subtitle: "Данные хранятся локально на этом компьютере · синхронизация с Google — только вручную"
            Layout.fillWidth: true
            stackActions: page.compact
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
                    Layout.minimumWidth: 0
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
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        wrapMode: Text.WordWrap
                    }
                    AppButton {
                        text: page.compact ? "" : "Обновить"
                        variant: "secondary"
                        iconName: "refresh"
                        onClicked: settingsVm.refresh()
                        ToolTip.visible: page.compact && hovered
                        ToolTip.text: "Обновить локальные данные"
                    }
                }

                // Разбивка ожидающих операций по типу (наглядный статус синка).
                GridLayout {
                    visible: settingsVm.hasSyncQueue
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    columns: page.compact ? 2 : 4
                    rowSpacing: Theme.spacingSm
                    columnSpacing: Theme.spacingSm

                    component OpChip: Rectangle {
                        property string label: ""
                        property int value: 0
                        implicitHeight: 52
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
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
                    columns: page.compact ? 1 : 2
                    columnSpacing: Theme.spacingLg
                    rowSpacing: Theme.spacingSm
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0

                    Label {
                        text: "Всего операций в очереди:"
                        font.pixelSize: Theme.fontBody
                        font.family: Theme.fontFamily
                        color: Theme.textSecondary
                    }
                    RowLayout {
                        spacing: Theme.spacingSm
                        Layout.minimumWidth: 0
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
                        Layout.minimumWidth: 0
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
                        Layout.minimumWidth: 0
                    }
                }

                Label {
                    visible: !settingsVm.hasSyncQueue
                    text: "Очередь синхронизации не создана (демо-режим в памяти)."
                    font.pixelSize: Theme.fontBody
                    font.family: Theme.fontFamily
                    color: Theme.textMuted
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                }

                Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }

                // ---- Подключение и ручной синк (реальный Google-шлюз) ----

                // Статус подключения (только файлы изолированного профиля).
                RowLayout {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    spacing: Theme.spacingSm

                    Rectangle {
                        implicitWidth: 22
                        implicitHeight: 22
                        radius: height / 2
                        color: settingsVm.googleConnected
                               ? Theme.successSoft : Theme.surfacePressed
                        Layout.alignment: Qt.AlignTop
                        AppIcon {
                            anchors.centerIn: parent
                            name: settingsVm.googleConnected ? "check" : "info"
                            size: 13
                            color: settingsVm.googleConnected
                                   ? Theme.success : Theme.textMuted
                        }
                    }
                    Label {
                        text: settingsVm.connectionStatusText
                        font.pixelSize: Theme.fontBody
                        font.family: Theme.fontFamily
                        color: Theme.textPrimary
                        wrapMode: Text.WrapAtWordBoundaryOrAnywhere
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                    }
                }

                // Действия: явное подключение и явный одноразовый синк.
                RowLayout {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    spacing: Theme.spacingSm

                    AppButton {
                        objectName: "connectGoogleButton"
                        visible: !settingsVm.googleConnected
                        text: settingsVm.connectRunning
                              ? (page.compact ? "Ожидание входа…"
                                              : "Ожидание входа в браузере…")
                              : (page.compact ? "Подключить"
                                              : "Подключить Google Calendar")
                        variant: "primary"
                        iconName: "plus"
                        loading: settingsVm.connectRunning
                        enabled: settingsVm.connectEnabled
                        onClicked: settingsVm.connectGoogle()
                        ToolTip.visible: hovered && !settingsVm.hasClientSecret
                        ToolTip.text: "Сначала положите client_secret.json в:\n"
                                      + settingsVm.clientSecretPath
                    }
                    AppButton {
                        objectName: "syncNowButton"
                        visible: settingsVm.googleConnected
                        text: settingsVm.syncRunning
                              ? "Синхронизация…" : "Синхронизировать сейчас"
                        variant: "primary"
                        iconName: "refresh"
                        loading: settingsVm.syncRunning
                        enabled: settingsVm.manualSyncEnabled
                        onClicked: settingsVm.syncNow()
                    }
                    BusyIndicator {
                        visible: settingsVm.syncBusy
                        running: settingsVm.syncBusy
                        implicitWidth: 24
                        implicitHeight: 24
                    }
                    Item { Layout.fillWidth: true }
                }

                // Сводка последнего синка + ошибка (без токенов).
                GridLayout {
                    visible: settingsVm.googleConnected
                             && (settingsVm.lastSyncSummary.length > 0
                                 || settingsVm.lastSyncAt !== "—")
                    columns: page.compact ? 1 : 2
                    columnSpacing: Theme.spacingLg
                    rowSpacing: Theme.spacingSm
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0

                    Label {
                        text: "Последний успешный синк:"
                        font.pixelSize: Theme.fontBody
                        font.family: Theme.fontFamily
                        color: Theme.textSecondary
                    }
                    Label {
                        text: settingsVm.lastSyncAt
                        font.pixelSize: Theme.fontBody
                        font.family: Theme.fontFamily
                        font.weight: Font.DemiBold
                        color: Theme.textPrimary
                    }

                    Label {
                        visible: settingsVm.lastSyncSummary.length > 0
                        text: "Итог:"
                        font.pixelSize: Theme.fontBody
                        font.family: Theme.fontFamily
                        color: Theme.textSecondary
                    }
                    Label {
                        visible: settingsVm.lastSyncSummary.length > 0
                        text: settingsVm.lastSyncSummary
                        font.pixelSize: Theme.fontBody
                        font.family: Theme.fontFamily
                        color: Theme.textPrimary
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                    }
                }

                Label {
                    objectName: "syncErrorLabel"
                    visible: settingsVm.lastSyncError.length > 0
                    text: settingsVm.lastSyncError
                    font.pixelSize: Theme.fontBody
                    font.family: Theme.fontFamily
                    color: Theme.danger
                    wrapMode: Text.WrapAtWordBoundaryOrAnywhere
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                }

                Label {
                    text: settingsVm.manualSyncNote
                    font.pixelSize: Theme.fontCaption
                    font.family: Theme.fontFamily
                    color: Theme.textMuted
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
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
                    Layout.minimumWidth: 0
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
                        text: page.compact
                              ? ""
                              : (copyButton.copied ? "Скопировано" : "Копировать")
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
                        ToolTip.visible: page.compact && hovered
                        ToolTip.text: copyButton.copied ? "Скопировано" : "Копировать"
                    }
                }

                GridLayout {
                    columns: page.compact ? 1 : 2
                    columnSpacing: Theme.spacingLg
                    rowSpacing: Theme.spacingSm
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0

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
