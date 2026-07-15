import QtQuick
import QtQuick.Controls

import "../theme"

// Бейдж принадлежности к серии: «Локальная серия» / «Серия Google»
// (+ пометка «изменён» для exception). Состояние передаётся не только
// цветом: есть иконка и текст (доступность).
Rectangle {
    id: badge

    property bool isLocalSeries: false
    property bool isGoogleSeries: false
    property bool isException: false
    // compact: только иконка + тултип, для узких карточек/блоков.
    property bool compact: false

    readonly property string labelText: {
        if (badge.isGoogleSeries)
            return "Серия Google"
        if (badge.isLocalSeries)
            return badge.isException ? "Локальная серия · изменён" : "Локальная серия"
        return ""
    }

    visible: isLocalSeries || isGoogleSeries
    radius: height / 2
    implicitHeight: 22
    implicitWidth: compact ? 26 : row.implicitWidth + 16
    color: badge.isGoogleSeries ? Theme.surfacePressed : Theme.accentSoft
    border.color: badge.isGoogleSeries ? Theme.borderStrong
                : Qt.alpha(Theme.accent, 0.35)
    border.width: 1

    Accessible.role: Accessible.StaticText
    Accessible.name: badge.labelText

    ToolTip.visible: hover.hovered
    ToolTip.text: badge.isGoogleSeries
        ? "Экземпляр повторяющегося события Google Calendar: расписание менять нельзя"
        : (badge.isException
            ? "Экземпляр локальной серии, изменённый отдельно («только этот»)"
            : "Экземпляр локальной повторяющейся серии (в Google не синхронизируется)")

    Row {
        id: row
        anchors.centerIn: parent
        spacing: 4

        AppIcon {
            anchors.verticalCenter: parent.verticalCenter
            name: "repeat"
            size: 12
            color: badge.isGoogleSeries ? Theme.textSecondary : Theme.accent
        }
        Label {
            visible: !badge.compact
            anchors.verticalCenter: parent.verticalCenter
            text: badge.labelText
            font.pixelSize: Theme.fontCaption - 1
            font.family: Theme.fontFamily
            font.weight: Font.DemiBold
            color: badge.isGoogleSeries ? Theme.textSecondary : Theme.accent
        }
    }

    HoverHandler { id: hover }
}
