import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

ColumnLayout {
    id: picker
    property var availableTags: []
    property var selectedIds: []
    property string validationError: ""
    signal createRequested(string name)

    spacing: Theme.spacingSm

    function reset(ids) {
        selectedIds = ids ? Array.prototype.slice.call(ids) : []
        validationError = ""
        newTagField.text = ""
    }
    function contains(tagId) {
        return selectedIds.indexOf(Number(tagId)) >= 0
    }
    function add(tagId) {
        tagId = Number(tagId)
        if (contains(tagId)) return
        var next = selectedIds.slice()
        next.push(tagId)
        selectedIds = next
    }
    function remove(tagId) {
        tagId = Number(tagId)
        var next = []
        for (var i = 0; i < selectedIds.length; i++)
            if (Number(selectedIds[i]) !== tagId) next.push(Number(selectedIds[i]))
        selectedIds = next
    }
    function tagForId(tagId) {
        for (var i = 0; i < availableTags.length; i++)
            if (Number(availableTags[i].id) === Number(tagId)) return availableTags[i]
        return null
    }
    function handleCreated(result) {
        if (result && result.ok) {
            validationError = ""
            add(result.id)
            newTagField.text = ""
        } else {
            validationError = result && result.error ? result.error : "Не удалось создать тег."
        }
    }

    Label {
        text: "Теги"
        font.pixelSize: Theme.fontCaption
        font.family: Theme.fontFamily
        font.weight: Font.DemiBold
        color: Theme.textMuted
        Accessible.role: Accessible.StaticText
    }

    Flow {
        Layout.fillWidth: true
        spacing: Theme.spacingXs
        visible: picker.selectedIds.length > 0
        Repeater {
            model: picker.selectedIds
            delegate: TagChip {
                required property var modelData
                readonly property var tagData: picker.tagForId(modelData)
                visible: tagData !== null
                name: tagData ? tagData.name : ""
                removable: true
                onRemoveRequested: picker.remove(modelData)
            }
        }
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.spacingSm
        ComboBox {
            id: existingCombo
            Layout.fillWidth: true
            model: picker.availableTags
            textRole: "name"
            Accessible.name: "Выбрать существующий тег"
        }
        AppButton {
            text: "Добавить"
            variant: "secondary"
            enabled: existingCombo.currentIndex >= 0
            Accessible.name: "Добавить выбранный тег к задаче"
            onClicked: {
                var item = picker.availableTags[existingCombo.currentIndex]
                if (item) picker.add(item.id)
            }
        }
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.spacingSm
        AppTextField {
            id: newTagField
            Layout.fillWidth: true
            placeholderText: "Новый локальный тег"
            Accessible.name: "Название нового тега"
            onAccepted: if (text.trim().length > 0) picker.createRequested(text)
        }
        AppButton {
            text: "Создать"
            iconName: "plus"
            variant: "secondary"
            enabled: newTagField.text.trim().length > 0
            Accessible.name: "Создать и назначить новый тег"
            onClicked: picker.createRequested(newTagField.text)
        }
    }

    Label {
        visible: picker.validationError.length > 0
        text: picker.validationError
        color: Theme.danger
        font.pixelSize: Theme.fontCaption
        font.family: Theme.fontFamily
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
        Accessible.role: Accessible.AlertMessage
    }
    Label {
        text: "Теги остаются только в Planner и не отправляются в Google Calendar."
        color: Theme.textMuted
        font.pixelSize: Theme.fontCaption
        font.family: Theme.fontFamily
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    }
}
