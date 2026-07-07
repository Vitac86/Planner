import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: card

    property string uid: ""
    property string title: ""
    property string notes: ""
    property string timeLabel: ""
    property bool isAllDay: false
    property int priority: 0
    property bool completed: false
    signal toggled(string uid)

    implicitHeight: content.implicitHeight + 20
    radius: 12
    color: "#FFFFFF"
    border.color: "#E6E8F0"
    border.width: 1

    RowLayout {
        id: content
        anchors.fill: parent
        anchors.leftMargin: 12
        anchors.rightMargin: 14
        anchors.topMargin: 10
        anchors.bottomMargin: 10
        spacing: 10

        CheckBox {
            checked: card.completed
            onToggled: card.toggled(card.uid)
            Layout.alignment: Qt.AlignVCenter
        }

        // индикатор приоритета
        Rectangle {
            width: 4
            radius: 2
            Layout.fillHeight: true
            color: card.priority >= 2 ? "#E5484D"
                 : card.priority === 1 ? "#F5A524" : "#D9DCE7"
        }

        ColumnLayout {
            spacing: 2
            Layout.fillWidth: true

            Label {
                text: card.title
                font.pixelSize: 14
                font.strikeout: card.completed
                color: card.completed ? "#9AA0B4" : "#23283D"
                elide: Text.ElideRight
                Layout.fillWidth: true
            }
            Label {
                text: card.notes
                visible: card.notes.length > 0
                font.pixelSize: 12
                color: "#8A90A6"
                elide: Text.ElideRight
                Layout.fillWidth: true
            }
        }

        Rectangle {
            visible: card.timeLabel.length > 0
            radius: 8
            color: card.isAllDay ? "#EAF6EE" : "#EEF1FE"
            implicitWidth: timeText.implicitWidth + 16
            implicitHeight: 24

            Label {
                id: timeText
                anchors.centerIn: parent
                text: card.timeLabel
                font.pixelSize: 12
                color: card.isAllDay ? "#2E7D46" : "#4F6BED"
            }
        }
    }
}
