#!/usr/bin/env python3
import sys
from PyQt6.QtWidgets import QApplication

from src.gui import MainWindow


def main():
    app = QApplication(sys.argv)
    
    # Force dark mode
    dark_stylesheet = """
        QWidget {
            background-color: #1e1e1e;
            color: #ffffff;
        }
        QMainWindow {
            background-color: #1e1e1e;
        }
        QGroupBox {
            border: 1px solid #555555;
            border-radius: 5px;
            margin-top: 10px;
            padding-top: 10px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 5px;
        }
        QPushButton {
            background-color: #2d2d2d;
            border: 1px solid #555555;
            border-radius: 3px;
            padding: 5px;
            color: #ffffff;
        }
        QPushButton:hover {
            background-color: #3d3d3d;
        }
        QPushButton:pressed {
            background-color: #1d1d1d;
        }
        QLineEdit {
            background-color: #2d2d2d;
            border: 1px solid #555555;
            border-radius: 3px;
            padding: 3px;
            color: #ffffff;
        }
        QComboBox {
            background-color: #2d2d2d;
            border: 1px solid #555555;
            border-radius: 3px;
            padding: 3px;
            color: #ffffff;
        }
        QComboBox::drop-down {
            border: none;
        }
        QComboBox::down-arrow {
            image: none;
            border-left: 4px solid transparent;
            border-right: 4px solid transparent;
            border-top: 6px solid #ffffff;
            margin-right: 5px;
        }
        QSlider::groove:horizontal {
            background: #2d2d2d;
            height: 6px;
            border-radius: 3px;
        }
        QSlider::handle:horizontal {
            background: #9C27B0;
            width: 14px;
            margin: -4px 0;
            border-radius: 7px;
        }
        QLabel {
            background-color: transparent;
        }
        QScrollArea {
            border: none;
            background-color: #1e1e1e;
        }
    """
    app.setStyleSheet(dark_stylesheet)
    
    win = MainWindow()
    win.resize(1200, 650)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

