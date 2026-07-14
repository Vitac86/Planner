import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

Panel {
    id: toolbar
    property var vm: null
    property bool compact: false
    signal deleteRequested()

    implicitHeight: content.implicitHeight + 2 * Theme.spacingMd
    borderColor: Theme.accentSoftBorder
    color: Theme.accentSoft

    Accessible.role: Accessible.ToolBar
    Accessible.name: vm ? vm.selectionStatus : "Пакетные действия"

    ColumnLayout {
        id: content
        anchors.fill: parent
        anchors.margins: Theme.spacingMd
        spacing: Theme.spacingSm

        RowLayout {
            Layout.fillWidth: true
            AppIcon { name: "check"; size: 17; color: Theme.accent }
            Label {
                text: toolbar.vm ? toolbar.vm.selectionStatus : ""
                font.pixelSize: Theme.fontBody
                font.family: Theme.fontFamily
                font.weight: Font.DemiBold
                color: Theme.textPrimary
                Accessible.role: Accessible.StatusBar
                Accessible.name: text
            }
            Item { Layout.fillWidth: true }
            AppButton {
                text: toolbar.compact ? "" : "Снять выбор"
                iconName: "close"
                variant: "ghost"
                Accessible.name: "Снять выбор задач"
                onClicked: if (toolbar.vm) toolbar.vm.clearSelection()
            }
        }

        Flow {
            Layout.fillWidth: true
            spacing: Theme.spacingSm
            AppButton {
                text: "Выполнить"; iconName: "check"; variant: "secondary"
                Accessible.name: "Отметить выбранные задачи выполненными"
                onClicked: toolbar.vm.bulkComplete()
            }
            AppButton {
                text: "В работу"; iconName: "refresh"; variant: "secondary"
                Accessible.name: "Вернуть выбранные задачи в работу"
                onClicked: toolbar.vm.bulkRestore()
            }
            AppButton {
                text: "На завтра"; iconName: "calendar"; variant: "secondary"
                Accessible.name: "Перенести выбранные задачи на завтра"
                onClicked: toolbar.vm.bulkPostponeTomorrow()
            }
            AppButton {
                text: "Без даты"; iconName: "calendar"; variant: "secondary"
                Accessible.name: "Снять расписание у выбранных задач"
                onClicked: toolbar.vm.bulkUnschedule()
            }
            ComboBox {
                id: priorityCombo
                width: 150
                model: ["Без приоритета", "Низкий", "Средний", "Высокий"]
                Accessible.name: "Приоритет для выбранных задач"
                onActivated: toolbar.vm.bulkSetPriority(currentIndex)
            }
            ComboBox {
                id: tagCombo
                width: 150
                model: toolbar.vm ? toolbar.vm.availableTags : []
                textRole: "name"
                Accessible.name: "Тег для пакетного действия"
            }
            AppButton {
                text: "+ тег"; variant: "secondary"
                enabled: tagCombo.currentIndex >= 0
                Accessible.name: "Добавить выбранный тег ко всем выбранным задачам"
                onClicked: toolbar.vm.bulkAddTag(tagCombo.model[tagCombo.currentIndex].id)
            }
            AppButton {
                text: "− тег"; variant: "secondary"
                enabled: tagCombo.currentIndex >= 0
                Accessible.name: "Убрать выбранный тег у всех выбранных задач"
                onClicked: toolbar.vm.bulkRemoveTag(tagCombo.model[tagCombo.currentIndex].id)
            }
            AppButton {
                text: "Удалить"; iconName: "trash"; variant: "danger"
                Accessible.name: "Удалить выбранные задачи с подтверждением"
                onClicked: toolbar.deleteRequested()
            }
        }
    }
}
