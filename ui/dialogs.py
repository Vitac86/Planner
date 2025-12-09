import flet as ft


def open_alert_dialog(page: ft.Page, *, title: str, content: ft.Control, actions: list[ft.Control]):
    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text(title),
        content=content,
        actions=actions,
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.dialog = dlg
    dlg.open = True
    page.update()
    return dlg


def close_alert_dialog(page: ft.Page):
    if page.dialog:
        page.dialog.open = False
        page.update()


def open_overlay(page: ft.Page, content: ft.Control):
    backdrop = ft.Container(expand=True, bgcolor=ft.colors.with_opacity(0.40, ft.colors.BLACK))
    layer = ft.Stack([backdrop, content])
    page.overlay.append(layer)
    page.update()
    return layer


def close_overlay(page: ft.Page, layer: ft.Control | None):
    if layer is None:
        return
    try:
        page.overlay.remove(layer)
    except ValueError:
        pass
    page.update()
