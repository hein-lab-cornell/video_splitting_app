import os
import sys

# opencv-python (the non-headless build) ships its own Qt plugins and, on import,
# points QT_QPA_PLATFORM_PLUGIN_PATH at them. On Linux those clash with PySide6's
# Qt and the app fails with "Could not load the Qt platform plugin 'xcb'".
# Import cv2 first, then drop that override so PySide6 uses its own plugins.
import cv2  # noqa: F401,E402

for _var in ("QT_QPA_PLATFORM_PLUGIN_PATH", "QT_PLUGIN_PATH"):
    if "cv2" in os.environ.get(_var, ""):
        os.environ.pop(_var)

from PySide6.QtWidgets import QApplication  # noqa: E402

from .main_window import MainWindow  # noqa: E402


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Behavior Clipper")
    app.setStyle("Fusion")
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
