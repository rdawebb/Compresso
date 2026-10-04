// Tests the .comp container: compress_file and decompress_file end to end

// Makes Unity define TEST_RANGE; the runner generator expands each one
#define UNITY_SUPPORT_TEST_CASES

#define PY_SSIZE_T_CLEAN
#include "common.h"
#include "files.h"
#include "fsutil.h"
#include "test_stubs.h"
#include "unity.h"
#include <Python.h>
#include <stdio.h>
#include <string.h>

#define TEST_INPUT FIXTURE_DIR "/alice29.txt"

enum { OW_ERROR = 0, OW_SKIP = 1, OW_OVERWRITE = 2, OW_RENAME = 3 };

static const AlgoID ALGOS[] = {ALGO_ZLIB, ALGO_BZIP2, ALGO_LZMA,
                               ALGO_ZSTD, ALGO_LZ4,   ALGO_SNAPPY};

// Every TEST_RANGE over ALGOS must span exactly these rows
_Static_assert(sizeof(ALGOS) / sizeof(ALGOS[0]) == 6, "update TEST_RANGE");

void setUp(void) {
  if (!Py_IsInitialized()) {
    Py_Initialize();
  }
  ensure_comp_exceptions();
}

void tearDown(void) { PyErr_Clear(); }

static int compress(const char *src, const char *dst, AlgoID algo, int level,
                    int overwrite, char *actual, size_t actual_size) {
  return compress_file(src, dst, algo, STRAT_BALANCED, level, overwrite, actual,
                       actual_size, NULL);
}

static CHeader read_header(const char *path) {
  uint8_t buf[C_HEADER_SIZE];
  FILE *f = fopen(path, "rb");
  TEST_ASSERT_NOT_NULL(f);
  TEST_ASSERT_EQUAL_size_t(sizeof(buf), fread(buf, 1, sizeof(buf), f));
  fclose(f);

  CHeader header;
  c_header_unpack(buf, &header);
  return header;
}

// A .comp file holding only `header` and a few payload bytes
static void write_comp(const char *path, const CHeader *header) {
  uint8_t buf[C_HEADER_SIZE];
  c_header_pack(header, buf);

  FILE *f = fopen(path, "wb");
  TEST_ASSERT_NOT_NULL(f);
  fwrite(buf, 1, sizeof(buf), f);
  fwrite("payload", 1, 7, f);
  fclose(f);
}

static CHeader valid_header(void) {
  CHeader header = {
      .version = 1, .algo = ALGO_ZLIB, .level = 255, .orig_size = 5};
  memcpy(header.magic, C_MAGIC, C_MAGIC_LEN);
  return header;
}

static void assert_error(int rc, PyObject *type) {
  TEST_ASSERT_EQUAL_INT(-1, rc);
  TEST_ASSERT_NOT_NULL(PyErr_Occurred());
  TEST_ASSERT_TRUE(PyErr_ExceptionMatches(type));
  PyErr_Clear();
}

static int file_holds(const char *path, const char *contents) {
  write_file("tmp_cmp_expected", contents);
  int equal = files_equal(path, "tmp_cmp_expected");
  remove("tmp_cmp_expected");
  return equal;
}

// ---- Round trip and header ----

TEST_RANGE([ 0, 5, 1 ])
void test_round_trip(int index) {
  AlgoID algo = ALGOS[index];
  char comp[64], out[64];
  snprintf(comp, sizeof(comp), "tmp_cmp_rt_%d.comp", (int)algo);
  snprintf(out, sizeof(out), "tmp_cmp_rt_%d.out", (int)algo);

  TEST_ASSERT_EQUAL_INT(
      0, compress(TEST_INPUT, comp, algo, -1, OW_OVERWRITE, NULL, 0));
  TEST_ASSERT_EQUAL_INT(0, decompress_file(comp, out, ALGO_NONE, NULL));
  TEST_ASSERT_TRUE(files_equal(TEST_INPUT, out));

  remove(comp);
  remove(out);
}

void test_header_records_algo_level_and_size(void) {
  const char *comp = "tmp_cmp_header.comp";
  TEST_ASSERT_EQUAL_INT(
      0, compress(TEST_INPUT, comp, ALGO_ZSTD, 3, OW_OVERWRITE, NULL, 0));

  CHeader header = read_header(comp);
  TEST_ASSERT_EQUAL_MEMORY(C_MAGIC, header.magic, C_MAGIC_LEN);
  TEST_ASSERT_EQUAL_UINT8(1, header.version);
  TEST_ASSERT_EQUAL_UINT8(ALGO_ZSTD, header.algo);
  TEST_ASSERT_EQUAL_UINT8(3, header.level);
  TEST_ASSERT_EQUAL_UINT8(0, header.flags);
  TEST_ASSERT_EQUAL_UINT64((uint64_t)file_size(TEST_INPUT), header.orig_size);

  remove(comp);
}

