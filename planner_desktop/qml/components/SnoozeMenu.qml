import QtQuick
import QtQuick.Controls

import "../theme"

// Меню снуза/переноса задачи. Одно на страницу: карточка или инспектор
// зовут openFor(uid), пункты и их доступность приходят из Python
// (vm.snoozeActionsFor: экземплярам повторяющихся серий перенос запрещён,
// у недатированной задачи нечего снимать). «Выбрать дату и время…»
// делегируется странице (pickRequested -> редактор).
Menu {
    id: menu

    property var vm
    property string targetUid: ""
    property var actions: []

    signal pickRequested(string uid)

    function openFor(uid) {
        targetUid = uid
        actions = vm.snoozeActionsFor(uid)
        if (actions.length > 0)
            popup()
    }

    Instantiator {
        model: menu.actions
        delegate: MenuItem {
            required property var modelData

            text: modelData.label
            enabled: modelData.enabled && !(menu.vm && menu.vm.busy)
            font.pixelSize: Theme.fontBody
            font.family: Theme.fontFamily
            onTriggered: {
                if (modelData.id === "pick")
                    menu.pickRequested(menu.targetUid)
                else
                    menu.vm.postponeTask(menu.targetUid, modelData.id)
            }
        }
        onObjectAdded: (index, object) => menu.insertItem(index, object)
        onObjectRemoved: (index, object) => menu.removeItem(object)
    }
}
