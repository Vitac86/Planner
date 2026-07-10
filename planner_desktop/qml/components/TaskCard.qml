import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Effects

import "../theme"

// Карточка задачи: галочка, полоса приоритета, заголовок, превью заметки,
// бейджи времени/синка и действия (редактировать/удалить). Тень лежит на
// фоновом прямоугольнике, контент — поверх и остаётся чётким.
// Используется и на «Сегодня», и в «Календаре».
Item {
    id: card

    property string uid: ""
    property string title: ""
    property string notes: ""
    property string timeLabel: ""
    property bool isAllDay: false
    property int priority: 0
    property bool completed: false
    property bool hasPendingSync: false
    property bool isLinked: false

    property bool hovered: hoverHandler.hovered

    signal toggled(string uid)
    signal editRequested(string uid)
    signal deleteRequested(string uid)

    implicitHeight: Math.max(content.implicitHeight + 24, 60)

    Rectangle {
        id: bg
        anchors.fill: parent
        radius: Theme.radiusMedium
        color: card.hovered ? Theme.surfaceHover : Theme.surface
        border.color: card.hovered ? Theme.borderStrong : Theme.border
        border.width: 1

        Behavior on color { ColorAnimation { duration: 110 } }
        Behavior on border.color { ColorAnimation { duration: 110 } }

        layer.enabled: true
        layer.effect: MultiEffect {
            shadowEnabled: true
            shadowColor: Theme.shadowColor
            blurMax: Theme.shadowBlurMax
            shadowBlur: card.hovered ? Theme.elevHoverBlur : Theme.elevCardBlur
            shadowVerticalOffset: card.hovered ? Theme.elevHoverY : Theme.elevCardY
            shadowOpacity: card.hovered ? Theme.elevHoverOpacity : Theme.elevCardOpacity
            autoPaddingEnabled: true
            Behavior on shadowBlur { NumberAnimation { duration: 130 } }
            Behavior on shadowOpacity { NumberAnimation { duration: 130 } }
        }
    }

    HoverHandler { id: hoverHandler }
    TapHandler {
        // Двойной клик по карточке — быстрый путь в редактор.
        onDoubleTapped: card.editRequested(card.uid)
    }

    RowLayout {
        id: content
        anchors.fill: parent
        anchors.leftMargin: 14
        anchors.rightMargin: 12
        anchors.topMargin: 12
        anchors.bottomMargin: 12
        spacing: Theme.spacingMd

        // ---- кастомная галочка выполнения ----
        Rectangle {
            id: check
            Layout.alignment: Qt.AlignVCenter
            implicitWidth: 22
            implicitHeight: 22
            radius: 7
            color: card.completed ? Theme.success : "transparent"
            border.color: card.completed ? Theme.success
                        : checkHover.hovered ? Theme.accent : Theme.borderStrong
            border.width: card.completed ? 0 : 1.6

            Behavior on color { ColorAnimation { duration: 130 } }
            Behavior on border.color { ColorAnimation { duration: 130 } }

            AppIcon {
                anchors.centerIn: parent
                name: "check"
                color: Theme.textOnAccent
                size: 15
                strokeWidth: 2.4
                visible: card.completed
                scale: card.completed ? 1.0 : 0.4
                Behavior on scale { NumberAnimation { duration: 140; easing.type: Easing.OutBack } }
            }

            HoverHandler { id: checkHover; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: card.toggled(card.uid) }
        }

        // цветная полоса приоритета
        Rectangle {
            visible: card.priority > 0
            width: 3
            radius: 2
            Layout.fillHeight: true
            Layout.topMargin: 3
            Layout.bottomMargin: 3
            color: Theme.priorityColor(card.priority)
        }

        ColumnLayout {
            spacing: 4
            Layout.fillWidth: true
            opacity: card.completed ? 0.55 : 1.0
            Behavior on opacity { NumberAnimation { duration: 150 } }

            Label {
                text: card.title
                font.pixelSize: Theme.fontBody
                font.family: Theme.fontFamily
                font.weight: Font.Medium
                font.strikeout: card.completed
                color: card.completed ? Theme.textMuted : Theme.textPrimary
                elide: Text.ElideRight
                Layout.fillWidth: true
            }

            RowLayout {
                spacing: Theme.spacingSm
                visible: card.priority > 0 || card.notes.length > 0
                Layout.fillWidth: true

                PriorityPill { priority: card.priority }

                Label {
                    text: card.notes
                    visible: card.notes.length > 0
                    font.pixelSize: Theme.fontCaption
                    font.family: Theme.fontFamily
                    color: Theme.textMuted
                    elide: Text.ElideRight
                    maximumLineCount: 1
                    Layout.fillWidth: true
                }
            }
        }

        Badge {
            text: card.timeLabel
            fg: card.isAllDay ? Theme.success : Theme.accent
            bg: card.isAllDay ? Theme.successSoft : Theme.accentSoft
            Layout.alignment: Qt.AlignVCenter
        }

        Badge {
            visible: card.hasPendingSync
            text: "⟳ Синк"
            fg: Theme.warningText
            bg: Theme.warningSoft
            Layout.alignment: Qt.AlignVCenter
            ToolTip.visible: syncHover.hovered
            ToolTip.text: "Операция ждёт отправки в Google Calendar (синк вручную)"
            HoverHandler { id: syncHover }
        }

        Rectangle {
            visible: card.isLinked && !card.hasPendingSync
            Layout.alignment: Qt.AlignVCenter
            implicitWidth: 26
            implicitHeight: 22
            radius: height / 2
            color: Theme.surfacePressed
            ToolTip.visible: linkHover.hovered
            ToolTip.text: "Связано с событием Google Calendar"
            AppIcon {
                anchors.centerIn: parent
                name: "link"
                color: Theme.textSecondary
                size: 14
            }
            HoverHandler { id: linkHover }
        }

        RowLayout {
            spacing: 2
            Layout.alignment: Qt.AlignVCenter
            opacity: card.hovered ? 1.0 : 0.0
            Behavior on opacity { NumberAnimation { duration: 130 } }

            IconButton {
                iconName: "edit"
                tip: "Редактировать"
                hoverGlyphColor: Theme.accent
                hoverBg: Theme.accentSoft
                onClicked: card.editRequested(card.uid)
            }
            IconButton {
                iconName: "trash"
                tip: "Удалить"
                hoverGlyphColor: Theme.danger
                hoverBg: Theme.dangerSoft
                onClicked: card.deleteRequested(card.uid)
            }
        }
    }
}
