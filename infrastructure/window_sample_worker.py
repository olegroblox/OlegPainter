"""Minimal subprocess entry point; deliberately does not import an app shell."""
from infrastructure.window_sampling import run_worker

if __name__ == "__main__":
    raise SystemExit(run_worker())
