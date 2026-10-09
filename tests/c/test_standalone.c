// Makes Unity define TEST_RANGE; the runner generator expands each one
#define UNITY_SUPPORT_TEST_CASES

#include "common.h"
#include "files.h"
#include "standalone.h"
#include "test_stubs.h"
#include "unity.h"
#include <stdio.h>

#define TEST_INPUT FIXTURE_DIR "/alice29.txt"

static const Format CONTAINERS[] = {
    FORMAT_GZIP, FORMAT_BZIP2, FORMAT_XZ, FORMAT_ZSTD, FORMAT_LZ4,
};

// Every TEST_RANGE below must span exactly these rows
_Static_assert(sizeof(CONTAINERS) / sizeof(CONTAINERS[0]) == 5,
               "update TEST_RANGE");

void setUp(void) {
  if (!Py_IsInitialized()) {
    Py_Initialize();
  }
  ensure_comp_exceptions();
}

void tearDown(void) {
  // Make sure a deliberately-triggered error does not leak into the next test
  PyErr_Clear();
}

TEST_RANGE([ 0, 4, 1 ])
void test_container_has_an_engine(int index) {
  const FormatDesc *fmt = find_standalone_format(CONTAINERS[index]);

  TEST_ASSERT_NOT_NULL(fmt);
  TEST_ASSERT_EQUAL(CONTAINERS[index], fmt->id);
  TEST_ASSERT_NOT_NULL_MESSAGE(fmt->engine, fmt->name);
}

void test_only_containers_are_standalone(void) {
  TEST_ASSERT_NULL(find_standalone_format(FORMAT_ZIP));
  TEST_ASSERT_NULL(find_standalone_format(FORMAT_TAR));
  TEST_ASSERT_NULL(find_standalone_format(FORMAT_COMPRESSO));
  TEST_ASSERT_NULL(find_standalone_format(FORMAT_UNKNOWN));
}

TEST_RANGE([ 0, 4, 1 ])
void test_round_trip(int index) {
  const FormatDesc *fmt = find_standalone_format(CONTAINERS[index]);
  char comp[256], out[256];
  snprintf(comp, sizeof(comp), "tmp_%s_rt.compressed", fmt->name);
  snprintf(out, sizeof(out), "tmp_%s_rt.out", fmt->name);

  TEST_ASSERT_EQUAL_INT_MESSAGE(
      0, standalone_compress(fmt, TEST_INPUT, OVERWRITE_TO(comp), 6, NULL),
      fmt->name);
  TEST_ASSERT_EQUAL_INT_MESSAGE(
      0, standalone_decompress(fmt, comp, NULL, OVERWRITE_TO(out), NULL),
      fmt->name);
  TEST_ASSERT_TRUE_MESSAGE(files_equal(TEST_INPUT, out), fmt->name);

  remove(comp);
  remove(out);
}

static void append_file(const char *dst, const char *src) {
  FILE *in = fopen(src, "rb");
  FILE *out = fopen(dst, "ab");
  TEST_ASSERT_NOT_NULL(in);
  TEST_ASSERT_NOT_NULL(out);

  int c;
  while ((c = fgetc(in)) != EOF) {
    fputc(c, out);
  }
  fclose(in);
  fclose(out);
}

TEST_RANGE([ 0, 4, 1 ])
void test_decodes_concatenated_streams(int index) {
  const FormatDesc *fmt = find_standalone_format(CONTAINERS[index]);
  char comp[256], both[256], out[256];
  snprintf(comp, sizeof(comp), "tmp_%s_cat.compressed", fmt->name);
  snprintf(both, sizeof(both), "tmp_%s_cat.both", fmt->name);
  snprintf(out, sizeof(out), "tmp_%s_cat.out", fmt->name);

  TEST_ASSERT_EQUAL_INT_MESSAGE(
      0, standalone_compress(fmt, TEST_INPUT, OVERWRITE_TO(comp), 6, NULL),
      fmt->name);
  remove(both);
  append_file(both, comp);
  append_file(both, comp);

  TEST_ASSERT_EQUAL_INT_MESSAGE(
      0, standalone_decompress(fmt, both, NULL, OVERWRITE_TO(out), NULL),
      fmt->name);
  TEST_ASSERT_EQUAL_INT_MESSAGE(2 * file_size(TEST_INPUT), file_size(out),
                                fmt->name);

  remove(comp);
  remove(both);
  remove(out);
}

TEST_RANGE([ 0, 4, 1 ])
void test_detects_corruption(int index) {
  const FormatDesc *fmt = find_standalone_format(CONTAINERS[index]);
  char comp[256], out[256];
  snprintf(comp, sizeof(comp), "tmp_%s_cx.compressed", fmt->name);
  snprintf(out, sizeof(out), "tmp_%s_cx.out", fmt->name);

  TEST_ASSERT_EQUAL_INT_MESSAGE(
      0, standalone_compress(fmt, TEST_INPUT, OVERWRITE_TO(comp), 6, NULL),
      fmt->name);

  // Flip a byte in the middle of the compressed payload
  FILE *f = fopen(comp, "rb+");
  TEST_ASSERT_NOT_NULL(f);
  fseek(f, 0, SEEK_END);
  long pos = ftell(f) / 2;
  fseek(f, pos, SEEK_SET);
  int byte = fgetc(f);
  fseek(f, pos, SEEK_SET);
  fputc(byte ^ 0xFF, f);
  fclose(f);

  // Decompression must fail (CRC/checksum or structural error)
  TEST_ASSERT_EQUAL_INT_MESSAGE(
      -1, standalone_decompress(fmt, comp, NULL, OVERWRITE_TO(out), NULL),
      fmt->name);
  TEST_ASSERT_TRUE_MESSAGE(PyErr_ExceptionMatches(comp_CorruptDataError),
                           fmt->name);

  remove(comp);
  remove(out);
}

static const struct {
  const char *name;
  unsigned char bytes[10];
  size_t size;
} BAD_GZIP_HEADERS[] = {
    {"bad magic", {0x1f, 0x8c, 0x08, 0, 0, 0, 0, 0, 0, 0xff}, 10},
    {"unknown method", {0x1f, 0x8b, 0x07, 0, 0, 0, 0, 0, 0, 0xff}, 10},
    {"truncated", {0x1f, 0x8b}, 2},
};

_Static_assert(sizeof(BAD_GZIP_HEADERS) / sizeof(BAD_GZIP_HEADERS[0]) == 3,
               "update TEST_RANGE");

TEST_RANGE([ 0, 2, 1 ])
void test_gzip_bad_header_is_corrupt_data(int index) {
  const char *comp = "tmp_gzip_hdr.gz", *out = "tmp_gzip_hdr.out";
  FILE *f = fopen(comp, "wb");
  TEST_ASSERT_NOT_NULL(f);
  fwrite(BAD_GZIP_HEADERS[index].bytes, 1, BAD_GZIP_HEADERS[index].size, f);
  fclose(f);

  TEST_ASSERT_EQUAL_INT_MESSAGE(
      -1,
      standalone_decompress(find_standalone_format(FORMAT_GZIP), comp, NULL,
                            OVERWRITE_TO(out), NULL),
      BAD_GZIP_HEADERS[index].name);
  TEST_ASSERT_TRUE_MESSAGE(PyErr_ExceptionMatches(comp_CorruptDataError),
                           BAD_GZIP_HEADERS[index].name);

  remove(comp);
  remove(out);
}
