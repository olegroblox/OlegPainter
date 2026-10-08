"""Default OlegPainter launch: the native Qt Quick application."""
import sys

from infrastructure.window_sampling import dispatch_worker

if __name__ == "__main__":
    dispatch_worker()
    if sys.argv[1:2] == ["--self-test"]:
        from ui.quick.self_test import run
        sys.exit(run(sys.argv[2] if len(sys.argv) > 2 else None))
    if "--install-update" in sys.argv:
        # A downloaded version replacing the program files (UPDATE-001), not the window.
        from ui.quick.update_installer import run
        sys.exit(run(sys.argv))

from quick_main import main

if __name__ == "__main__":
    sys.exit(main())
