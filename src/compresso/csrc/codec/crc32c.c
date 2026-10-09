#include "crc32c.h"
#include <assert.h>
#include <string.h>

#if defined(__x86_64__) || defined(_M_X64)
#define CRC32C_SSE42
#include <nmmintrin.h>
#ifdef _MSC_VER
#include <intrin.h>
#endif
#elif defined(__ARM_FEATURE_CRC32)
// Only where the compiler targets it already, as Apple Silicon does
#define CRC32C_ARM
#include <arm_acle.h>
#endif

// Each update below works on the CRC register as is; crc32c inverts it on
// the way in and out, as the standard requires
typedef uint32_t (*Update)(uint32_t, const unsigned char *, size_t);

// TABLE[k][b] is the CRC of byte b followed by k zero bytes
static uint32_t TABLE[8][256];
static Update update;

static uint32_t load_le32(const unsigned char *p) {
  return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 |
         (uint32_t)p[3] << 24;
}

// Slicing-by-8: one lookup per byte, but eight independent ones per step
static uint32_t update_table(uint32_t crc, const unsigned char *p, size_t n) {
  for (; n >= 8; p += 8, n -= 8) {
    uint32_t lo = crc ^ load_le32(p), hi = load_le32(p + 4);
    crc = TABLE[7][lo & 0xFF] ^ TABLE[6][(lo >> 8) & 0xFF] ^
          TABLE[5][(lo >> 16) & 0xFF] ^ TABLE[4][lo >> 24] ^
          TABLE[3][hi & 0xFF] ^ TABLE[2][(hi >> 8) & 0xFF] ^
          TABLE[1][(hi >> 16) & 0xFF] ^ TABLE[0][hi >> 24];
  }
  for (; n; n--)
    crc = (crc >> 8) ^ TABLE[0][(crc ^ *p++) & 0xFF];
  return crc;
}

#ifdef CRC32C_SSE42
// GCC and Clang build these intrinsics only into a function that targets them
#if defined(__GNUC__) || defined(__clang__)
__attribute__((target("sse4.2")))
#endif
static uint32_t update_sse42(uint32_t crc, const unsigned char *p, size_t n) {
  uint64_t crc64 = crc;
  for (; n >= 8; p += 8, n -= 8) {
    uint64_t word;
    memcpy(&word, p, 8);
    crc64 = _mm_crc32_u64(crc64, word);
  }
  crc = (uint32_t)crc64;
  for (; n; n--)
    crc = _mm_crc32_u8(crc, *p++);
  return crc;
}

static int have_sse42(void) {
#ifdef _MSC_VER
  int info[4];
  __cpuid(info, 1);
  return (info[2] >> 20) & 1;
#else
  return __builtin_cpu_supports("sse4.2");
#endif
}
#endif

#ifdef CRC32C_ARM
static uint32_t update_arm(uint32_t crc, const unsigned char *p, size_t n) {
  for (; n >= 8; p += 8, n -= 8) {
    uint64_t word;
    memcpy(&word, p, 8);
    crc = __crc32cd(crc, word);
  }
  for (; n; n--)
    crc = __crc32cb(crc, *p++);
  return crc;
}
#endif

void crc32c_init(void) {
  for (uint32_t b = 0; b < 256; b++) {
    uint32_t crc = b;
    for (int bit = 0; bit < 8; bit++)
      crc = (crc >> 1) ^ (0x82F63B78u & (0u - (crc & 1)));
    TABLE[0][b] = crc;
  }
  for (int b = 0; b < 256; b++)
    for (int k = 1; k < 8; k++)
      TABLE[k][b] = (TABLE[k - 1][b] >> 8) ^ TABLE[0][TABLE[k - 1][b] & 0xFF];

  update = update_table;
#ifdef CRC32C_SSE42
  if (have_sse42())
    update = update_sse42;
#elif defined(CRC32C_ARM)
  update = update_arm;
#endif
}

uint32_t crc32c(uint32_t crc, const void *buf, size_t len) {
  assert(update && "crc32c_init not called");
  return ~update(~crc, buf, len);
}

uint32_t crc32c_portable(uint32_t crc, const void *buf, size_t len) {
  assert(update && "crc32c_init not called");
  return ~update_table(~crc, buf, len);
}
