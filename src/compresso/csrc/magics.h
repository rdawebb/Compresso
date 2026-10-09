#ifndef MAGICS_H
#define MAGICS_H
#include <stddef.h>
#include <string.h>

static inline int magic_is_gzip(const unsigned char *m, size_t n) {
  return n >= 2 && m[0] == 0x1f && m[1] == 0x8b;
}

// "BZh" then the block size as a digit, 1-9 (100-900 KB)
static inline int magic_is_bzip2(const unsigned char *m, size_t n) {
  return n >= 4 && m[0] == 'B' && m[1] == 'Z' && m[2] == 'h' && m[3] >= '1' &&
         m[3] <= '9';
}

static inline int magic_is_xz(const unsigned char *m, size_t n) {
  static const unsigned char XZ[] = {0xFD, 0x37, 0x7A, 0x58, 0x5A, 0x00};
  return n >= sizeof(XZ) && memcmp(m, XZ, sizeof(XZ)) == 0;
}

// zstd and lz4 share skippable frames: a magic of 0x184D2A50-5F, then the
// size of the data that follows, both LE32; returns where the first other
// frame starts, or `n` when the skippable ones run past the buffer
static inline size_t skip_skippable_frames(const unsigned char *m, size_t n) {
  size_t off = 0;
  while (n - off >= 8 && (m[off] & 0xF0) == 0x50 && m[off + 1] == 0x2A &&
         m[off + 2] == 0x4D && m[off + 3] == 0x18) {
    size_t size = (size_t)m[off + 4] | (size_t)m[off + 5] << 8 |
                  (size_t)m[off + 6] << 16 | (size_t)m[off + 7] << 24;
    if (size > n - off - 8)
      return n;
    off += 8 + size;
  }
  return off;
}

// A file that opens with skippable frames is the format of the frame after
// them; zstd when that lies past the buffer, as zstd defines them
static inline int magic_is_zstd(const unsigned char *m, size_t n) {
  static const unsigned char ZSTD[] = {0x28, 0xB5, 0x2F, 0xFD};
  size_t off = skip_skippable_frames(m, n);
  if (off == n)
    return off > 0;
  return n - off >= sizeof(ZSTD) && memcmp(m + off, ZSTD, sizeof(ZSTD)) == 0;
}

static inline int magic_is_lz4(const unsigned char *m, size_t n) {
  static const unsigned char LZ4[] = {0x04, 0x22, 0x4D, 0x18};
  size_t off = skip_skippable_frames(m, n);
  return n - off >= sizeof(LZ4) && memcmp(m + off, LZ4, sizeof(LZ4)) == 0;
}

static inline int magic_is_zip(const unsigned char *m, size_t n) {
  return n >= 4 && m[0] == 'P' && m[1] == 'K' && m[2] == 0x03 && m[3] == 0x04;
}

static inline int magic_is_7z(const unsigned char *m, size_t n) {
  static const unsigned char SEVEN_Z[] = {'7', 'z', 0xBC, 0xAF, 0x27, 0x1C};
  return n >= sizeof(SEVEN_Z) && memcmp(m, SEVEN_Z, sizeof(SEVEN_Z)) == 0;
}

// The octal checksum of a 512-byte header, which sums the field itself as
// spaces; old tars summed signed chars, so either sum is accepted
static inline int tar_checksum_ok(const unsigned char *m) {
  const unsigned char *field = m + 148, *end = field + 8;
  while (field < end && *field == ' ')
    field++;
  if (field == end || *field < '0' || *field > '7')
    return 0;

  long stored = 0;
  while (field < end && *field >= '0' && *field <= '7')
    stored = stored * 8 + (*field++ - '0');

  long unsigned_sum = 0, signed_sum = 0;
  for (int i = 0; i < 512; i++) {
    unsigned char c = (i >= 148 && i < 156) ? ' ' : m[i];
    unsigned_sum += c;
    signed_sum += (signed char)c;
  }
  return stored == unsigned_sum || stored == signed_sum;
}

// Tar stores its signature at an offset rather than at the start; a pre-POSIX
// (v7) header has none, so it is recognised by a v7 or ustar entry type and a
// valid checksum
static inline int magic_is_tar(const unsigned char *m, size_t n) {
  if (n >= 257 + 5 && memcmp(m + 257, "ustar", 5) == 0)
    return 1;
  return n >= 512 && (m[156] == '\0' || (m[156] >= '0' && m[156] <= '7')) &&
         tar_checksum_ok(m);
}

// The Compresso header's own magic is C_MAGIC in common.h, which this matches
// without including it, since the standalone files don't use it
static inline int magic_is_compresso(const unsigned char *m, size_t n) {
  return n >= 4 && m[0] == 'C' && m[1] == 'O' && m[2] == 'M' && m[3] == 'P';
}

#endif // MAGICS_H
