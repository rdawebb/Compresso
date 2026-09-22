"""Records and checks the exact bytes (SHA-256) each codec writes.

Usage:
    python scripts/codec_fixtures.py record   # write the baseline manifest
    python scripts/codec_fixtures.py check    # compare against it

`record` also leaves every compressed artifact under the work directory, so a
later build can be pointed at them with `decode` to confirm it still reads what
an earlier one wrote.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The extension is imported from the source tree, so a `build_ext --inplace`
# build is what gets measured
sys.path.insert(0, str(REPO_ROOT / "src"))

from compresso import _core

MANIFEST_PATH = REPO_ROOT / "tests" / "fixtures" / "codec_manifest.json"
WORK_DIR = REPO_ROOT / "build" / "codec-fixtures"

# Levels span the ends of every backend's range plus the default, since some
# libraries derive header bytes from the level
LEVELS = (1, 6, 9)

# `.comp` framing, via the CBackend stream ops
COMP_ALGOS = ("zlib", "bzip2", "lzma", "zstd", "lz4", "snappy")

# Standalone containers, via the StandaloneFormat ops
STANDALONE_FORMATS = ("gzip", "bzip2", "xz", "zstd", "lz4")

# Deterministic, so reproducible without committing 2 MB of noise
RANDOM_SEED = 20260922
RANDOM_SIZE = 2 * 1024 * 1024


def build_corpus(corpus_dir: Path) -> dict[str, Path]:
    """Materialise the input corpus, reusing the repo's own text fixtures.

    Args:
        corpus_dir: Directory to write the generated inputs into.

    Returns:
        A mapping of case name to input path.
    """
    corpus_dir.mkdir(parents=True, exist_ok=True)

    empty = corpus_dir / "empty.bin"
    empty.write_bytes(b"")

    one_byte = corpus_dir / "one_byte.bin"
    one_byte.write_bytes(b"\x00")

    # Incompressible, so the codecs take their expansion paths rather than
    # their happy paths, and large enough to span many 64 KB chunks
    noise = corpus_dir / "random.bin"
    noise.write_bytes(random.Random(RANDOM_SEED).randbytes(RANDOM_SIZE))

    return {
        "empty": empty,
        "one_byte": one_byte,
        # 4 KB: smaller than one chunk
        "xargs": REPO_ROOT / "tests" / "fixtures" / "xargs.1",
        # 152 KB of real prose: forces the multi-chunk drain
        "alice": REPO_ROOT / "tests" / "fixtures" / "alice29.txt",
        "random": noise,
    }


def sha256(path: Path) -> str:
    """Hash a file's contents.

    Args:
        path: File to hash.

    Returns:
        The hex digest.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)

    return digest.hexdigest()


def record_outcome(write: Callable[[], object], dst: Path) -> str:
    """Run one compression and describe what it produced.

    A refusal is recorded rather than skipped: `.comp` rejects empty input via
    `validate_size` while the standalone containers accept it.

    Args:
        write: Thunk performing the compression.
        dst: Path the compression writes to.

    Returns:
        The output's SHA-256, or an `error:` line naming the refusal.
    """
    try:
        write()

    except Exception as exc:  # noqa: BLE001 - any refusal is a result worth recording
        return f"error:{type(exc).__name__}: {exc}"

    return sha256(dst)


