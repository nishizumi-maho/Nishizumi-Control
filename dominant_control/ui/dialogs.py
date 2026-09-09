from __future__ import annotations

from ..foundation import *

class VoiceTestDialog(tk.Toplevel):
    """Dialog for validating voice commands and macro triggers."""

    def __init__(
        self,
        parent,
        app,
        phrases_map: Dict[str, Callable]
    ):
        super().__init__(parent)
        self.app = app
        self.phrases_map = {k.strip().lower(): v for k, v in phrases_map.items()}
        self.title("Voice and Macro Test")
        self.geometry("430x360")

        info = tk.Label(
            self,
            text=(
                "Say one of the configured phrases to fire the macro.\nUse the test button to confirm the microphone and the phrases work."
            ),
            wraplength=400,
            justify="left"
        )
        info.pack(padx=10, pady=(10, 6), anchor="w")

        phrases_text = "\n".join(
            f"• {phrase}" for phrase in sorted(self.phrases_map.keys())
        ) or "No phrase configured."

        tk.Label(
            self,
            text="Available phrases:",
            font=("Arial", 10, "bold")
        ).pack(anchor="w", padx=10)

        tk.Message(
            self,
            text=phrases_text,
            width=400
        ).pack(fill="x", padx=10, pady=(0, 8))

        self.status_var = tk.StringVar(value="Waiting for the test...")
        self.heard_var = tk.StringVar(value="(nothing yet)")

        self.btn_listen = tk.Button(
            self,
            text="🎤 Listen and test",
            command=self.start_listen,
            bg="#ADD8E6"
        )
        self.btn_listen.pack(fill="x", padx=10, pady=4)

        tk.Label(self, textvariable=self.status_var, fg="gray").pack(
            anchor="w", padx=12
        )
        tk.Label(
            self,
            textvariable=self.heard_var,
            font=("Arial", 10, "bold")
        ).pack(anchor="w", padx=12, pady=(0, 8))

        manual = tk.Frame(self)
        manual.pack(fill="x", padx=10, pady=(6, 10))

        tk.Label(manual, text="Run a phrase by hand:").pack(
            anchor="w"
        )
        self.entry_manual = ttk.Entry(manual)
        self.entry_manual.pack(fill="x", pady=2)
        tk.Button(
            manual,
            text="Run the macro",
            command=self.run_manual_phrase,
            bg="#90ee90"
        ).pack(fill="x", pady=2)

    def start_listen(self):
        """Start a one-off listening test."""
        self.btn_listen.config(state="disabled", text="Listening...")
        self.status_var.set("Say the configured command now...")
        self.heard_var.set("(listening)")
        threading.Thread(target=self._listen_worker, daemon=True).start()

    def _listen_worker(self):
        phrase, error = voice_listener.capture_once()

        def finalize():
            self.btn_listen.config(state="normal", text="🎤 Listen and test")
            if error:
                self.status_var.set(f'Error while listening: {error}')
                return

            if phrase is None:
                self.status_var.set("Voice unavailable.")
                return

            normalized = phrase.strip()
            self.heard_var.set(normalized or "(nothing recognized)")

            if not normalized:
                self.status_var.set("No phrase was recognized.")
                return

            triggered = self._trigger_phrase(normalized)
            if triggered:
                self.status_var.set("Macro triggered.")
            else:
                self.status_var.set("Phrase recognized, but no macro is bound to it.")

        self.after(0, finalize)

    def _trigger_phrase(self, phrase: str) -> bool:
        """Execute macro for the given phrase if available."""
        action = self.phrases_map.get(phrase.strip().lower())
        if not action:
            return False

        _CALLBACK_DISPATCHER.submit(action)
        return True

    def run_manual_phrase(self):
        """Trigger macro manually from text input."""
        phrase = self.entry_manual.get().strip().lower()
        if not phrase:
            self.status_var.set("Type a phrase to test.")
            return

        if self._trigger_phrase(phrase):
            self.status_var.set("Macro run by hand.")
        else:
            self.status_var.set("No macro is bound to that phrase.")

# ======================================================================
# DEVICE SELECTOR DIALOG
# ======================================================================
class DeviceSelector(tk.Toplevel):
    """
    Dialog for selecting which USB devices the application can use.
    """

    def __init__(self, parent, current_allowed: List[str], callback: Callable[[List[str]], None]):
        super().__init__(parent)
        self.title("Manage Input Devices")
        self.geometry("450x400")
        self.callback = callback

        tk.Label(
            self,
            text="Choose which devices the application may use",
            font=("Arial", 10, "bold"),
            pady=10
        ).pack()

        tk.Label(
            self,
            text="Check or uncheck to allow or block the device",
            fg="gray"
        ).pack()

        self.frame_list = tk.Frame(self)
        self.frame_list.pack(fill="both", expand=True, padx=10, pady=10)

        self.check_vars: Dict[str, tk.BooleanVar] = {}
        all_devices = input_manager.get_all_devices()

        for idx, name in all_devices:
            var = tk.BooleanVar()
            if current_allowed:
                var.set(name in current_allowed)
            else:
                # First run defaults to nothing selected
                var.set(False)

            chk = tk.Checkbutton(
                self.frame_list, 
                text=name, 
                variable=var, 
                anchor="w"
            )
            chk.pack(fill="x")
            self.check_vars[name] = var

        tk.Button(
            self,
            text="Save and apply",
            command=self.save,
            bg="#90ee90",
            height=2
        ).pack(fill="x", padx=10, pady=10)

    def save(self):
        """Save device selection and close dialog."""
        final_list = [name for name, var in self.check_vars.items() if var.get()]
        self.callback(final_list)
        self.destroy()

__all__ = [name for name in globals() if not name.startswith('__')]
