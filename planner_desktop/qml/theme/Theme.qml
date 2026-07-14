pragma Singleton
import QtQuick

// Дизайн-токены нового десктопа. Единственный источник цветов, отступов,
// теней и типографики: страницы и компоненты не хардкодят hex-значения.
QtObject {
    // ---- шрифт ----
    readonly property string fontFamily: "Segoe UI"

    // ---- фон и поверхности ----
    readonly property color background: "#F1F3F8"
    readonly property color backgroundAlt: "#EAEDF4"   // низ фонового градиента
    readonly property color surface: "#FFFFFF"
    readonly property color surfaceMuted: "#F7F8FC"     // мягкая внутренняя поверхность
    readonly property color surfaceHover: "#F4F6FB"
    readonly property color surfacePressed: "#ECEFF7"
    readonly property color border: "#E6E8F0"
    readonly property color borderStrong: "#D3D8E5"

    // ---- текст ----
    readonly property color textPrimary: "#1B2030"
    readonly property color textSecondary: "#565D72"
    readonly property color textMuted: "#98A0B5"
    readonly property color textOnAccent: "#FFFFFF"

    // ---- акцент (индиго) ----
    readonly property color accent: "#4F6BED"
    readonly property color accentHover: "#4360DC"
    readonly property color accentPressed: "#3A55C8"
    readonly property color accentGradTop: "#5A75F0"
    readonly property color accentGradBottom: "#4560E4"
    readonly property color accentSoft: "#ECF0FE"
    readonly property color accentSoftHover: "#E1E8FE"
    readonly property color accentSoftBorder: "#D4DCFB"

    // ---- статусы ----
    readonly property color danger: "#E0454D"
    readonly property color dangerHover: "#CC3A42"
    readonly property color dangerSoft: "#FDECED"
    readonly property color success: "#2E9E5B"
    readonly property color successSoft: "#E7F6EC"
    readonly property color successSoftBorder: "#C6E9D2"
    readonly property color warningText: "#9A7418"
    readonly property color warningSoft: "#FDF3DE"
    readonly property color warningSoftBorder: "#F1E2B0"

    // ---- геометрия ----
    readonly property int radiusSmall: 8
    readonly property int radiusMedium: 12
    readonly property int radiusLarge: 16
    readonly property int radiusXl: 20
    readonly property int radiusPill: 999
    readonly property int spacingXs: 4
    readonly property int spacingSm: 8
    readonly property int spacingMd: 12
    readonly property int spacingLg: 16
    readonly property int spacingXl: 24
    readonly property int spacing2xl: 32

    // ---- тени / высота (для MultiEffect) ----
    readonly property color shadowColor: "#0F1B3D"
    readonly property int shadowBlurMax: 48
    // мягкая покоящаяся карточка
    readonly property real elevCardBlur: 0.34
    readonly property real elevCardOpacity: 0.10
    readonly property int elevCardY: 4
    // выделенное состояние / hover
    readonly property real elevHoverBlur: 0.48
    readonly property real elevHoverOpacity: 0.18
    readonly property int elevHoverY: 10
    // диалоги / поповеры
    readonly property real elevDialogBlur: 0.62
    readonly property real elevDialogOpacity: 0.30
    readonly property int elevDialogY: 22

    // ---- прочее ----
    readonly property color focusRing: "#B7C4FA"
    readonly property color ringTrack: "#E7EAF3"
    readonly property color scrim: "#232842"

    // ---- типографика ----
    readonly property int fontCaption: 12
    readonly property int fontBody: 14
    readonly property int fontSubtitle: 15
    readonly property int fontTitle: 20
    readonly property int fontDisplay: 26

    // ---- приоритеты (уровни и цвета — как в core/priorities.py старого app) ----
    readonly property var priorityNames: ["Без приоритета", "Низкий", "Средний", "Высокий"]
    readonly property var priorityFg: ["#64748B", "#0EA5E9", "#F59E0B", "#EF4444"]
    readonly property var priorityBg: ["#EEF1F6", "#E4F3FD", "#FEF3D6", "#FDE7E7"]

    function clampPriority(p) {
        return Math.max(0, Math.min(priorityNames.length - 1, p || 0))
    }
    function priorityName(p) { return priorityNames[clampPriority(p)] }
    function priorityColor(p) { return priorityFg[clampPriority(p)] }
    function priorityBgColor(p) { return priorityBg[clampPriority(p)] }

    // ---- русские названия месяцев (для заголовка месячной сетки) ----
    readonly property var monthsNominative: [
        "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
        "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"
    ]
    function monthName(monthZeroBased) {
        var m = Math.max(0, Math.min(11, monthZeroBased))
        return monthsNominative[m]
    }

    // Русское склонение существительного при числе:
    // plural(3, "задача", "задачи", "задач") -> "задачи".
    function plural(n, one, few, many) {
        var m10 = n % 10, m100 = n % 100
        if (m10 === 1 && m100 !== 11) return one
        if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few
        return many
    }
}
