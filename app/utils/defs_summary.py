from __future__ import annotations

import os
from collections import Counter
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Tuple

from loguru import logger

try:
    # Prefer lxml if available for performance; fall back to stdlib
    from lxml import etree as ET  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    import xml.etree.ElementTree as ET  # type: ignore


@dataclass(frozen=True)
class DefsSummary:
    total_defs: int
    type_counts: Dict[str, int]
    files_scanned: int


class _DefsSummaryCache:
    """In-memory cache for defs summaries with simple mtime signature.

    Keyed by absolute mod path. Each entry stores (signature, summary) where
    signature is a tuple of (files_count, latest_mtime) across all XML files
    under any directory named "Defs" (case-insensitive).
    """

    def __init__(self) -> None:
        self._cache: Dict[str, Tuple[Tuple[int, float], DefsSummary]] = {}

    def _iter_defs_xml_files(self, mod_path: Path) -> Iterable[Path]:
        """Yield XML files from Defs directories relevant to the active version.

        Rules:
        - Always include XML files under the root-level `Defs` directory (if present).
        - If there are version subfolders like `1.3`, `1.4`, `1.5`, prefer the latest
          supported version per About.xml <supportedVersions>. If About is missing or
          contains no usable versions, pick the highest version folder present.
        - Only include XML files from `Defs` directly under that single chosen version folder.
        """

        def _find_case_insensitive_dir(parent: Path, expected_name: str) -> Path | None:
            try:
                for entry in os.scandir(parent):
                    if entry.is_dir() and entry.name.lower() == expected_name.lower():
                        return Path(entry.path)
            except FileNotFoundError:
                return None
            return None

        def _about_xml_path(mod_root: Path) -> Path | None:
            about_dir = _find_case_insensitive_dir(mod_root, "About")
            if not about_dir:
                return None
            try:
                for entry in os.scandir(about_dir):
                    if entry.is_file() and entry.name.lower() == "about.xml":
                        return Path(entry.path)
            except FileNotFoundError:
                return None
            return None

        def _parse_supported_versions(mod_root: Path) -> list[str]:
            path = _about_xml_path(mod_root)
            if not path:
                return []
            try:
                tree = ET.parse(str(path))
                root = tree.getroot()
                versions: list[str] = []
                # Look for supportedVersions/li elements (case-insensitive)
                for elem in root.iter():
                    tag = _strip_ns(elem.tag).lower()
                    if tag == "supportedversions":
                        for child in elem:
                            if _strip_ns(child.tag).lower() == "li":
                                text = (child.text or "").strip()
                                if text:
                                    versions.append(text)
                        break
                return versions
            except Exception:
                return []

        def _normalize_version_string(version: str) -> tuple[int, int] | None:
            match = re.search(r"(\d+)\.(\d+)", version)
            if not match:
                return None
            try:
                return int(match.group(1)), int(match.group(2))
            except Exception:
                return None

        def _find_available_version_dirs(mod_root: Path) -> dict[tuple[int, int], Path]:
            mapping: dict[tuple[int, int], Path] = {}
            try:
                for entry in os.scandir(mod_root):
                    if not entry.is_dir():
                        continue
                    norm = _normalize_version_string(entry.name)
                    if norm is not None:
                        mapping[norm] = Path(entry.path)
            except FileNotFoundError:
                return mapping
            return mapping

        def _choose_version_dir(mod_root: Path) -> Path | None:
            supported = _parse_supported_versions(mod_root)
            available = _find_available_version_dirs(mod_root)

            # First, try to choose the max among supported versions that exist
            supported_norm = [nv for v in supported if (nv := _normalize_version_string(v))]
            candidates = [nv for nv in supported_norm if nv in available]
            if candidates:
                chosen = max(candidates)
                return available[chosen]

            # Fallback: choose the highest available version dir
            if available:
                chosen = max(available.keys())
                return available[chosen]
            return None

        # Root-level Defs
        root_defs = _find_case_insensitive_dir(mod_path, "Defs")
        if root_defs:
            for r, _dirs, files in os.walk(root_defs):
                for file in files:
                    if file.lower().endswith(".xml"):
                        yield Path(r) / file

        # Versioned Defs (latest supported or latest available)
        version_dir = _choose_version_dir(mod_path)
        if version_dir:
            version_defs = _find_case_insensitive_dir(version_dir, "Defs")
            if version_defs:
                for r, _dirs, files in os.walk(version_defs):
                    for file in files:
                        if file.lower().endswith(".xml"):
                            yield Path(r) / file

    def _compute_signature(self, mod_path: Path) -> Tuple[int, float]:
        files = list(self._iter_defs_xml_files(mod_path))
        if not files:
            return 0, 0.0
        latest_mtime = 0.0
        for f in files:
            try:
                mtime = f.stat().st_mtime
                if mtime > latest_mtime:
                    latest_mtime = mtime
            except Exception:
                # Ignore files we cannot stat
                continue
        return len(files), latest_mtime

    def get(self, mod_path: str | Path) -> DefsSummary:
        path_obj = Path(mod_path)
        path_key = str(path_obj.resolve())

        signature = self._compute_signature(path_obj)
        cached = self._cache.get(path_key)
        if cached and cached[0] == signature:
            return cached[1]

        summary = self._scan_defs(path_obj)
        self._cache[path_key] = (signature, summary)
        return summary

    def _scan_defs(self, mod_path: Path) -> DefsSummary:
        type_counter: Counter[str] = Counter()
        files_scanned = 0

        for xml_path in self._iter_defs_xml_files(mod_path):
            try:
                # Fast-path: parse and count direct children of <Defs>
                tree = ET.parse(str(xml_path))
                root = tree.getroot()
                # Handle namespaces by stripping them
                tag_no_ns = _strip_ns(root.tag)
                if tag_no_ns.lower() != "defs":
                    # Not a standard Defs container; skip
                    continue
                for child in root:
                    child_tag = _strip_ns(child.tag)
                    if child_tag:
                        type_counter[child_tag] += 1
                files_scanned += 1
            except Exception as e:
                # Avoid noisy logs; record once per file at debug level
                logger.debug(f"Defs scan skipped file due to parse error: {xml_path}: {e}")
                continue

        total_defs = sum(type_counter.values())
        return DefsSummary(
            total_defs=total_defs, type_counts=dict(type_counter), files_scanned=files_scanned
        )


def _strip_ns(tag: str) -> str:
    """Strip XML namespace from a tag, returning the local name."""
    if not tag:
        return tag
    if tag.startswith("{"):
        # Namespace in form {ns}local
        end = tag.find("}")
        if end != -1:
            return tag[end + 1 :]
    return tag


_cache = _DefsSummaryCache()


def get_defs_summary(mod_path: str | Path) -> DefsSummary:
    """Public API to get defs summary for a mod path with caching.

    - Counts all XML def nodes directly under <Defs> from root-level `Defs` and
      at most one versioned folder `X.Y/Defs` (preferring the latest supported
      version from About.xml, otherwise the highest available version folder).
    - Returns totals and per-type counts.
    - Uses a simple signature (num XML files, latest mtime) to invalidate cache.
    """
    return _cache.get(mod_path)


