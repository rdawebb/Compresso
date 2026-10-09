// Each malformed stream below differs from a valid one in a single way, so a
// CorruptDataError can only come from what each test checks

#include "codec/codec.h"
#include "codec/crc32c.h"
#include "common.h"
#include "files.h"
#include "test_stubs.h"
#include "unity.h"
#include <stdio.h>
#include <string.h>

#define TEST_INPUT FIXTURE_DIR "/alice29.txt"
#define TMP_IN "tmp_snappy.in"
#define TMP_OUT "tmp_snappy.out"
#define TMP_BACK "tmp_snappy.back"

#define STREAM_ID "\xff\x06\x00\x00sNaPpY"
// An uncompressed chunk of "hello", with its masked CRC-32C
#define HELLO "\x01\x09\x00\x00\xbb\x1f\x1c\x19hello"

void setUp(void) {
  if (!Py_IsInitialized()) {
    Py_Initialize();
  }
  ensure_comp_exceptions();
  crc32c_init();
  PyErr_Clear();
}

void tearDown(void) {
  PyErr_Clear();
  remove(TMP_IN);
  remove(TMP_OUT);
  remove(TMP_BACK);
}

static int run(int decompress, const char *in, const char *out) {
  CodecParams params = {0};
  return codec_run_file(codec_snappy_ops(), &params, decompress, in,
                        OVERWRITE_TO(out), NULL, "snappy run failed");
}

static void write_bytes(const char *path, const char *bytes, size_t n) {
  FILE *f = fopen(path, "wb");
  TEST_ASSERT_NOT_NULL(f);
  TEST_ASSERT_EQUAL_size_t(n, fwrite(bytes, 1, n, f));
  fclose(f);
}

// Decodes `n` bytes of `stream`; on success, checks the output is `expected`
static int decode(const char *stream, size_t n, const char *expected) {
  write_bytes(TMP_IN, stream, n);
  int rc = run(1, TMP_IN, TMP_OUT);
  if (rc == 0) {
    write_file(TMP_BACK, expected);
    TEST_ASSERT_TRUE(files_equal(TMP_BACK, TMP_OUT));
  }
  return rc;
}

#define DECODE(stream, expected) decode(stream, sizeof(stream) - 1, expected)

static void assert_corrupt(int rc) {
  TEST_ASSERT_EQUAL_INT(-1, rc);
  TEST_ASSERT_TRUE(PyErr_ExceptionMatches(comp_CorruptDataError));
}

static unsigned char head[11];

static void read_head(const char *path) {
  FILE *f = fopen(path, "rb");
  TEST_ASSERT_NOT_NULL(f);
  TEST_ASSERT_EQUAL_size_t(sizeof(head), fread(head, 1, sizeof(head), f));
  fclose(f);
}

// ---- Encoding ----

void test_round_trips_a_multi_chunk_file(void) {
  // alice29.txt is 152 KB: two full chunks and a partial one
  TEST_ASSERT_EQUAL_INT(0, run(0, TEST_INPUT, TMP_OUT));
  read_head(TMP_OUT);
  TEST_ASSERT_EQUAL_MEMORY(STREAM_ID, head, 10);
  TEST_ASSERT_EQUAL_HEX8(0x00, head[10]); // Text compresses

  TEST_ASSERT_EQUAL_INT(0, run(1, TMP_OUT, TMP_BACK));
  TEST_ASSERT_TRUE(files_equal(TEST_INPUT, TMP_BACK));
}

void test_stores_data_that_does_not_shrink_uncompressed(void) {
  unsigned char noise[1000];
  uint32_t x = 1;
  for (size_t i = 0; i < sizeof(noise); i++) {
    x = x * 1103515245 + 12345;
    noise[i] = (unsigned char)(x >> 24);
  }
  write_bytes(TMP_IN, (const char *)noise, sizeof(noise));

  TEST_ASSERT_EQUAL_INT(0, run(0, TMP_IN, TMP_OUT));
  read_head(TMP_OUT);
  TEST_ASSERT_EQUAL_HEX8(0x01, head[10]);
  TEST_ASSERT_EQUAL_INT(0, run(1, TMP_OUT, TMP_BACK));
  TEST_ASSERT_TRUE(files_equal(TMP_IN, TMP_BACK));
}

void test_encodes_empty_input_as_the_stream_identifier(void) {
  write_file(TMP_IN, "");
  TEST_ASSERT_EQUAL_INT(0, run(0, TMP_IN, TMP_OUT));
  TEST_ASSERT_EQUAL_INT(10, file_size(TMP_OUT));
}

// ---- Decoding ----

void test_decodes_an_uncompressed_chunk(void) {
  TEST_ASSERT_EQUAL_INT(0, DECODE(STREAM_ID HELLO, "hello"));
}

void test_skips_padding_skippable_chunks_and_repeated_identifiers(void) {
  TEST_ASSERT_EQUAL_INT(0, DECODE(STREAM_ID "\xfe\x03\x00\x00pad" HELLO
                                            "\x80\x02\x00\x00xx" STREAM_ID HELLO
                                            "\xfd\x00\x00\x00",
                                  "hellohello"));
}

void test_decodes_empty_input_as_empty(void) {
  TEST_ASSERT_EQUAL_INT(0, decode("", 0, ""));
}

// HELLO with an unskippable type in place of its own
void test_rejects_a_reserved_unskippable_chunk(void) {
  assert_corrupt(DECODE(STREAM_ID "\x02\x09\x00\x00\xbb\x1f\x1c\x19hello", ""));
}

void test_rejects_a_bad_checksum(void) {
  assert_corrupt(DECODE(STREAM_ID "\x01\x09\x00\x00\xbc\x1f\x1c\x19hello", ""));
}

void test_rejects_a_missing_stream_identifier(void) {
  assert_corrupt(DECODE(HELLO, ""));
}

void test_rejects_padding_before_the_stream_identifier(void) {
  assert_corrupt(DECODE("\xfe\x00\x00\x00" STREAM_ID HELLO, ""));
}

void test_rejects_a_wrong_stream_identifier(void) {
  assert_corrupt(DECODE("\xff\x06\x00\x00sNaPpX" HELLO, ""));
}

void test_rejects_a_truncated_chunk(void) {
  assert_corrupt(DECODE(STREAM_ID "\x01\x09\x00\x00\xbb\x1f\x1c\x19hel", ""));
}

void test_rejects_a_truncated_skippable_chunk(void) {
  assert_corrupt(DECODE(STREAM_ID "\x80\x04\x00\x00xx", ""));
}
