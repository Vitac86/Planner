import QtQuick

import "."
import "../theme"

Badge {
    id: root
    property bool supported: false
    property bool cancelled: false

    text: cancelled ? "Отменена"
                    : (supported ? "Поддерживается" : "Не поддерживается")
    fg: cancelled ? Theme.textSecondary
                  : (supported ? Theme.success : Theme.warningText)
    bg: cancelled ? Theme.surfacePressed
                  : (supported ? Theme.successSoft : Theme.warningSoft)
    Accessible.name: text
}
