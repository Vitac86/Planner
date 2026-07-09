pragma Singleton
import QtQuick

// Дизайн-токены нового десктопа. Единственный источник цветов, отступов
// и типографики: страницы и компоненты не хардкодят hex-значения.
QtObject {
    // ---- фон и поверхности ----
    readonly property color background: "#F3F4F8"
    readonly property color surface: "#FFFFFF"
    readonly property color surfaceHover: "#F6F7FB"
    readonly property color surfacePressed: "#EEF0F7"
    readonly property color border: "#E5E7EF"
    readonly property color borderStrong: "#D5D9E5"

    // ---- текст ----
    readonly property color textPrimary: "#1E2434"
    readonly property color textSecondary: "#5A6072"
    readonly property color textMuted: "#9AA0B4"
    readonly property color textOnAccent: "#FFFFFF"

    // ---- акцент (индиго) ----
    readonly property color accent: "#4F6BED"
    readonly property color accentHover: "#4360DC"
    readonly property color accentPressed: "#3A55C8"
    readonly property color accentSoft: "#EDF0FE"
    readonly property color accentSoftBorder: "#D4DCFB"

    // ---- статусы ----
    readonly property color danger: "#D93840"
    readonly property color dangerHover: "#C52F37"
    readonly property color dangerSoft: "#FDECED"
    readonly property color success: "#2E7D46"
    readonly property color successSoft: "#EAF6EE"
    readonly property color successSoftBorder: "#C9E7D3"
    readonly property color warningText: "#8A6D1F"
    readonly property color warningSoft: "#FDF4E0"
    readonly property color warningSoftBorder: "#F1E2B8"

    // ---- геометрия ----
    readonly property int radiusSmall: 8
    readonly property int radiusMedium: 12
    readonly property int radiusLarge: 16
    readonly property int spacingXs: 4
    readonly property int spacingSm: 8
    readonly property int spacingMd: 12
    readonly property int spacingLg: 16
    readonly property int spacingXl: 24

    // ---- типографика ----
    readonly property int fontCaption: 12
    readonly property int fontBody: 14
    readonly property int fontSubtitle: 15
    readonly property int fontTitle: 20
    readonly property int fontDisplay: 26

    // ---- приоритеты (уровни и цвета — как в core/priorities.py старого app) ----
    readonly property var priorityNames: ["Без приоритета", "Низкий", "Средний", "Высокий"]
    readonly property var priorityFg: ["#64748B", "#0EA5E9", "#F59E0B", "#EF4444"]
    readonly property var priorityBg: ["#E2E8F0", "#E0F2FE", "#FEF3C7", "#FEE2E2"]

    function clampPriority(p) {
        return Math.max(0, Math.min(priorityNames.length - 1, p || 0))
    }
    function priorityName(p) { return priorityNames[clampPriority(p)] }
    function priorityColor(p) { return priorityFg[clampPriority(p)] }
    function priorityBgColor(p) { return priorityBg[clampPriority(p)] }
}