def compress_all(corpus: dict[str, Path], out_dir: Path) -> dict[str, str]:
    """Compress every corpus entry through both paths at every level.

    Args:
        corpus: Mapping of case name to input path.
        out_dir: Directory to write the compressed artifacts into.

    Returns:
        A mapping of artifact key to SHA-256 digest, or to an `error:` line
        where that combination is refused.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    digests: dict[str, str] = {}

    for case, src in sorted(corpus.items()):
        for level in LEVELS:
            for algo in COMP_ALGOS:
                dst = out_dir / f"{case}.{algo}.{level}.comp"
                digests[f"comp/{algo}/{level}/{case}"] = record_outcome(
                    # overwrite=2, so a re-run replaces rather than renaming
                    lambda s=src, d=dst, a=algo, lv=level: _core.compress_file(
                        str(s), str(d), a, "balanced", lv, overwrite=2
                    ),
                    dst,
                )

            for fmt in STANDALONE_FORMATS:
                dst = out_dir / f"{case}.{fmt}.{level}.bin"
                digests[f"standalone/{fmt}/{level}/{case}"] = record_outcome(
                    lambda s=src, d=dst, f=fmt, lv=level: _core.compress_standalone(
                        str(s), str(d), f, lv, overwrite=2
                    ),
                    dst,
                )

    return digests


def decode_all(
    corpus: dict[str, Path], out_dir: Path, baseline: dict[str, str]
) -> list[str]:
    """Decompress every artifact in `out_dir` and compare against its input.

    Args:
        corpus: Mapping of case name to input path.
        out_dir: Directory holding artifacts written by a previous `record`.
        baseline: The recorded manifest, used to skip refused combinations.

    Returns:
        A list of human-readable failures; empty when every artifact round-trips.
    """
    scratch = out_dir / "decoded"
    scratch.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []

    def check(
        key: str, artifact: Path, dst: Path, expected: str, decode: Callable[[], object]
    ) -> None:
        """Decode one artifact and record a failure if it does not match.

        Args:
            key: Manifest key for the artifact.
            artifact: The compressed file to decode.
            dst: Where to write the decoded output.
            expected: SHA-256 of the original input.
            decode: Thunk performing the decompression.
        """
        if baseline.get(key, "").startswith("error:"):
            return  # Never written, because the recording build refused it

        if not artifact.is_file():
            failures.append(f"{key} (artifact missing)")
            return

        try:
            decode()

        except Exception as exc:  # noqa: BLE001 - a refusal here is the failure
            failures.append(f"{key} ({type(exc).__name__}: {exc})")
            return

        if sha256(dst) != expected:
            failures.append(key)

    for case, src in sorted(corpus.items()):
        expected = sha256(src)
        for level in LEVELS:
            for algo in COMP_ALGOS:
                artifact = out_dir / f"{case}.{algo}.{level}.comp"
                dst = scratch / f"{case}.{algo}.{level}.out"
                check(
                    f"comp/{algo}/{level}/{case}",
                    artifact,
                    dst,
                    expected,
                    lambda a=artifact, d=dst, al=algo: _core.decompress_file(
                        str(a), str(d), al
                    ),
                )

            for fmt in STANDALONE_FORMATS:
                artifact = out_dir / f"{case}.{fmt}.{level}.bin"
                dst = scratch / f"{case}.{fmt}.{level}.out"
                check(
                    f"standalone/{fmt}/{level}/{case}",
                    artifact,
                    dst,
                    expected,
                    lambda a=artifact, d=dst, f=fmt: _core.decompress_standalone(
                        str(a), str(d), f
                    ),
                )

    return failures


def report(baseline: dict[str, str], current: dict[str, str]) -> int:
    """Diff two digest maps and print what moved.

    Args:
        baseline: Digests from the recorded manifest.
        current: Digests from this build.

    Returns:
        A process exit status: 0 when identical, 1 otherwise.
    """
    changed = sorted(
        k for k in baseline.keys() & current.keys() if baseline[k] != current[k]
    )
    added = sorted(current.keys() - baseline.keys())
    removed = sorted(baseline.keys() - current.keys())

    for key in changed:
        print(f"CHANGED {key}\n  was {baseline[key]}\n  now {current[key]}")
    for key in added:
        print(f"ADDED   {key}")
    for key in removed:
        print(f"MISSING {key}")

    if changed or added or removed:
        print(
            f"\n{len(changed)} changed, {len(added)} added, {len(removed)} missing "
            f"of {len(baseline)} recorded outputs"
        )
        return 1

    print(f"{len(current)} outputs byte-identical to the manifest")
    return 0


def main() -> int:
    """Run the requested mode.

    Returns:
        A process exit status.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("record", "check", "decode"))
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=WORK_DIR,
        help="where corpus and artifacts live (default: build/codec-fixtures)",
    )
    args = parser.parse_args()

    corpus_dir = args.work_dir / "corpus"
    out_dir = args.work_dir / "artifacts"

    if args.mode == "decode":
        if not out_dir.is_dir():
            print(
                f"no artifacts at {out_dir}; run `record` with an earlier build first"
            )
            return 1

        if not MANIFEST_PATH.is_file():
            print(f"no manifest at {MANIFEST_PATH}; run `record` first")
            return 1

        corpus = build_corpus(corpus_dir)
        baseline = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["outputs"]
        failures = decode_all(corpus, out_dir, baseline)
        for key in failures:
            print(f"MISMATCH {key}")

        if failures:
            print(f"\n{len(failures)} artifacts did not decode to their input")
            return 1

        print("every recorded artifact decoded back to its input")
        return 0

    corpus = build_corpus(corpus_dir)

    if args.mode == "record":
        digests = compress_all(corpus, out_dir)
        MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST_PATH.write_text(
            json.dumps(
                {"seed": RANDOM_SEED, "outputs": digests}, indent=2, sort_keys=True
            )
            + "\n",
            encoding="utf-8",
        )
        print(
            f"recorded {len(digests)} outputs to {MANIFEST_PATH.relative_to(REPO_ROOT)}"
        )
        return 0

    if not MANIFEST_PATH.is_file():
        print(f"no manifest at {MANIFEST_PATH}; run `record` first")
        return 1

    baseline = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["outputs"]

    # A check writes into its own directory, so it can never overwrite the
    # artifacts `decode` needs from the recording build
    check_dir = args.work_dir / "check"
    shutil.rmtree(check_dir, ignore_errors=True)

    return report(baseline, compress_all(corpus, check_dir))


if __name__ == "__main__":
    raise SystemExit(main())
