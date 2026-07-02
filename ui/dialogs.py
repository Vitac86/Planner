import flet as ft


def _has_method(obj, name: str) -> bool:
    try:
        return callable(getattr(obj, name, None))
    except Exception:
        return False


def open_alert_dialog(
    page: ft.Page,
    *,
    title: str,
    content: ft.Control,
    actions: list[ft.Control],
) -> ft.AlertDialog:
    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text(title),
        content=content,
        actions=actions,
        actions_alignment=ft.MainAxisAlignment.END,
    )

    # Сохраняем ссылку, чтобы close_alert_dialog(page) всегда знал что закрывать
    setattr(page, "_planner_active_dialog", dlg)

    # Оставляем page.dialog для совместимости с твоим app_shell.py (Escape и проверки)
    try:
        page.dialog = dlg
    except Exception:
        pass

    # Новый/актуальный способ показа диалогов
    if _has_method(page, "open"):
        page.open(dlg)
    else:
        # Фоллбек для старых версий
        dlg.open = True
        page.update()

    return dlg


def close_alert_dialog(page: ft.Page):
    dlg = getattr(page, "_planner_active_dialog", None) or getattr(page, "dialog", None)
    if dlg is None:
        return

    # Закрываем “правильным” способом, если доступно
    if _has_method(page, "close"):
        try:
            page.close(dlg)
        except Exception:
            try:
                dlg.open = False
                page.update()
            except Exception:
                pass
        return

    # Фоллбек
    try:
        dlg.open = False
        page.update()
    except Exception:
        pass


def open_overlay(page: ft.Page, content: ft.Control):
    """Показывает кастомный оверлей поверх страницы.

    Слой и фон помечены тегами planner_layer/planner_backdrop, чтобы
    AppShell.cleanup_overlays() отличал их от системных контролов Flet
    (DatePicker, TimePicker, AlertDialog и т.п.).
    """
    layer = ft.Stack(data="planner_layer")

    backdrop = ft.Container(
        expand=True,
        bgcolor=ft.Colors.with_opacity(0.40, ft.Colors.BLACK),
        data="planner_backdrop",
        on_click=lambda e: close_overlay(page, layer),
    )
    layer.controls = [backdrop, content]
    # python-атрибут "open": по нему cleanup_overlays и Esc-обработчик
    # отличают активный слой от закрытого
    layer.open = True
    page.overlay.append(layer)
    page.update()
    return layer


def close_overlay(page: ft.Page, layer: ft.Control | None):
    if layer is None:
        return
    try:
        layer.open = False
    except Exception:
        pass
    try:
        page.overlay.remove(layer)
    except ValueError:
        pass
    page.update()
