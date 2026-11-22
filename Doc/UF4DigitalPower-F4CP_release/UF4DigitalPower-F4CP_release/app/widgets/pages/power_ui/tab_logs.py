# -*- coding: utf-8 -*-
"""Full-height clean protocol and status log tab."""

from __future__ import annotations

from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import ComboBox, FluentIcon as FIF, PushButton, TextEdit

from .theme_tokens import card_frame_qss, current_palette, make_mono_font


class LogsTab(QWidget):
    """Dedicated spacious log console so logs never clutter the main dashboard."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PowerLogsTab")

        self.logCard = QFrame(self)
        self.logCard.setObjectName("LabLogCard")

        self.titleLabel = QLabel("协议 / 状态日志", self.logCard)
        self.logLevelCombo = ComboBox(self.logCard)
        self.logLevelCombo.addItem("普通", userData="normal")
        self.logLevelCombo.addItem("详细", userData="verbose")
        self.logLevelCombo.setFixedWidth(100)
        self.logLevelCombo.setFixedHeight(32)

        self.copyButton = PushButton(FIF.COPY, "复制", self.logCard)
        self.clearLogButton = PushButton(FIF.BROOM, "清空", self.logCard)
        self.copyButton.setFixedHeight(32)
        self.clearLogButton.setFixedHeight(32)
        self.copyButton.clicked.connect(self._copyAll)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        top.addWidget(self.titleLabel)
        top.addStretch(1)
        top.addWidget(self.logLevelCombo)
        top.addWidget(self.copyButton)
        top.addWidget(self.clearLogButton)

        self.logEdit = TextEdit(self.logCard)
        self.logEdit.setReadOnly(True)
        self.logEdit.setFont(make_mono_font(10, QFont.Normal))
        self.logEdit.document().setMaximumBlockCount(600)

        layout = QVBoxLayout(self.logCard)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        layout.addLayout(top)
        layout.addWidget(self.logEdit, 1)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.logCard)
        self.refreshTheme()

    def _copyAll(self) -> None:
        QApplication.clipboard().setText(self.logEdit.toPlainText())

    def refreshTheme(self) -> None:
        p = current_palette()
        self.logCard.setStyleSheet(card_frame_qss("LabLogCard", p, radius=12))
        self.titleLabel.setStyleSheet(f"color: {p.text_primary}; font-size: 14px; font-weight: 600; background: transparent;")
