# -*- coding: utf-8 -*-
"""Minimalist Lab Instrument design tokens (Light primary + Dark support)."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt5.QtGui import QFont
from qfluentwidgets import isDarkTheme


@dataclass(frozen=True)
class LabPalette:
    dark: bool
    canvas: str
    surface: str
    surface_subtle: str
    border: str
    border_strong: str
    text_primary: str
    text_secondary: str
    text_muted: str
    accent: str
    accent_hover: str
    accent_soft_bg: str
    accent_soft_border: str
    vout: str
    iout: str
    pout: str
    vin: str
    ok_fg: str
    ok_bg: str
    ok_border: str
    warn_fg: str
    warn_bg: str
    danger_fg: str
    danger_bg: str
    danger_border: str


LIGHT_PALETTE = LabPalette(
    dark=False,
    canvas="#F3F5F9",
    surface="#FFFFFF",
    surface_subtle="#F8FAFC",
    border="#E2E8F0",
    border_strong="#CBD5E1",
    text_primary="#0F172A",
    text_secondary="#475569",
    text_muted="#94A3B8",
    accent="#2563EB",
    accent_hover="#1D4ED8",
    accent_soft_bg="#EFF6FF",
    accent_soft_border="#BFDBFE",
    vout="#059669",
    iout="#D97706",
    pout="#0891B2",
    vin="#475569",
    ok_fg="#047857",
    ok_bg="#ECFDF5",
    ok_border="#A7F3D0",
    warn_fg="#B45309",
    warn_bg="#FFFBEB",
    danger_fg="#DC2626",
    danger_bg="#FEF2F2",
    danger_border="#FECACA",
)

DARK_PALETTE = LabPalette(
    dark=True,
    canvas="#11151C",
    surface="#1A202C",
    surface_subtle="#212836",
    border="#2B3445",
    border_strong="#3B475E",
    text_primary="#F1F5F9",
    text_secondary="#94A3B8",
    text_muted="#64748B",
    accent="#3B82F6",
    accent_hover="#2563EB",
    accent_soft_bg="rgba(59, 130, 246, 0.14)",
    accent_soft_border="rgba(59, 130, 246, 0.35)",
    vout="#34D399",
    iout="#FBBF24",
    pout="#22D3EE",
    vin="#93C5FD",
    ok_fg="#34D399",
    ok_bg="rgba(16, 185, 129, 0.16)",
    ok_border="rgba(16, 185, 129, 0.34)",
    warn_fg="#FBBF24",
    warn_bg="rgba(245, 158, 11, 0.15)",
    danger_fg="#F87171",
    danger_bg="rgba(239, 68, 68, 0.16)",
    danger_border="rgba(239, 68, 68, 0.36)",
)


def current_palette() -> LabPalette:
    return DARK_PALETTE if isDarkTheme() else LIGHT_PALETTE


def make_mono_font(point_size: int, weight: int = QFont.DemiBold) -> QFont:
    font = QFont()
    font.setFamilies(["Cascadia Mono", "Consolas", "Segoe UI", "Microsoft YaHei UI"])
    font.setPointSize(point_size)
    font.setWeight(weight)
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


def card_frame_qss(object_name: str, p: LabPalette, radius: int = 12) -> str:
    return f"""
        QFrame#{object_name} {{
            background-color: {p.surface};
            border: 1px solid {p.border};
            border-radius: {radius}px;
        }}
    """
