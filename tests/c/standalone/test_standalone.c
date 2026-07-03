/**
 * test_standalone.c - Tests for the standalone single-file formats
 * (gzip, bzip2, xz, zstd, lz4).
 *
 * Each format is exercised through the StandaloneFormat interface:
 *   - getter returns a populated descriptor (name / extension / callbacks)
 *   - is_format() matches the format's magic bytes and rejects others
 *   - a file round-trips byte-for-byte through compress_file/decompress_file
 *   - a single-byte corruption of the compressed file is detected on decompress
 *     (this validates the library's built-in CRC/checksum verification)
 */

#include "../unity.h"
#include "../../../src/compresso/csrc/common.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define TEST_INPUT "../fixtures/alice29.txt"

// The comp_* exception objects live in _core.c in the real extension and are
// defined (as NULL) by the test harness stub. Create them once so error paths
// that call PyErr_SetString() have a valid exception type.
static void ensure_comp_exceptions(void) {
  if (!comp_Error)
    comp_Error = PyErr_NewException("compresso.Error", NULL, NULL);
  if (!comp_HeaderError)
    comp_HeaderError =
        PyErr_NewException("compresso.HeaderError", comp_Error, NULL);
  if (!comp_BackendError)
    comp_BackendError =
        PyErr_NewException("compresso.BackendError", comp_Error, NULL);
}

void setUp(void) {
  if (!Py_IsInitialized()) {
    Py_Initialize();
  }
  ensure_comp_exceptions();
}

void tearDown(void) {
  // Make sure a deliberately-triggered error does not leak into the next test.
  PyErr_Clear();
}

// ---- Helpers ----

static int files_equal(const char *a, const char *b) {
  FILE *fa = fopen(a, "rb");
  FILE *fb = fopen(b, "rb");
  if (!fa || !fb) {
    if (fa)
      fclose(fa);
    if (fb)
      fclose(fb);
    return 0;
  }

  int equal = 1;
  for (;;) {
    int ca = fgetc(fa);
    int cb = fgetc(fb);
    if (ca != cb) {
      equal = 0;
      break;
    }
    if (ca == EOF) {
      break; // both reached EOF together
    }
  }

  fclose(fa);
  fclose(fb);
  return equal;
}

static void round_trip(const StandaloneFormat *fmt) {
  char comp[256], out[256];
  snprintf(comp, sizeof(comp), "tmp_%s_rt.compressed", fmt->name);
  snprintf(out, sizeof(out), "tmp_%s_rt.out", fmt->name);

  TEST_ASSERT_EQUAL_INT_MESSAGE(0, fmt->compress_file(TEST_INPUT, comp, 6),
                                fmt->name);
  TEST_ASSERT_EQUAL_INT_MESSAGE(0, fmt->decompress_file(comp, out), fmt->name);
  TEST_ASSERT_TRUE_MESSAGE(files_equal(TEST_INPUT, out), fmt->name);

  remove(comp);
  remove(out);
}

static void detect_corruption(const StandaloneFormat *fmt) {
  char comp[256], out[256];
  snprintf(comp, sizeof(comp), "tmp_%s_cx.compressed", fmt->name);
  snprintf(out, sizeof(out), "tmp_%s_cx.out", fmt->name);

  TEST_ASSERT_EQUAL_INT_MESSAGE(0, fmt->compress_file(TEST_INPUT, comp, 6),
                                fmt->name);

  // Flip a byte in the middle of the compressed payload.
  FILE *f = fopen(comp, "rb+");
  TEST_ASSERT_NOT_NULL(f);
  fseek(f, 0, SEEK_END);
  long size = ftell(f);
  long pos = size / 2;
  fseek(f, pos, SEEK_SET);
  int c = fgetc(f);
  fseek(f, pos, SEEK_SET);
  fputc(c ^ 0xFF, f);
  fclose(f);

  // Decompression must fail (CRC/checksum or structural error).
  TEST_ASSERT_EQUAL_INT_MESSAGE(-1, fmt->decompress_file(comp, out), fmt->name);
  PyErr_Clear();

  remove(comp);
  remove(out);
}

// ---- gzip ----

void test_gzip_descriptor(void) {
  const StandaloneFormat *fmt = get_gzip_format();
  TEST_ASSERT_NOT_NULL(fmt);
  TEST_ASSERT_EQUAL_STRING("gzip", fmt->name);
  TEST_ASSERT_EQUAL_STRING(".gz", fmt->extension);
}

void test_gzip_is_format(void) {
  const StandaloneFormat *fmt = get_gzip_format();
  unsigned char good[] = {0x1f, 0x8b, 0x08};
  unsigned char bad[] = {'P', 'K'};
  TEST_ASSERT_TRUE(fmt->is_format(good, sizeof(good)));
  TEST_ASSERT_FALSE(fmt->is_format(bad, sizeof(bad)));
}

void test_gzip_round_trip(void) { round_trip(get_gzip_format()); }
void test_gzip_detect_corruption(void) { detect_corruption(get_gzip_format()); }

