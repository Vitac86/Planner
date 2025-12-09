import flet as ft

HAS_WRAP = hasattr(ft, "Wrap")
TEXT_ACCEPTS_DECORATION = "decoration" in ft.Text.__init__.__code__.co_varnames


def wrap_row(controls, spacing=12, run_spacing=8):
    if HAS_WRAP:
        return ft.Wrap(controls=controls, spacing=spacing, run_spacing=run_spacing)
    return ft.Row(controls=controls, wrap=True, spacing=spacing, run_spacing=run_spacing)


def strike_text(text: str, *, tooltip: str | None = None, strike: bool = False):
    if TEXT_ACCEPTS_DECORATION:
        t = ft.Text(text, tooltip=tooltip)
        if strike:
            t.decoration = ft.TextDecoration.LINE_THROUGH
        return t
    return ft.Text(
        text,
        tooltip=tooltip,
        style=ft.TextStyle(decoration=ft.TextDecoration.LINE_THROUGH if strike else None),
    )
