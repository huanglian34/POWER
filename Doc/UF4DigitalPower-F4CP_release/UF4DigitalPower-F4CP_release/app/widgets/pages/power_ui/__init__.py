# -*- coding: utf-8 -*-
"""Modular minimalist lab UI components for PowerPage."""

from .theme_tokens import LabPalette, card_frame_qss, current_palette, make_mono_font
from .preset_store import DEFAULT_OUTPUT_PRESETS, OutputPreset, OutputPresetStore
from .lab_scope import LabScopeWidget
from .topbar import PowerTopBar
from .tab_dashboard import DashboardTab
from .tab_calibration import CalibrationTab, DEFAULT_VOUT_CAL_TARGETS_V
from .tab_protection import ProtectionTab
from .tab_logs import LogsTab

__all__ = [
    "CalibrationTab",
    "DEFAULT_OUTPUT_PRESETS",
    "DEFAULT_VOUT_CAL_TARGETS_V",
    "DashboardTab",
    "LabPalette",
    "LabScopeWidget",
    "LogsTab",
    "OutputPreset",
    "OutputPresetStore",
    "PowerTopBar",
    "ProtectionTab",
    "card_frame_qss",
    "current_palette",
    "make_mono_font",
]
