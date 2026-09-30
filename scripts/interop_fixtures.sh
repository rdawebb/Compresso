#!/usr/bin/env bash
# Regenerates tests/fixtures/interop/: files made by the reference tools rather
# than by Compresso, so the tests catch formats only other producers emit
#
# Needs gzip, bgzip (htslib), bzip2, pbzip2, xz, lz4, zstd, GNU tar, Info-ZIP
# zip and python3; GNU tar is looked up as `gtar` then `tar`, or set GNU_TAR to
# override
#
# Usage: scripts/interop_fixtures.sh

set -euo pipefail

# MSYS and Cygwin copy on `ln -s` and ignore directory modes, which would give
# wrong fixtures without any error; WSL behaves like Linux
case "$(uname -s)" in
  MINGW* | MSYS* | CYGWIN*)
    echo "not supported on $(uname -s); run under WSL, Linux or macOS" >&2
    exit 1
    ;;
esac

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/tests/fixtures/interop"
ALICE="$ROOT/tests/fixtures/alice29.txt"
if [ -z "${GNU_TAR:-}" ]; then
  if command -v gtar >/dev/null; then GNU_TAR=gtar; else GNU_TAR=tar; fi
fi

for tool in gzip bgzip bzip2 pbzip2 xz lz4 zstd "$GNU_TAR" zip python3; do
  command -v "$tool" >/dev/null || { echo "missing: $tool" >&2; exit 1; }
done
"$GNU_TAR" --version | grep -q "GNU tar" || {
  echo "$GNU_TAR is not GNU tar; set GNU_TAR" >&2
  exit 1
}

umask 022
WORK="$(mktemp -d)"
trap 'chmod -R u+w "$WORK"; rm -rf "$WORK"' EXIT

mkdir -p "$OUT"
cd "$OUT"

# Two distinct members, so a decoder that stops after the first is caught
head -c 2000 "$ALICE" >part1.txt
head -c 4000 "$ALICE" | tail -c 2000 >part2.txt

# A skippable frame (shared by zstd and lz4): magic 0x184D2A50, u32 LE length,
# then that many bytes the decoder must ignore
skippable() { printf '\x50\x2a\x4d\x18\x08\x00\x00\x00SKIPPED!'; }

# Single-stream baselines
gzip -9 -n -c part1.txt >gzip_single.gz
bzip2 -9 -c part1.txt >bzip2_single.bz2
xz -c part1.txt >xz_single.xz
lz4 -q -c part1.txt >lz4_single.lz4
zstd -q -c part1.txt >zstd_single.zst

# gzip: `cat a.gz b.gz`; BGZF is 64 KB members with an FEXTRA field, ending in
# an empty EOF member
{ gzip -n -c part1.txt; gzip -n -c part2.txt; } >gzip_multi_member.gz
bgzip -c "$ALICE" >gzip_bgzf.gz

# bzip2: pbzip2 compresses each 100 KB block as its own stream
pbzip2 -c -b1 -p2 "$ALICE" >bzip2_pbzip2.bz2

# xz: two whole streams back to back
{ xz -c part1.txt; xz -c part2.txt; } >xz_concat.xz

# xz: check type 0x02, which the format reserves and no xz writes; it has the
# same 4-byte field as CRC32 (0x01), so only the header and footer flags and
# their CRCs change, and xz itself warns and decodes it unchecked
xz --check=crc32 -c part1.txt >xz_unsupported_check.xz
python3 - xz_unsupported_check.xz <<'PY'
import struct, sys, zlib
path = sys.argv[1]
data = bytearray(open(path, "rb").read())
data[7] = 0x02
data[8:12] = struct.pack("<I", zlib.crc32(data[6:8]))
data[-3] = 0x02
data[-12:-8] = struct.pack("<I", zlib.crc32(data[-8:-2]))
open(path, "wb").write(data)
PY

# lz4 and zstd: back-to-back frames, and a skippable frame between two frames
{ lz4 -q -c part1.txt; lz4 -q -c part2.txt; } >lz4_concat.lz4
{ lz4 -q -c part1.txt; skippable; lz4 -q -c part2.txt; } >lz4_skippable.lz4
{ zstd -q -c part1.txt; zstd -q -c part2.txt; } >zstd_concat.zst
{ zstd -q -c part1.txt; skippable; zstd -q -c part2.txt; } >zstd_skippable.zst

TAR_FLAGS=(--format=gnu --sort=name --owner=0 --group=0 --numeric-owner
  --mtime=2024-01-01T00:00:00Z)

# GNU tar stores the second name of a hardlinked file as a link entry with no data
mkdir "$WORK/hardlink"
cp part1.txt "$WORK/hardlink/a.txt"
ln "$WORK/hardlink/a.txt" "$WORK/hardlink/b.txt"
"$GNU_TAR" "${TAR_FLAGS[@]}" -cf tar_hardlink.tar -C "$WORK" hardlink

# A directory entry with mode 0555 that precedes the file inside it
mkdir "$WORK/readonly"
cp part1.txt "$WORK/readonly/file.txt"
chmod 0555 "$WORK/readonly"
"$GNU_TAR" "${TAR_FLAGS[@]}" -cf tar_readonly_dir.tar -C "$WORK" readonly

# Info-ZIP with -y stores a symlink as its target text, marked by S_IFLNK in
# the external attributes
mkdir "$WORK/symlink"
printf 'target\n' >"$WORK/symlink/target.txt"
ln -s target.txt "$WORK/symlink/link.txt"
[ -L "$WORK/symlink/link.txt" ] || {
  echo "ln -s did not make a symlink" >&2
  exit 1
}
touch -h -t 202401010000 "$WORK/symlink" "$WORK/symlink/target.txt" \
  "$WORK/symlink/link.txt"
rm -f zip_symlink.zip
(cd "$WORK" && zip -q -X -y "$OUT/zip_symlink.zip" symlink/target.txt symlink/link.txt)

echo "Wrote $(ls "$OUT" | wc -l | tr -d ' ') files to $OUT"
