import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

// Карточка задачи: галочка, приоритет, заголовок, превью заметки,
// бейджи времени/синка и действия (редактировать/удалить).
// Используется и на «Сегодня», и в «Календаре».
Rectangle {
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

    signal toggled(string uid)
    signal editRequested(string uid)
    signal deleteRequested(string uid)

    implicitHeight: content.implicitHeight + 22
    radius: Theme.radiusMedium
    color: cardHover.hovered ? Theme.surfaceHover : Theme.surface
    border.color: cardHover.hovered ? Theme.borderStrong : Theme.border
    border.width: 1

    Behavior on color { ColorAnimation { duration: 100 } }

    HoverHandler { id: cardHover }

    TapHandler {
        // Двойной клик по карточке — быстрый путь в редактор.
        onDoubleTapped: card.editRequested(card.uid)
    }

    RowLayout {
        id: content
        anchors.fill: parent
        anchors.leftMargin: 10
        anchors.rightMargin: 12
        anchors.topMargin: 11
        anchors.bottomMargin: 11
        spacing: Theme.spacingSm

        CheckBox {
            checked: card.completed
            onToggled: card.toggled(card.uid)
            Layout.alignment: Qt.AlignVCenter
            HoverHandler { cursorShape: Qt.PointingHandCursor }
        }

        // цветная полоска приоритета
        Rectangle {
            width: 4
            radius: 2
            Layout.fillHeight: true
            Layout.topMargin: 2
            Layout.bottomMargin: 2
            color: card.priority > 0 ? Theme.priorityColor(card.priority)
                                     : Theme.border
        }

        ColumnLayout {
            spacing: 3
            Layout.fillWidth: true

            Label {
                text: card.title
                font.pixelSize: Theme.fontBody
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

        Badge {
            visible: card.isLinked && !card.hasPendingSync
            text: "Google"
            fg: Theme.textSecondary
            bg: Theme.surfacePressed
            Layout.alignment: Qt.AlignVCenter
        }

        RowLayout {
            spacing: 2
            Layout.alignment: Qt.AlignVCenter
            opacity: cardHover.hovered ? 1.0 : 0.35
            Behavior on opacity { NumberAnimation { duration: 120 } }

            IconButton {
                glyph: "✎"
                tip: "Редактировать"
                hoverGlyphColor: Theme.accent
                onClicked: card.editRequested(card.uid)
            }
            IconButton {
                glyph: "🗑"
                glyphSize: 13
                tip: "Удалить"
                hoverGlyphColor: Theme.danger
                hoverBg: Theme.dangerSoft
                onClicked: card.deleteRequested(card.uid)
            }
        }
    }
}
