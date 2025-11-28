#!/usr/bin/env python3
import sys
from PyQt6.QtWidgets import QApplication

from src.gui import MainWindow


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.resize(1200, 650)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