// ---- bzip2 ----

void test_bzip2_descriptor(void) {
  const StandaloneFormat *fmt = get_bzip2_format();
  TEST_ASSERT_NOT_NULL(fmt);
  TEST_ASSERT_EQUAL_STRING("bzip2", fmt->name);
  TEST_ASSERT_EQUAL_STRING(".bz2", fmt->extension);
  TEST_ASSERT_NULL(fmt->get_original_name("x.bz2"));
}

void test_bzip2_is_format(void) {
  const StandaloneFormat *fmt = get_bzip2_format();
  unsigned char good[] = {'B', 'Z', 'h', '9'};
  unsigned char bad[] = {0x1f, 0x8b};
  TEST_ASSERT_TRUE(fmt->is_format(good, sizeof(good)));
  TEST_ASSERT_FALSE(fmt->is_format(bad, sizeof(bad)));
}

void test_bzip2_round_trip(void) { round_trip(get_bzip2_format()); }
void test_bzip2_detect_corruption(void) {
  detect_corruption(get_bzip2_format());
}

// ---- xz ----

void test_xz_descriptor(void) {
  const StandaloneFormat *fmt = get_xz_format();
  TEST_ASSERT_NOT_NULL(fmt);
  TEST_ASSERT_EQUAL_STRING("xz", fmt->name);
  TEST_ASSERT_EQUAL_STRING(".xz", fmt->extension);
  TEST_ASSERT_NULL(fmt->get_original_name("x.xz"));
}

void test_xz_is_format(void) {
  const StandaloneFormat *fmt = get_xz_format();
  unsigned char good[] = {0xFD, 0x37, 0x7A, 0x58, 0x5A, 0x00};
  unsigned char bad[] = {0xFD, 0x37, 0x7A, 0x00, 0x00, 0x00};
  TEST_ASSERT_TRUE(fmt->is_format(good, sizeof(good)));
  TEST_ASSERT_FALSE(fmt->is_format(bad, sizeof(bad)));
}

void test_xz_round_trip(void) { round_trip(get_xz_format()); }
void test_xz_detect_corruption(void) { detect_corruption(get_xz_format()); }

// ---- zstd ----

void test_zstd_descriptor(void) {
  const StandaloneFormat *fmt = get_zstd_format();
  TEST_ASSERT_NOT_NULL(fmt);
  TEST_ASSERT_EQUAL_STRING("zstd", fmt->name);
  TEST_ASSERT_EQUAL_STRING(".zst", fmt->extension);
  TEST_ASSERT_NULL(fmt->get_original_name("x.zst"));
}

void test_zstd_is_format(void) {
  const StandaloneFormat *fmt = get_zstd_format();
  unsigned char good[] = {0x28, 0xB5, 0x2F, 0xFD};
  unsigned char bad[] = {0x28, 0xB5, 0x2F, 0x00};
  TEST_ASSERT_TRUE(fmt->is_format(good, sizeof(good)));
  TEST_ASSERT_FALSE(fmt->is_format(bad, sizeof(bad)));
}

void test_zstd_round_trip(void) { round_trip(get_zstd_format()); }
void test_zstd_detect_corruption(void) { detect_corruption(get_zstd_format()); }

// ---- lz4 ----

void test_lz4_descriptor(void) {
  const StandaloneFormat *fmt = get_lz4_format();
  TEST_ASSERT_NOT_NULL(fmt);
  TEST_ASSERT_EQUAL_STRING("lz4", fmt->name);
  TEST_ASSERT_EQUAL_STRING(".lz4", fmt->extension);
  TEST_ASSERT_NULL(fmt->get_original_name("x.lz4"));
}

void test_lz4_is_format(void) {
  const StandaloneFormat *fmt = get_lz4_format();
  unsigned char good[] = {0x04, 0x22, 0x4D, 0x18};
  unsigned char bad[] = {0x04, 0x22, 0x4D, 0x00};
  TEST_ASSERT_TRUE(fmt->is_format(good, sizeof(good)));
  TEST_ASSERT_FALSE(fmt->is_format(bad, sizeof(bad)));
}

void test_lz4_round_trip(void) { round_trip(get_lz4_format()); }
void test_lz4_detect_corruption(void) { detect_corruption(get_lz4_format()); }

// ---- registry ----

void test_registry_resolves_all_standalone_formats(void) {
  TEST_ASSERT_NOT_NULL(find_standalone_format(FORMAT_GZIP));
  TEST_ASSERT_NOT_NULL(find_standalone_format(FORMAT_BZIP2));
  TEST_ASSERT_NOT_NULL(find_standalone_format(FORMAT_XZ));
  TEST_ASSERT_NOT_NULL(find_standalone_format(FORMAT_ZSTD));
  TEST_ASSERT_NOT_NULL(find_standalone_format(FORMAT_LZ4));
  TEST_ASSERT_NULL(find_standalone_format(FORMAT_ZIP));
}
