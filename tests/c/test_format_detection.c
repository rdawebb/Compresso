// Makes Unity define TEST_RANGE; the runner generator expands each one
#define UNITY_SUPPORT_TEST_CASES

#include "archives.h"
#include "unity.h"

Format detect_format_from_magic_bytes(const unsigned char *magic, size_t size);

static const unsigned char GZIP[] = {0x1f, 0x8b, 0x08};
static const unsigned char BZIP2[] = {'B', 'Z', 'h', '9'};
static const unsigned char XZ[] = {0xfd, 0x37, 0x7a, 0x58, 0x5a, 0x00};
static const unsigned char ZSTD[] = {0x28, 0xb5, 0x2f, 0xfd};
static const unsigned char LZ4[] = {0x04, 0x22, 0x4d, 0x18};
static const unsigned char ZIP[] = {'P', 'K', 0x03, 0x04};
static const unsigned char COMPRESSO[] = {'C', 'O', 'M', 'P'};
static const unsigned char SEVEN_Z[] = {'7', 'z', 0xbc, 0xaf, 0x27, 0x1c};
static const unsigned char UNKNOWN[] = {0xff, 0xff, 0xff, 0xff};
// "BZ" alone is not bzip2: the "h" and a block-size digit 1-9 must follow
static const unsigned char BZIP2_NO_H[] = {'B', 'Z', 'x', '9'};
static const unsigned char BZIP2_BAD_SIZE[] = {'B', 'Z', 'h', '0'};

static const struct {
  const char *label;
  const unsigned char *magic;
  size_t size;
  Format expected;
} CASES[] = {
    {"gzip", GZIP, sizeof(GZIP), FORMAT_GZIP},
    {"bzip2", BZIP2, sizeof(BZIP2), FORMAT_BZIP2},
    {"xz", XZ, sizeof(XZ), FORMAT_XZ},
    {"zstd", ZSTD, sizeof(ZSTD), FORMAT_ZSTD},
    {"lz4", LZ4, sizeof(LZ4), FORMAT_LZ4},
    {"zip", ZIP, sizeof(ZIP), FORMAT_ZIP},
    {"compresso", COMPRESSO, sizeof(COMPRESSO), FORMAT_COMPRESSO},
    {"7z", SEVEN_Z, sizeof(SEVEN_Z), FORMAT_7Z},
    {"unknown magic", UNKNOWN, sizeof(UNKNOWN), FORMAT_UNKNOWN},
    {"bzip2 without its h", BZIP2_NO_H, sizeof(BZIP2_NO_H), FORMAT_UNKNOWN},
    {"bzip2 block size 0", BZIP2_BAD_SIZE, sizeof(BZIP2_BAD_SIZE),
     FORMAT_UNKNOWN},
    {"bzip2 cut short", BZIP2, 3, FORMAT_UNKNOWN},
    // Only the first byte of gzip's magic
    {"truncated magic", GZIP, 1, FORMAT_UNKNOWN},
    {"zero size", GZIP, 0, FORMAT_UNKNOWN},
    {"null buffer", NULL, 10, FORMAT_UNKNOWN},
};

// The TEST_RANGE below must span exactly these rows
_Static_assert(sizeof(CASES) / sizeof(CASES[0]) == 15, "update TEST_RANGE");

void setUp(void) {}
void tearDown(void) {}

TEST_RANGE([0, 14, 1])
void test_detect_format_from_magic_bytes(int index) {
  TEST_ASSERT_EQUAL_MESSAGE(
      CASES[index].expected,
      detect_format_from_magic_bytes(CASES[index].magic, CASES[index].size),
      CASES[index].label);
}
