#ifndef MAGICS_H
#define MAGICS_H
#include <stddef.h>
#include <string.h>

// TAR stores its signature at an offset rather than at the start
#define TAR_MAGIC_OFFSET 257
#define TAR_MAGIC "ustar"

static inline int magic_is_gzip(const unsigned char *m, size_t n) {
  return n >= 2 && m[0] == 0x1f && m[1] == 0x8b;
}

static inline int magic_is_bzip2(const unsigned char *m, size_t n) {
  return n >= 2 && m[0] == 'B' && m[1] == 'Z';
}

static inline int magic_is_xz(const unsigned char *m, size_t n) {
  static const unsigned char XZ[] = {0xFD, 0x37, 0x7A, 0x58, 0x5A, 0x00};
  return n >= sizeof(XZ) && memcmp(m, XZ, sizeof(XZ)) == 0;
}

static inline int magic_is_zstd(const unsigned char *m, size_t n) {
  static const unsigned char ZSTD[] = {0x28, 0xB5, 0x2F, 0xFD};
  return n >= sizeof(ZSTD) && memcmp(m, ZSTD, sizeof(ZSTD)) == 0;
}

static inline int magic_is_lz4(const unsigned char *m, size_t n) {
  static const unsigned char LZ4[] = {0x04, 0x22, 0x4D, 0x18};
  return n >= sizeof(LZ4) && memcmp(m, LZ4, sizeof(LZ4)) == 0;
}

static inline int magic_is_zip(const unsigned char *m, size_t n) {
  return n >= 4 && m[0] == 'P' && m[1] == 'K' && m[2] == 0x03 && m[3] == 0x04;
}

static inline int magic_is_7z(const unsigned char *m, size_t n) {
  static const unsigned char SEVEN_Z[] = {'7', 'z', 0xBC, 0xAF, 0x27, 0x1C};
  return n >= sizeof(SEVEN_Z) && memcmp(m, SEVEN_Z, sizeof(SEVEN_Z)) == 0;
}

// The Compresso header's own magic is C_MAGIC in common.h, which this matches
// without including it, since the standalone files don't use it
static inline int magic_is_compresso(const unsigned char *m, size_t n) {
  return n >= 4 && m[0] == 'C' && m[1] == 'O' && m[2] == 'M' && m[3] == 'P';
}

#endif // MAGICS_H
