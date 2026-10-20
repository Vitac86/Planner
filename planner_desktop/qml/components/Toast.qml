import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Effects

import "../theme"

// Всплывашка успеха/ошибки в нижней части окна.
// show(text) — успех (или «удалено», если текст про удаление);
// showError(text) — ошибка, держится дольше и окрашена в danger.
Item {
    id: toast

    property string message: ""
    property string iconName: "check"
    property color iconColor: "#7CE6A6"
    property color bubbleColor: Theme.scrim
    property string actionText: ""
    signal actionTriggered()

    function show(text) {
        actionText = ""
        message = text
        bubbleColor = Theme.scrim
        if (text.indexOf("далена") >= 0 || text.indexOf("далён") >= 0) {
            iconName = "trash"; iconColor = "#FF9B9B"
        } else {
            iconName = "check"; iconColor = "#7CE6A6"
        }
        toastTimer.interval = 2200
        toastTimer.restart()
    }

    function showError(text) {
        actionText = ""
        message = text
        bubbleColor = "#7A2E33"
        iconName = "info"
        iconColor = "#FFC9CC"
        toastTimer.interval = 4200
        toastTimer.restart()
    }

    function showAction(text, action) {
        show(text)
        actionText = action
        toastTimer.interval = 6500
        toastTimer.restart()
    }

    anchors.bottomMargin: toastTimer.running ? 30 : 18
    width: toastBg.width
    height: toastBg.height
    opacity: toastTimer.running ? 1.0 : 0
    visible: opacity > 0
    z: 900

    Behavior on opacity { NumberAnimation { duration: 180 } }
    Behavior on anchors.bottomMargin { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }

    Rectangle {
        id: toastBg
        radius: height / 2
        color: toast.bubbleColor
        implicitHeight: Math.max(40, toastRow.implicitHeight + 8)
        implicitWidth: toastRow.implicitWidth + 34

        layer.enabled: true
        layer.effect: MultiEffect {
            shadowEnabled: true
            shadowColor: Theme.shadowColor
            blurMax: Theme.shadowBlurMax
            shadowBlur: Theme.elevDialogBlur
            shadowVerticalOffset: 8
            shadowOpacity: 0.34
            autoPaddingEnabled: true
        }

        RowLayout {
            id: toastRow
            anchors.centerIn: parent
            spacing: Theme.spacingSm

            AppIcon {
                name: toast.iconName
                color: toast.iconColor
                size: 17
            }
            Label {
                text: toast.message
                color: "#FFFFFF"
                font.pixelSize: 13
                font.family: Theme.fontFamily
                font.weight: Font.Medium
                elide: Text.ElideRight
                // длинные ошибки не растягивают пилюлю на всё окно
                Layout.maximumWidth: Math.min(520, Math.max(120,
                    (toast.parent ? toast.parent.width : 680)
                    - (toast.actionText.length > 0 ? 200 : 80)))
            }
            Button {
                visible: toast.actionText.length > 0
                text: toast.actionText
                flat: true
                palette.buttonText: "#FFFFFF"
                font.bold: true
                Accessible.name: toast.actionText
                onClicked: {
                    toastTimer.stop()
                    toast.actionTriggered()
                }
            }
        }
    }

    Timer {
        id: toastTimer
        interval: 2200
    }
}