void test_header_stores_the_default_level_as_255(void) {
  const char *comp = "tmp_cmp_default.comp";
  TEST_ASSERT_EQUAL_INT(
      0, compress(TEST_INPUT, comp, ALGO_ZLIB, -1, OW_OVERWRITE, NULL, 0));
  TEST_ASSERT_EQUAL_UINT8(255, read_header(comp).level);
  remove(comp);
}

void test_strategy_picks_the_backend_without_an_algo(void) {
  const char *comp = "tmp_cmp_strategy.comp";
  TEST_ASSERT_EQUAL_INT(0,
                        compress_file(TEST_INPUT, comp, ALGO_NONE, STRAT_FAST,
                                      -1, OW_OVERWRITE, NULL, 0, NULL));
  TEST_ASSERT_EQUAL_UINT8(choose_backend(STRAT_FAST)->id,
                          read_header(comp).algo);
  remove(comp);
}

void test_compress_refuses_empty_input(void) {
  write_file("tmp_cmp_empty", "");
  assert_error(compress("tmp_cmp_empty", "tmp_cmp_empty.comp", ALGO_ZLIB, -1,
                        OW_OVERWRITE, NULL, 0),
               PyExc_ValueError);
  TEST_ASSERT_EQUAL_INT(-1, file_size("tmp_cmp_empty.comp"));
  remove("tmp_cmp_empty");
}

void test_compress_reports_a_missing_source(void) {
  assert_error(compress("tmp_cmp_missing", "tmp_cmp_missing.comp", ALGO_ZLIB,
                        -1, OW_OVERWRITE, NULL, 0),
               PyExc_FileNotFoundError);
  TEST_ASSERT_EQUAL_INT(-1, file_size("tmp_cmp_missing.comp"));
}

// ---- Overwrite modes ----

void test_overwrite_error_keeps_the_existing_file(void) {
  const char *dst = "tmp_cmp_ow_error.comp";
  write_file(dst, "keep");

  assert_error(compress(TEST_INPUT, dst, ALGO_ZLIB, -1, OW_ERROR, NULL, 0),
               PyExc_FileExistsError);
  TEST_ASSERT_TRUE(file_holds(dst, "keep"));

  remove(dst);
}

void test_overwrite_skip_keeps_the_existing_file(void) {
  const char *dst = "tmp_cmp_ow_skip.comp";
  write_file(dst, "keep");

  char actual[FS_PATH_MAX];
  TEST_ASSERT_EQUAL_INT(0, compress(TEST_INPUT, dst, ALGO_ZLIB, -1, OW_SKIP,
                                    actual, sizeof(actual)));
  TEST_ASSERT_EQUAL_STRING(dst, actual);
  TEST_ASSERT_TRUE(file_holds(dst, "keep"));

  remove(dst);
}

void test_overwrite_replaces_the_existing_file(void) {
  const char *dst = "tmp_cmp_ow_replace.comp";
  write_file(dst, "keep");

  TEST_ASSERT_EQUAL_INT(
      0, compress(TEST_INPUT, dst, ALGO_ZLIB, -1, OW_OVERWRITE, NULL, 0));
  TEST_ASSERT_EQUAL_UINT8(ALGO_ZLIB, read_header(dst).algo);

  remove(dst);
}

void test_overwrite_rename_writes_beside_the_existing_file(void) {
  const char *dst = "tmp_cmp_ow_rename.comp";
  write_file(dst, "keep");

  char actual[FS_PATH_MAX];
  TEST_ASSERT_EQUAL_INT(0, compress(TEST_INPUT, dst, ALGO_ZLIB, -1, OW_RENAME,
                                    actual, sizeof(actual)));
  TEST_ASSERT_TRUE(strcmp(dst, actual) != 0);
  TEST_ASSERT_TRUE(file_holds(dst, "keep"));
  TEST_ASSERT_EQUAL_UINT8(ALGO_ZLIB, read_header(actual).algo);

  remove(dst);
  remove(actual);
}

// ---- Decompression errors ----

void test_decompress_rejects_an_unknown_format(void) {
  write_file("tmp_cmp_plain.txt", "not compressed at all");
  assert_error(decompress_file("tmp_cmp_plain.txt", "tmp_cmp_plain.out",
                               ALGO_NONE, NULL),
               comp_Error);
  remove("tmp_cmp_plain.txt");
}

