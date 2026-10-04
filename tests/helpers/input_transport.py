"""Explicit recording device for headless tests; never injects native input."""
class RecordingInputBackend:
    def __init__(self):
        self.events = []

    def reset(self):
        pass

    def bind_target(self, region, focus=True):
        pass

    def unbind_target(self):
        pass

    def check_target(self):
        pass

    def move(self, x, y):
        self.events.append(("move", x, y))

    def button(self, button, down):
        self.events.append(("down" if down else "up", button))

    def send_keys(self, sequence):
        self.events.append(("keys", sequence))

    def write_text(self, text):
        self.events.append(("text", text))

    def key(self, key, down):
        self.events.append(("key_down" if down else "key_up", key))
