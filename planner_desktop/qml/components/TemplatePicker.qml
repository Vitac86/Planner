import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Effects

import "../theme"

// Выбор локального шаблона для новой задачи/серии. Применение шаблона
// только предзаполняет редактор — ничего не сохраняется до «Создать».
Dialog {
    id: dialog

    // [{uid, name, kind, title, isRecurring}] — vm.taskTemplates.
    property var templates: []

    signal templateChosen(string uid)

    parent: Overlay.overlay
    anchors.centerIn: parent
    modal: true
    focus: true
    width: Math.min(440, (parent ? parent.width : 440) - 48)
    height: Math.min(implicitHeight, (parent ? parent.height : 640) - 64)
    padding: Theme.spacingXl
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

    Overlay.modal: Rectangle {
        color: Qt.rgba(0.09, 0.10, 0.16, 0.42)
    }

    background: Rectangle {
        radius: Theme.radiusLarge
        color: Theme.surface
        border.color: Theme.border
        border.width: 1
        layer.enabled: true
        layer.effect: MultiEffect {
            shadowEnabled: true
            shadowColor: Theme.shadowColor
            blurMax: Theme.shadowBlurMax
            shadowBlur: Theme.elevDialogBlur
            shadowVerticalOffset: Theme.elevDialogY
            shadowOpacity: Theme.elevDialogOpacity
            autoPaddingEnabled: true
        }
    }

    contentItem: ColumnLayout {
        spacing: Theme.spacingMd

        RowLayout {
            spacing: Theme.spacingSm
            Layout.fillWidth: true

            AppIcon { name: "template"; size: 18; color: Theme.accent }
            Label {
                text: "Создать из шаблона"
                font.pixelSize: Theme.fontTitle
                font.family: Theme.fontFamily
                font.weight: Font.DemiBold
                color: Theme.textPrimary
                Layout.fillWidth: true
            }
            IconButton {
                iconName: "close"
                tip: "Закрыть (Esc)"
                onClicked: dialog.close()
            }
        }

        Label {
            text: "Шаблон предзаполнит редактор — ничего не сохранится, "
                  + "пока вы не нажмёте «Создать». Шаблоны локальны."
            font.pixelSize: Theme.fontCaption
            font.family: Theme.fontFamily
            color: Theme.textMuted
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        EmptyState {
            visible: !dialog.templates || dialog.templates.length === 0
            iconName: "template"
            text: "Шаблонов пока нет"
            hint: "Создайте шаблон в «Настройках» → «Шаблоны»"
            Layout.fillWidth: true
        }

        ListView {
            id: list
            visible: dialog.templates && dialog.templates.length > 0
            model: dialog.templates || []
            clip: true
            spacing: Theme.spacingXs
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(320, contentHeight)
            keyNavigationEnabled: true
            activeFocusOnTab: true
            Accessible.name: "Шаблоны задач"

            delegate: Rectangle {
                id: row
                required property var modelData
                required property int index

                width: ListView.view.width
                height: 52
                radius: Theme.radiusSmall
                color: rowHover.hovered || row.ListView.isCurrentItem
                       ? Theme.surfaceHover : Theme.surface
                border.color: row.ListView.isCurrentItem
                              ? Theme.accent : Theme.border
                border.width: 1

                Accessible.role: Accessible.ListItem
                Accessible.name: modelData.name + ", "
                    + (modelData.isRecurring ? "повторяющаяся серия" : "обычная задача")

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: Theme.spacingMd
                    anchors.rightMargin: Theme.spacingMd
                    spacing: Theme.spacingSm

                    AppIcon {
                        name: row.modelData.isRecurring ? "repeat" : "template"
                        size: 16
                        color: row.modelData.isRecurring ? Theme.accent : Theme.textSecondary
                    }
                    ColumnLayout {
                        spacing: 1
                        Layout.fillWidth: true
                        Label {
                            text: row.modelData.name
                            font.pixelSize: Theme.fontBody
                            font.family: Theme.fontFamily
                            font.weight: Font.Medium
                            color: Theme.textPrimary
                            elide: Text.ElideRight
                            Layout.fillWidth: true
                        }
                        Label {
                            text: row.modelData.isRecurring
                                  ? "Повторяющаяся серия" : "Обычная задача"
                            font.pixelSize: Theme.fontCaption - 1
                            font.family: Theme.fontFamily
                            color: Theme.textMuted
                        }
                    }
                }

                HoverHandler { id: rowHover; cursorShape: Qt.PointingHandCursor }
                TapHandler {
                    onTapped: {
                        list.currentIndex = row.index
                        dialog.close()
                        dialog.templateChosen(row.modelData.uid)
                    }
                }
            }

            Keys.onReturnPressed: {
                if (currentIndex >= 0 && currentIndex < model.length) {
                    var uid = model[currentIndex].uid
                    dialog.close()
                    dialog.templateChosen(uid)
                }
            }
        }
    }

    onOpened: {
        if (list.count > 0) {
            list.currentIndex = 0
            list.forceActiveFocus()
        }
    }
}