void test_decompress_rejects_an_unsupported_version(void) {
  CHeader header = valid_header();
  header.version = 2;
  write_comp("tmp_cmp_v2.comp", &header);

  assert_error(
      decompress_file("tmp_cmp_v2.comp", "tmp_cmp_v2.out", ALGO_NONE, NULL),
      comp_HeaderError);
  TEST_ASSERT_EQUAL_INT(-1, file_size("tmp_cmp_v2.out"));

  remove("tmp_cmp_v2.comp");
}

void test_decompress_decodes_with_the_header_algo(void) {
  const char *comp = "tmp_cmp_algo_ok.comp";
  const char *out = "tmp_cmp_algo_ok.out";
  TEST_ASSERT_EQUAL_INT(0, compress(TEST_INPUT, comp, ALGO_ZSTD, -1,
                                    OW_OVERWRITE, NULL, 0));

  // Naming the right algorithm is allowed, as a check
  TEST_ASSERT_EQUAL_INT(0, decompress_file(comp, out, ALGO_ZSTD, NULL));
  TEST_ASSERT_TRUE(files_equal(TEST_INPUT, out));

  remove(comp);
  remove(out);
}

void test_decompress_refuses_an_algo_the_header_contradicts(void) {
  const char *comp = "tmp_cmp_algo_bad.comp";
  const char *out = "tmp_cmp_algo_bad.out";
  TEST_ASSERT_EQUAL_INT(0, compress(TEST_INPUT, comp, ALGO_ZSTD, -1,
                                    OW_OVERWRITE, NULL, 0));

  assert_error(decompress_file(comp, out, ALGO_LZ4, NULL), PyExc_ValueError);
  TEST_ASSERT_EQUAL_INT(-1, file_size(out));

  remove(comp);
}

void test_decompress_rejects_an_unknown_header_algo(void) {
  CHeader header = valid_header();
  header.algo = 99;
  write_comp("tmp_cmp_algo.comp", &header);

  assert_error(
      decompress_file("tmp_cmp_algo.comp", "tmp_cmp_algo.out", ALGO_NONE, NULL),
      comp_HeaderError);
  TEST_ASSERT_EQUAL_INT(-1, file_size("tmp_cmp_algo.out"));

  remove("tmp_cmp_algo.comp");
}

void test_decompress_rejects_a_truncated_header(void) {
  write_file("tmp_cmp_short.comp", "COMP\x01\x01");

  assert_error(decompress_file("tmp_cmp_short.comp", "tmp_cmp_short.out",
                               ALGO_NONE, NULL),
               comp_HeaderError);
  TEST_ASSERT_EQUAL_INT(-1, file_size("tmp_cmp_short.out"));

  remove("tmp_cmp_short.comp");
}

void test_decompress_removes_output_of_a_corrupt_payload(void) {
  const char *comp = "tmp_cmp_corrupt.comp";
  const char *out = "tmp_cmp_corrupt.out";
  // bzip2 checks a CRC per block, so a flipped byte is always caught
  TEST_ASSERT_EQUAL_INT(
      0, compress(TEST_INPUT, comp, ALGO_BZIP2, -1, OW_OVERWRITE, NULL, 0));

  FILE *f = fopen(comp, "rb+");
  TEST_ASSERT_NOT_NULL(f);
  fseek(f, 0, SEEK_END);
  long pos = ftell(f) / 2;
  fseek(f, pos, SEEK_SET);
  int byte = fgetc(f);
  fseek(f, pos, SEEK_SET);
  fputc(byte ^ 0xFF, f);
  fclose(f);

  TEST_ASSERT_EQUAL_INT(-1, decompress_file(comp, out, ALGO_NONE, NULL));
  TEST_ASSERT_TRUE(PyErr_ExceptionMatches(comp_CorruptDataError));
  TEST_ASSERT_EQUAL_INT(-1, file_size(out));

  remove(comp);
}

void test_decompress_rejects_data_after_the_payload(void) {
  const char *comp = "tmp_cmp_trailing.comp";
  TEST_ASSERT_EQUAL_INT(
      0, compress(TEST_INPUT, comp, ALGO_ZLIB, -1, OW_OVERWRITE, NULL, 0));
  FILE *f = fopen(comp, "ab");
  TEST_ASSERT_NOT_NULL(f);
  fputs("trailing", f);
  fclose(f);

  // .comp holds exactly one stream, so has no trailing data to tolerate
  assert_error(decompress_file(comp, "tmp_cmp_trailing.out", ALGO_NONE, NULL),
               comp_CorruptDataError);
  TEST_ASSERT_EQUAL_INT(-1, file_size("tmp_cmp_trailing.out"));

  remove(comp);
}
