import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont
from config import APP_STYLESHEET
from ui import MainWindow

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet(APP_STYLESHEET)
    app.setFont(QFont("Segoe UI", 10))

    window = MainWindow()
    window.show()

    sys.exit(app.exec())