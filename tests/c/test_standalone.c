// Makes Unity define TEST_RANGE; the runner generator expands each one
#define UNITY_SUPPORT_TEST_CASES

#include "common.h"
#include "files.h"
#include "standalone.h"
#include "test_stubs.h"
#include "unity.h"
#include <stdio.h>

#define TEST_INPUT FIXTURE_DIR "/alice29.txt"

typedef struct {
  const StandaloneFormat *(*get)(void);
  Format format;
  const char *name;
  const char *extension;
  // gzip reads a stored name back from the file; the rest never store one
  int stores_name;
  unsigned char magic[6];
  size_t magic_size;
  // The magic with one byte wrong, which must not be taken for the format
  unsigned char near_miss[6];
  size_t near_miss_size;
} FormatCase;

static const FormatCase FORMATS[] = {
    {get_gzip_format,
     FORMAT_GZIP,
     "gzip",
     ".gz",
     1,
     {0x1f, 0x8b, 0x08},
     3,
     {0x1f, 0x8c, 0x08},
     3},
    {get_bzip2_format,
     FORMAT_BZIP2,
     "bzip2",
     ".bz2",
     0,
     {'B', 'Z', 'h', '9'},
     4,
     {'B', 'Z', 'x', '9'},
     4},
    {get_xz_format,
     FORMAT_XZ,
     "xz",
     ".xz",
     0,
     {0xFD, 0x37, 0x7A, 0x58, 0x5A, 0x00},
     6,
     {0xFD, 0x37, 0x7A, 0x00, 0x00, 0x00},
     6},
    {get_zstd_format,
     FORMAT_ZSTD,
     "zstd",
     ".zst",
     0,
     {0x28, 0xB5, 0x2F, 0xFD},
     4,
     {0x28, 0xB5, 0x2F, 0x00},
     4},
    {get_lz4_format,
     FORMAT_LZ4,
     "lz4",
     ".lz4",
     0,
     {0x04, 0x22, 0x4D, 0x18},
     4,
     {0x04, 0x22, 0x4D, 0x00},
     4},
};

// Every TEST_RANGE below must span exactly these rows
_Static_assert(sizeof(FORMATS) / sizeof(FORMATS[0]) == 5, "update TEST_RANGE");

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
void test_descriptor(int index) {
  const FormatCase *c = &FORMATS[index];
  const StandaloneFormat *fmt = c->get();

  TEST_ASSERT_NOT_NULL_MESSAGE(fmt, c->name);
  TEST_ASSERT_EQUAL_STRING(c->name, fmt->name);
  TEST_ASSERT_EQUAL_STRING(c->extension, fmt->extension);
  if (!c->stores_name) {
    TEST_ASSERT_NULL_MESSAGE(fmt->get_original_name("x"), c->name);
  }
}

TEST_RANGE([ 0, 4, 1 ])
void test_is_format_needs_the_whole_magic(int index) {
  const FormatCase *c = &FORMATS[index];
  const StandaloneFormat *fmt = c->get();

  TEST_ASSERT_TRUE_MESSAGE(fmt->is_format(c->magic, c->magic_size), c->name);
  TEST_ASSERT_FALSE_MESSAGE(fmt->is_format(c->near_miss, c->near_miss_size),
                            c->name);
}

TEST_RANGE([ 0, 4, 1 ])
void test_registry_resolves_the_same_descriptor(int index) {
  const FormatCase *c = &FORMATS[index];

  TEST_ASSERT_EQUAL_PTR_MESSAGE(c->get(), find_standalone_format(c->format),
                                c->name);
}

void test_registry_has_no_standalone_zip(void) {
  TEST_ASSERT_NULL(find_standalone_format(FORMAT_ZIP));
}

TEST_RANGE([ 0, 4, 1 ])
void test_round_trip(int index) {
  const StandaloneFormat *fmt = FORMATS[index].get();
  char comp[256], out[256];
  snprintf(comp, sizeof(comp), "tmp_%s_rt.compressed", fmt->name);
  snprintf(out, sizeof(out), "tmp_%s_rt.out", fmt->name);

  TEST_ASSERT_EQUAL_INT_MESSAGE(
      0, fmt->compress_file(TEST_INPUT, comp, 6, NULL), fmt->name);
  TEST_ASSERT_EQUAL_INT_MESSAGE(0, fmt->decompress_file(comp, out, NULL),
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
  const StandaloneFormat *fmt = FORMATS[index].get();
  char comp[256], both[256], out[256];
  snprintf(comp, sizeof(comp), "tmp_%s_cat.compressed", fmt->name);
  snprintf(both, sizeof(both), "tmp_%s_cat.both", fmt->name);
  snprintf(out, sizeof(out), "tmp_%s_cat.out", fmt->name);

  TEST_ASSERT_EQUAL_INT_MESSAGE(
      0, fmt->compress_file(TEST_INPUT, comp, 6, NULL), fmt->name);
  remove(both);
  append_file(both, comp);
  append_file(both, comp);

  TEST_ASSERT_EQUAL_INT_MESSAGE(0, fmt->decompress_file(both, out, NULL),
                                fmt->name);
  TEST_ASSERT_EQUAL_INT_MESSAGE(2 * file_size(TEST_INPUT), file_size(out),
                                fmt->name);

  remove(comp);
  remove(both);
  remove(out);
}

TEST_RANGE([ 0, 4, 1 ])
void test_detects_corruption(int index) {
  const StandaloneFormat *fmt = FORMATS[index].get();
  char comp[256], out[256];
  snprintf(comp, sizeof(comp), "tmp_%s_cx.compressed", fmt->name);
  snprintf(out, sizeof(out), "tmp_%s_cx.out", fmt->name);

  TEST_ASSERT_EQUAL_INT_MESSAGE(
      0, fmt->compress_file(TEST_INPUT, comp, 6, NULL), fmt->name);

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
  TEST_ASSERT_EQUAL_INT_MESSAGE(-1, fmt->decompress_file(comp, out, NULL),
                                fmt->name);
  TEST_ASSERT_TRUE_MESSAGE(PyErr_ExceptionMatches(comp_CorruptDataError),
                           fmt->name);

  remove(comp);
  remove(out);
}
