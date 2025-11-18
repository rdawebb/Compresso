from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable

_DEFAULT_COMP_MB_S = {
    "zlib": 200.0,
    "bzip2": 50.0,
    "lzma": 30.0,
    "zstd": 400.0,
    "lz4": 800.0,
    "snappy": 600.0,
}

_DEFAULT_DECOMP_MB_S = {
    "zlib": 250.0,
    "bzip2": 60.0,
    "lzma": 40.0,
    "zstd": 500.0,
    "lz4": 900.0,
    "snappy": 700.0,
}

_CONFIG_DIR = Path.home() / ".compresso"
_SPEEDS_FILE = _CONFIG_DIR / "speeds.json"


@dataclass
class AlgoSpeeds:
    """Dataclass representing compression and decompression speeds for an algorithm.

    Attributes:
        algo (str): The name of the compression algorithm.
        comp_mb_s (float): Compression speed in megabytes per second.
        decomp_mb_s (float): Decompression speed in megabytes per second.
        samples (int): Number of samples used to calculate the speeds.
    """

    algo: str
    comp_mb_s: float
    decomp_mb_s: float
    samples: int


def _load_raw() -> Dict[str, AlgoSpeeds]:
    """Load raw speed data from the speeds file.

    Returns:
        Dict[str, AlgoSpeeds]: A dictionary mapping algorithm names to their speed data.
    """
    if not _SPEEDS_FILE.is_file():
        return {}

    try:
        data = json.loads(_SPEEDS_FILE.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    result: Dict[str, AlgoSpeeds] = {}
    for algo, entry in data.items():
        try:
            result[algo] = AlgoSpeeds(
                algo=algo,
                comp_mb_s=float(entry.get("comp_mb_s", 0.0)),
                decomp_mb_s=float(entry.get("decomp_mb_s", 0.0)),
                samples=int(entry.get("samples", 0)),
            )
        except (TypeError, ValueError):
            continue

    return result


def _save_raw(entries: Dict[str, AlgoSpeeds]) -> None:
    """Save raw speed data to the speeds file.

    Args:
        entries (Dict[str, AlgoSpeeds]): A dictionary mapping algorithm names to their speed data.
    """
    if not _CONFIG_DIR.exists():
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    data = {
        algo: {
            "comp_mb_s": entry.comp_mb_s,
            "decomp_mb_s": entry.decomp_mb_s,
            "samples": entry.samples,
        }
        for algo, entry in entries.items()
    }

    _SPEEDS_FILE.write_text(json.dumps(data, indent=4), "utf-8")


def update_from_benchmarks(results: Iterable[object]) -> None:
    """Update speed estimates based on benchmark results.

    Args:
        results (Iterable[object]): An iterable of benchmark result objects. Each object should have 'algo', 'comp_mb_s', and 'decomp_mb_s' attributes.
    """
    existing = _load_raw()

    accumulated: Dict[str, dict] = {}
    for result in results:
        algo = getattr(result, "algo", None)
        if not algo:
            continue
        comp = float(getattr(result, "comp_mb_s", None) or 0.0)
        decomp = float(getattr(result, "decomp_mb_s", None) or 0.0)

        if comp <= 0.0 or decomp <= 0.0:
            continue

        agg = accumulated.setdefault(
            algo, {"comp_sum": 0.0, "decomp_sum": 0.0, "count": 0}
        )
        agg["comp_sum"] += comp
        agg["decomp_sum"] += decomp
        agg["count"] += 1

    if not accumulated:
        return

    for algo, stats in accumulated.items():
        new_count = stats["count"]
        new_comp_avg = stats["comp_sum"] / new_count
        new_decomp_avg = stats["decomp_sum"] / new_count

        old = existing.get(algo)
        if old is None or old.samples <= 0:
            existing[algo] = AlgoSpeeds(
                algo=algo,
                comp_mb_s=new_comp_avg,
                decomp_mb_s=new_decomp_avg,
                samples=new_count,
            )
        else:
            total_samples = old.samples + new_count
            combined_comp = (
                old.comp_mb_s * old.samples + new_comp_avg * new_count
            ) / total_samples
            combined_decomp = (
                old.decomp_mb_s * old.samples + new_decomp_avg * new_count
            ) / total_samples
            existing[algo] = AlgoSpeeds(
                algo=algo,
                comp_mb_s=combined_comp,
                decomp_mb_s=combined_decomp,
                samples=total_samples,
            )

    _save_raw(existing)


def get_estimated_speeds(algo: str, *, operation: str = "decompress") -> float:
    """Get estimated speed for a given algorithm and operation.

    Args:
        algo (str): The name of the compression algorithm.
        operation (str): The operation type, either "compress" or "decompress". Defaults to "decompress".

    Returns:
        float: The estimated speed in MB/s for the specified algorithm and operation.
    """
    algo = algo.lower()
    db = _load_raw()

    if algo in db:
        entry = db[algo]
        if operation == "compress":
            if entry.comp_mb_s > 0.0:
                return entry.comp_mb_s
        else:
            if entry.decomp_mb_s > 0.0:
                return entry.decomp_mb_s

    if operation == "compress":
        return _DEFAULT_COMP_MB_S.get(algo, 200.0)
    else:
        return _DEFAULT_DECOMP_MB_S.get(algo, 200.0)
