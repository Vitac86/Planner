import QtQuick

import "../theme"

// Базовая «карточка»-поверхность: скруглённая, с тонкой рамкой.
Rectangle {
    radius: Theme.radiusMedium
    color: Theme.surface
    border.color: Theme.border
    border.width: 1
}
