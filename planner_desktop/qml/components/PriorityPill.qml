import QtQuick

import "../theme"

// Пилюля приоритета: цвета и подписи — как в старом приложении.
// Для priority 0 («без приоритета») ничего не рисуется.
Badge {
    property int priority: 0

    visible: priority > 0
    text: priority > 0 ? Theme.priorityName(priority) : ""
    fg: Theme.priorityColor(priority)
    bg: Theme.priorityBgColor(priority)
}
