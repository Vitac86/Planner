import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ScrollView {
    id: page
    contentWidth: availableWidth
    clip: true

    ColumnLayout {
        width: Math.min(page.availableWidth - 48, 720)
        x: 24
        spacing: 14

        Item { implicitHeight: 6 }

        Label {
            text: "Настройки"
            font.pixelSize: 26
            font.weight: Font.DemiBold
            color: "#23283D"
        }

        // предупреждение о фейковых данных
        Rectangle {
            Layout.fillWidth: true
            radius: 10
            implicitHeight: warnLabel.implicitHeight + 24
            color: "#FDECEC"
            border.color: "#F2C4C4"
            border.width: 1

            Label {
                id: warnLabel
                anchors.fill: parent
                anchors.margins: 12
                text: "⚠️ Этот скелет использует только фейковые данные в памяти. "
                      + "Ничего не сохраняется, никакие Google API не вызываются."
                wrapMode: Text.WordWrap
                font.pixelSize: 13
                color: "#8C2B2B"
            }
        }

        Repeater {
            model: [
                {
                    name: "Режим приложения",
                    value: "экспериментальный rewrite на PySide6 + Qt Quick/QML"
                },
                {
                    name: "Синхронизация",
                    value: "не подключена в этом скелете (контракт: sync/calendar_contract.py)"
                },
                {
                    name: "Движок по умолчанию",
                    value: "legacy — старое Flet-приложение (main.py) остаётся основным"
                },
                {
                    name: "Мобильная версия",
                    value: "приложение Google Calendar на телефоне (двусторонняя синхронизация — в будущих фазах)"
                }
            ]

            delegate: Rectangle {
                required property var modelData
                Layout.fillWidth: true
                radius: 12
                implicitHeight: settingRow.implicitHeight + 28
                color: "#FFFFFF"
                border.color: "#E6E8F0"
                border.width: 1

                ColumnLayout {
                    id: settingRow
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 4

                    Label {
                        text: modelData.name
                        font.pixelSize: 12
                        color: "#8A90A6"
                    }
                    Label {
                        text: modelData.value
                        font.pixelSize: 14
                        color: "#23283D"
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }
                }
            }
        }

        Item { implicitHeight: 24 }
    }
}
