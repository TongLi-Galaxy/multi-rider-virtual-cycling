from __future__ import annotations

from PySide6 import QtGui, QtWidgets


class NoWheelComboBox(QtWidgets.QComboBox):
    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:  # type: ignore[override]
        event.ignore()


class NoWheelSpinBox(QtWidgets.QSpinBox):
    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:  # type: ignore[override]
        event.ignore()


class NoWheelDoubleSpinBox(QtWidgets.QDoubleSpinBox):
    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:  # type: ignore[override]
        event.ignore()
