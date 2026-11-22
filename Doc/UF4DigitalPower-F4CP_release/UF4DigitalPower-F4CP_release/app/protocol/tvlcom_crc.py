# -*- coding: utf-8 -*-
"""UF4COM CRC helpers (256-entry lookup table for minimal GIL overhead)."""

from __future__ import annotations


def _build_crc16_table() -> tuple[int, ...]:
    table = []
    for byte in range(256):
        crc = byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
        table.append(crc)
    return tuple(table)


_CRC16_TABLE = _build_crc16_table()


def crc16_ccitt(data: bytes) -> int:
    """CRC16-CCITT used by UF4COM V3/V5."""
    crc = 0xFFFF
    table = _CRC16_TABLE
    for byte in data:
        crc = ((crc << 8) ^ table[((crc >> 8) ^ byte) & 0xFF]) & 0xFFFF
    return crc
