import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

// Responsive mode switch for Day / Work week / Week.
Item {
    id: control
    objectName: "calendarViewModeSwitch"

    property var options: []
    property string current: "week"
    property bool compact: false
    signal selected(string value)

    implicitHeight: 38
    implicitWidth: compact ? 184 : segments.implicitWidth

    SegmentedControl {
        id: segments
        visible: !control.compact
        anchors.fill: parent
        options: control.options
        current: control.current
        onSelected: value => control.selected(value)
    }

    ComboBox {
        id: combo
        visible: control.compact
        anchors.fill: parent
        model: control.options
        textRole: "label"
        valueRole: "value"
        font.pixelSize: Theme.fontBody
        font.family: Theme.fontFamily
        Accessible.name: "Режим отображения календаря"

        function syncIndex() {
            for (var i = 0; i < control.options.length; ++i) {
                if (control.options[i].value === control.current) {
                    currentIndex = i
                    return
                }
            }
        }
        Component.onCompleted: syncIndex()
        onModelChanged: syncIndex()
        onActivated: index => control.selected(control.options[index].value)
        Connections {
            target: control
            function onCurrentChanged() { combo.syncIndex() }
        }
    }
}
