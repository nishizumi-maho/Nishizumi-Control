"""Interface coordinator for Dominant Control."""

class LapDistCaptureCoordinator:
    def __init__(self, app):
        self.app = app

    def begin(self, *_args, **_kwargs):
        return False

    def cancel(self, *_args, **_kwargs):
        return None

    def cancel_for_row(self, *_args, **_kwargs):
        return None

    def cancel_for_owner(self, *_args, **_kwargs):
        return None

    def handle_hotkey(self, *_args, **_kwargs):
        return False
