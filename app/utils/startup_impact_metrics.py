from __future__ import annotations

from pathlib import Path
from typing import Dict
import xml.etree.ElementTree as ET

_cache: Dict[str, int] | None = None
_max_total_ms: int | None = None


def _find_metrics_file() -> Path | None:
    # Minimal: support Windows path where StartupImpact writes by default
    candidate = (
        Path.home()
        / "AppData/LocalLow/Ludeon Studios/RimWorld by Ludeon Studios/StartupImpact/metrics.xml"
    )
    if candidate.exists():
        return candidate
    return None


def get_total_ms_by_mod_name() -> Dict[str, int]:
    global _cache
    if _cache is not None:
        return _cache

    metrics: Dict[str, int] = {}
    metrics_file = _find_metrics_file()
    if not metrics_file:
        _cache = metrics
        return metrics

    try:
        tree = ET.parse(metrics_file)
        root = tree.getroot()
        mods = root.find("Mods")
        if mods is None:
            _cache = metrics
            return metrics
        for mod_el in mods.findall("Mod"):
            name = mod_el.get("name")
            total_ms_str = mod_el.get("totalMs")
            if not name or not total_ms_str:
                continue
            try:
                total_ms = int(total_ms_str)
            except ValueError:
                continue
            if total_ms <= 0:
                continue
            # normalize by lowercase name to match RimSort metadata lookups
            metrics[name.lower()] = total_ms
    except Exception:
        # Silent fail: return empty mapping on any parsing errors
        metrics = {}

    _cache = metrics
    global _max_total_ms
    _max_total_ms = max(metrics.values()) if metrics else 0
    return metrics


def get_max_total_ms() -> int:
    global _max_total_ms
    if _max_total_ms is not None:
        return _max_total_ms
    metrics = get_total_ms_by_mod_name()
    _max_total_ms = max(metrics.values()) if metrics else 0
    return _max_total_ms


