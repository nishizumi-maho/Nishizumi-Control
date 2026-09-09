"""Render the original Traction window from the integrated coach state."""

from __future__ import annotations

from .client import OverlayBridgeClient
from .ptbr import localize_tk_tree, translate_text


def run() -> int:
    from dominant_control.vendor.nishizumi_tools import Nishizumi_Traction as traction

    client = OverlayBridgeClient("traction")

    class BridgeOnlyIRSDK:
        """Never open a second live pyirsdk connection in the renderer."""

        is_initialized = False
        is_connected = False

        def startup(self) -> bool:
            return False

        def shutdown(self) -> None:
            return None

        def __getitem__(self, _name):
            return None

    traction.irsdk.IRSDK = BridgeOnlyIRSDK

    class BridgeTractionOverlay(traction.TractionCircleOverlay):
        def _load_ibt_reference(self) -> None:
            file_path = traction.filedialog.askopenfilename(
                title="Load IBT reference",
                filetypes=[("iRacing telemetry", "*.ibt"), ("All files", "*.*")],
            )
            if file_path:
                client.command("load_ibt", path=file_path)

        def _clear_ibt_reference(self) -> None:
            client.command("clear_ibt")

        # `Nishizumi_Traction.py` is vendored byte-for-byte (see
        # vendor/nishizumi_tools/PROVENANCE.md) and its `_render_circle`
        # deletes and recreates every canvas item on each tick, which
        # flickers. Adapting behaviour here (as this class already does for
        # the two methods above) fixes that without touching the vendored
        # file: static items are created once and cached per canvas, and
        # later ticks only move/recolor them in place.
        def _render_circle(self, canvas, long_g, lat_g, usage_pct, *, compact):
            cache = self.__dict__.setdefault("_circle_items", {})
            canvas.update_idletasks()
            w = max(100, int(canvas.winfo_width()))
            h = max(100, int(canvas.winfo_height()))
            cx = w // 2
            cy = h // 2 - (2 if compact else 8)
            radius = min(w, h) * (0.35 if compact else 0.34)

            limit = max(0.8, self.estimated_limit_g)
            scale = radius / limit
            dot_x = cx + lat_g * scale
            dot_y = cy - long_g * scale
            dot_radius = 6 if compact else 7
            usage_color = (
                traction.GOOD
                if usage_pct < 85
                else traction.MEDIUM if usage_pct < 97 else traction.BAD
            )
            usage_text = (
                f'{usage_pct:.0f}% of the estimated limit'
                if compact
                else f'Using {usage_pct:.0f}% of the estimated limit'
            )

            items = cache.get(canvas)
            layout_key = (w, h, compact)
            if items is None or items.get("layout_key") != layout_key:
                items = self._build_circle_items(canvas, w, h, cx, cy, radius, compact, layout_key)
                cache[canvas] = items

            canvas.coords(items["accel_line"], cx, cy, dot_x, dot_y)
            canvas.itemconfigure(items["accel_line"], fill=usage_color)
            canvas.coords(
                items["dot"],
                dot_x - dot_radius, dot_y - dot_radius,
                dot_x + dot_radius, dot_y + dot_radius,
            )
            canvas.itemconfigure(items["usage_text"], text=usage_text, fill=usage_color)

            gauge_geo = items.get("gauge_geo")
            if gauge_geo is not None:
                gx0, gy0, gy1, gauge_w = gauge_geo
                fill_x = gx0 + int(max(0.0, min(1.0, usage_pct / 100.0)) * gauge_w)
                canvas.coords(items["gauge_fill"], gx0, gy0, fill_x, gy1)
                canvas.itemconfigure(items["gauge_fill"], fill=usage_color)

        def _build_circle_items(self, canvas, w, h, cx, cy, radius, compact, layout_key):
            canvas.delete("all")
            items = {"layout_key": layout_key}

            canvas.create_oval(cx - radius, cy - radius, cx + radius, cy + radius, outline=traction.RING, width=2)
            for frac in (0.2, 0.4, 0.6, 0.8):
                rr = radius * frac
                canvas.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, outline=traction.GRID, width=1)

            canvas.create_line(cx - radius, cy, cx + radius, cy, fill=traction.GRID, width=1)
            canvas.create_line(cx, cy - radius, cx, cy + radius, fill=traction.GRID, width=1)

            items["accel_line"] = canvas.create_line(cx, cy, cx, cy, fill=traction.GOOD, width=3)
            items["dot"] = canvas.create_oval(cx, cy, cx, cy, fill=traction.DOT, outline="")
            canvas.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill=traction.SUBTEXT, outline="")

            if compact:
                canvas.create_text(cx, 20, text="Traction circle", fill=traction.TEXT, font=("Segoe UI Semibold", 12))
                items["usage_text"] = canvas.create_text(
                    cx, h - 22, text="", fill=traction.GOOD, font=("Segoe UI Semibold", 10)
                )
                return items

            label_y = cy + radius + 26
            canvas.create_text(cx, 24, text="Traction circle", fill=traction.TEXT, font=("Segoe UI Semibold", 13))
            canvas.create_text(
                cx, 46,
                text="LongAccel ↑ / brake    •    throttle ↓    •    LatAccel ← →",
                fill=traction.SUBTEXT, font=("Segoe UI", 9),
            )
            items["usage_text"] = canvas.create_text(
                cx, label_y, text="", fill=traction.GOOD, font=("Segoe UI Semibold", 11)
            )

            gauge_w = min(int(w * 0.64), 360)
            gauge_h = 12
            gx0 = cx - gauge_w // 2
            gy0 = label_y + 18
            gx1 = gx0 + gauge_w
            gy1 = gy0 + gauge_h
            canvas.create_rectangle(gx0, gy0, gx1, gy1, fill=traction.PANEL_2, outline=traction.BORDER)
            items["gauge_fill"] = canvas.create_rectangle(gx0, gy0, gx0, gy1, fill=traction.GOOD, outline="")
            items["gauge_geo"] = (gx0, gy0, gy1, gauge_w)
            return items

    app = BridgeTractionOverlay()
    localize_tk_tree(app.root)
    last_show_generation = -1
    applying_remote = False

    simple_vars = (
        "status_var",
        "context_var",
        "reference_var",
        "headline_var",
        "subheadline_var",
        "footer_var",
        "settings_hint_var",
        "circle_caption_var",
    )
    cards = ("card_total", "card_long", "card_lat", "card_limit")
    coach_cards = ("coach_card_1", "coach_card_2", "coach_card_3")

    def send_settings(*_args) -> None:
        if applying_remote:
            return
        client.command(
            "settings",
            laps_for_feedback=int(app.laps_for_feedback_var.get()),
            incident_free_only=bool(app.incident_free_only_var.get()),
        )

    app.laps_for_feedback_var.trace_add("write", send_settings)
    app.incident_free_only_var.trace_add("write", send_settings)

    def poll() -> None:
        nonlocal last_show_generation, applying_remote
        response = client.state()
        if response:
            state = response.get("state") or {}
            for name in simple_vars:
                if name in state:
                    getattr(app, name).set(translate_text(str(state[name])))
            for name in cards:
                values = state.get(name)
                if isinstance(values, list) and len(values) >= 2:
                    getattr(app, name).set(
                        translate_text(str(values[0])),
                        translate_text(str(values[1])),
                    )
            for name in coach_cards:
                values = state.get(name)
                if isinstance(values, list) and len(values) >= 3:
                    getattr(app, name).set(
                        translate_text(str(values[0])),
                        translate_text(str(values[1])),
                        translate_text(str(values[2])),
                    )
            circle = state.get("circle") or {}
            if isinstance(circle, dict):
                app._draw_circle(
                    float(circle.get("long_g", 0.0)),
                    float(circle.get("lat_g", 0.0)),
                    float(circle.get("usage_pct", 0.0)),
                )
            settings = state.get("settings") or {}
            if isinstance(settings, dict):
                applying_remote = True
                try:
                    app.laps_for_feedback_var.set(
                        int(settings.get("laps_for_feedback", 5))
                    )
                    app.incident_free_only_var.set(
                        bool(settings.get("incident_free_only", True))
                    )
                finally:
                    applying_remote = False
            show_generation = int(response.get("show_generation", 0))
            if show_generation != last_show_generation:
                last_show_generation = show_generation
                app.root.deiconify()
                app.root.lift()
                app.root.attributes("-topmost", True)
            localize_tk_tree(app.root)
        if app.root.winfo_exists():
            app.root.after(75, poll)

    poll()
    app.root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
