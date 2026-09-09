"""Render the original Tk pit overlay from the integrated calibrator state."""

from __future__ import annotations

from .client import OverlayBridgeClient
from .ptbr import localize_tk_tree, translate_text


def run() -> int:
    from dominant_control.vendor.nishizumi_tools import nishizumi_pitcalibrator as pit

    client = OverlayBridgeClient("pit")

    class BridgeOnlyIRSDK:
        """Never touch shared memory from this render-only process."""

        is_initialized = False
        is_connected = False

        def startup(self) -> bool:
            return False

        def shutdown(self) -> None:
            return None

        def __getitem__(self, _name):
            return None

    pit.irsdk.IRSDK = BridgeOnlyIRSDK
    app = pit.PitCalibratorApp()
    localize_tk_tree(app.root)
    app.arm_btn.configure(command=lambda: client.command("toggle_arm"))
    app.tire_btn.configure(command=lambda: client.command("mark_tire"))
    app.root.protocol("WM_DELETE_WINDOW", app.root.destroy)
    last_show_generation = -1

    variable_names = (
        "connection_var",
        "context_var",
        "arm_state_var",
        "status_var",
        "live_total_var",
        "live_service_var",
        "live_base_var",
        "live_fuel_var",
        "live_rate_var",
        "live_tire_var",
        "pending_fuel_var",
        "saved_total_var",
        "saved_service_var",
        "saved_base_var",
        "saved_fuel_var",
        "saved_rate_var",
        "saved_tire_var",
    )

    def poll() -> None:
        nonlocal last_show_generation
        response = client.state()
        if response:
            state = response.get("state") or {}
            for name in variable_names:
                if name in state:
                    getattr(app, name).set(translate_text(str(state[name])))
            armed = bool(state.get("armed", False))
            app.arm_btn.configure(
                text="CANCEL" if armed else "ARM",
                bg=app.BTN_ACTIVE if armed else app.BTN,
            )
            show_generation = int(response.get("show_generation", 0))
            if show_generation != last_show_generation:
                last_show_generation = show_generation
                app.root.deiconify()
                app.root.lift()
                app.root.attributes("-topmost", True)
            localize_tk_tree(app.root)
        if app.root.winfo_exists():
            app.root.after(100, poll)

    poll()
    app.root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
