"""Default OlegPainter launch: the native Qt Quick application."""
import sys

from infrastructure.window_sampling import dispatch_worker

if __name__ == "__main__":
    dispatch_worker()

from quick_main import main

if __name__ == "__main__":
    sys.exit(main())
