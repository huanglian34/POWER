# -*- coding: utf-8 -*-
"""Persistent customizable output voltage/current memory presets (M1..M6)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from config import CTX, get_logger

logger = get_logger("PowerPresets")

PRESET_COUNT = 6
MAX_VOLTAGE_V = 65.0
MAX_CURRENT_A = 10.0


@dataclass(frozen=True)
class OutputPreset:
    """Single user-customizable voltage + current limit memory slot."""

    name: str
    voltage_v: float
    current_a: float

    def normalized(self, index: int = 0) -> "OutputPreset":
        clean_name = (self.name or "").strip()[:12] or f"挡位 {index + 1}"
        v = round(max(0.0, min(MAX_VOLTAGE_V, float(self.voltage_v))), 3)
        c = round(max(0.0, min(MAX_CURRENT_A, float(self.current_a))), 3)
        return OutputPreset(name=clean_name, voltage_v=v, current_a=c)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "voltage_v": self.voltage_v,
            "current_a": self.current_a,
        }

    @classmethod
    def from_dict(cls, data: object, fallback: "OutputPreset", index: int) -> "OutputPreset":
        if not isinstance(data, dict):
            return fallback
        try:
            name = str(data.get("name", fallback.name))
            voltage_v = float(data.get("voltage_v", fallback.voltage_v))
            current_a = float(data.get("current_a", fallback.current_a))
        except (TypeError, ValueError):
            return fallback
        return cls(name=name, voltage_v=voltage_v, current_a=current_a).normalized(index)


DEFAULT_OUTPUT_PRESETS: tuple[OutputPreset, ...] = (
    OutputPreset("5V 逻辑", 5.0, 1.0),
    OutputPreset("12V 常用", 12.0, 2.0),
    OutputPreset("12V 大流", 12.0, 5.0),
    OutputPreset("20V 快充", 20.0, 3.0),
    OutputPreset("24V 工业", 24.0, 5.0),
    OutputPreset("48V 高压", 48.0, 3.0),
)


class OutputPresetStore:
    """JSON-backed persistence for M1..M6 presets, last active slot, and live-apply preference."""

    def __init__(self, path: Path | None = None):
        self._path = Path(path) if path is not None else (CTX.dirs.ConfigDir / "power_presets.json")
        self._presets: list[OutputPreset] = list(DEFAULT_OUTPUT_PRESETS)
        self._active_index: int = 1  # Default M2 (12V / 2A)
        self._live_apply: bool = True
        self.load()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def presets(self) -> list[OutputPreset]:
        return list(self._presets)

    @property
    def active_index(self) -> int:
        return self._active_index

    @property
    def live_apply(self) -> bool:
        return self._live_apply

    def get(self, index: int) -> OutputPreset:
        idx = max(0, min(PRESET_COUNT - 1, int(index)))
        return self._presets[idx]

    def set_preset(self, index: int, preset: OutputPreset) -> OutputPreset:
        idx = max(0, min(PRESET_COUNT - 1, int(index)))
        cleaned = preset.normalized(idx)
        self._presets[idx] = cleaned
        self.save()
        return cleaned

    def set_active_index(self, index: int) -> None:
        idx = int(index)
        if idx < -1 or idx >= PRESET_COUNT:
            idx = -1
        if self._active_index != idx:
            self._active_index = idx
            self.save()

    def set_live_apply(self, enabled: bool) -> None:
        val = bool(enabled)
        if self._live_apply != val:
            self._live_apply = val
            self.save()

    def reset_slot(self, index: int) -> OutputPreset:
        idx = max(0, min(PRESET_COUNT - 1, int(index)))
        self._presets[idx] = DEFAULT_OUTPUT_PRESETS[idx]
        self.save()
        return self._presets[idx]

    def reset_defaults(self) -> list[OutputPreset]:
        self._presets = list(DEFAULT_OUTPUT_PRESETS)
        self._active_index = 1
        self.save()
        return self.presets

    def replace_all(self, presets: Iterable[OutputPreset]) -> None:
        items = list(presets)
        merged: list[OutputPreset] = []
        for idx in range(PRESET_COUNT):
            if idx < len(items):
                merged.append(items[idx].normalized(idx))
            else:
                merged.append(DEFAULT_OUTPUT_PRESETS[idx])
        self._presets = merged
        self.save()

    def load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(f"Failed to read preset store {self._path}: {exc}")
            return
        if not isinstance(raw, dict):
            return

        raw_list = raw.get("presets")
        if isinstance(raw_list, list):
            loaded: list[OutputPreset] = []
            for idx in range(PRESET_COUNT):
                fallback = DEFAULT_OUTPUT_PRESETS[idx]
                item = raw_list[idx] if idx < len(raw_list) else None
                loaded.append(OutputPreset.from_dict(item, fallback, idx))
            self._presets = loaded

        active = raw.get("active_index", self._active_index)
        if isinstance(active, int) and -1 <= active < PRESET_COUNT:
            self._active_index = active

        live = raw.get("live_apply", self._live_apply)
        if isinstance(live, bool):
            self._live_apply = live

    def save(self) -> None:
        payload = {
            "version": 1,
            "active_index": self._active_index,
            "live_apply": self._live_apply,
            "presets": [preset.to_dict() for preset in self._presets],
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.error(f"Failed to save preset store {self._path}: {exc}")
