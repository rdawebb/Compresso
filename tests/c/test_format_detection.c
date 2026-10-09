// Makes Unity define TEST_RANGE; the runner generator expands each one
#define UNITY_SUPPORT_TEST_CASES

#include "formats.h"
#include "unity.h"

static Format id_of(const FormatDesc *desc) {
  return desc ? desc->id : FORMAT_UNKNOWN;
}

static const unsigned char GZIP[] = {0x1f, 0x8b, 0x08};
static const unsigned char BZIP2[] = {'B', 'Z', 'h', '9'};
static const unsigned char XZ[] = {0xfd, 0x37, 0x7a, 0x58, 0x5a, 0x00};
static const unsigned char ZSTD[] = {0x28, 0xb5, 0x2f, 0xfd};
static const unsigned char LZ4[] = {0x04, 0x22, 0x4d, 0x18};
static const unsigned char ZIP[] = {'P', 'K', 0x03, 0x04};
static const unsigned char COMPRESSO[] = {'C', 'O', 'M', 'P'};
static const unsigned char SEVEN_Z[] = {'7', 'z', 0xbc, 0xaf, 0x27, 0x1c};
static const unsigned char UNKNOWN[] = {0xff, 0xff, 0xff, 0xff};
// Tar's magic sits at an offset, behind the first entry's name
static const unsigned char TAR[262] = {[257] = 'u', 's', 't', 'a', 'r'};
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
    {"tar", TAR, sizeof(TAR), FORMAT_TAR},
    {"tar cut short", TAR, sizeof(TAR) - 1, FORMAT_UNKNOWN},
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
_Static_assert(sizeof(CASES) / sizeof(CASES[0]) == 17, "update TEST_RANGE");

void setUp(void) {}
void tearDown(void) {}

TEST_RANGE([ 0, 16, 1 ])
void test_format_by_magic(int index) {
  TEST_ASSERT_EQUAL_MESSAGE(
      CASES[index].expected,
      id_of(format_by_magic(CASES[index].magic, CASES[index].size)),
      CASES[index].label);
}

static const struct {
  const char *path;
  Format expected;
  int in_tar;
} EXTENSIONS[] = {
    {"a.gz", FORMAT_GZIP, 0},
    {"A.GZ", FORMAT_GZIP, 0},
    {"dir/a.tar", FORMAT_TAR, 0},
    {"a.tgz", FORMAT_GZIP, 1},
    {"a.tar.zstd", FORMAT_ZSTD, 1},
    // Tar inside something unknown is still tar inside
    {"a.tar.foo", FORMAT_UNKNOWN, 1},
    {"a.star.gz", FORMAT_GZIP, 0},
    {"noext", FORMAT_UNKNOWN, 0},
    // A dot in a directory name is not the file's extension
    {"dir.gz/file", FORMAT_UNKNOWN, 0},
    // A leading dot names a hidden file, as os.path.splitext has it
    {".gz", FORMAT_UNKNOWN, 0},
    {"dir/.zst", FORMAT_UNKNOWN, 0},
    // UTF-8 bytes are negative as a char, which tolower must not be given
    {"caf\xc3\xa9.xz", FORMAT_XZ, 0},
    {"file.\xc3\xa9", FORMAT_UNKNOWN, 0},
};

// The TEST_RANGE below must span exactly these rows
_Static_assert(sizeof(EXTENSIONS) / sizeof(EXTENSIONS[0]) == 13,
               "update TEST_RANGE");

TEST_RANGE([ 0, 12, 1 ])
void test_format_by_ext(int index) {
  int in_tar = -1;
  TEST_ASSERT_EQUAL_MESSAGE(
      EXTENSIONS[index].expected,
      id_of(format_by_ext(EXTENSIONS[index].path, &in_tar)),
      EXTENSIONS[index].path);
  TEST_ASSERT_EQUAL_MESSAGE(EXTENSIONS[index].in_tar, in_tar,
                            EXTENSIONS[index].path);
}

void test_find_archive_by_id(void) {
  TEST_ASSERT_EQUAL_PTR(&TAR_ARCHIVE, find_archive_by_id(ARCHIVE_TAR));
  TEST_ASSERT_EQUAL_PTR(&ZIP_ARCHIVE, find_archive_by_id(ARCHIVE_ZIP));
  TEST_ASSERT_NULL(find_archive_by_id(ARCHIVE_7Z));
  TEST_ASSERT_NULL(find_archive_by_id(ARCHIVE_NONE));
}
