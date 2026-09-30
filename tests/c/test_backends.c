// Tests every compression backend through one table

// Makes Unity define TEST_RANGE; the runner generator expands each one
#define UNITY_SUPPORT_TEST_CASES

#include "backend_io.h"
#include "codec/codec.h"
#include "unity.h"
#include <lz4hc.h>
#include <stdlib.h>
#include <string.h>
#include <zlib.h>
#include <zstd.h>

const CBackend *get_zlib_backend(void);
const CBackend *get_bzip2_backend(void);
const CBackend *get_lzma_backend(void);
const CBackend *get_zstd_backend(void);
const CBackend *get_lz4_backend(void);
const CBackend *get_snappy_backend(void);

typedef struct {
  const CBackend *backend;
  uint8_t id;
  const char *name;
  // The range the library itself defines, to check the backend's against
  int min_level, max_level;
  // A low and high level to compare; equal for a backend without levels
  int level_lo, level_hi;
} BackendCase;

// Every TEST_RANGE below runs over these rows, so it must span exactly them
#define BACKEND_COUNT 6

static BackendCase backend_case(int index) {
  // Built per call, because zstd only reports its maximum level at run time
  const BackendCase cases[] = {
      {get_zlib_backend(), ALGO_ZLIB, "zlib", Z_NO_COMPRESSION,
       Z_BEST_COMPRESSION, 1, 9},
      {get_bzip2_backend(), ALGO_BZIP2, "bzip2", 1, 9, 1, 9},
      {get_lzma_backend(), ALGO_LZMA, "lzma", 0, 9, 0, 9},
      // Levels past 19 allocate a large window, slow even on a small input
      {get_zstd_backend(), ALGO_ZSTD, "zstd", 1, ZSTD_maxCLevel(), 1, 19},
      {get_lz4_backend(), ALGO_LZ4, "lz4", 0, LZ4HC_CLEVEL_MAX, 1, 12},
      {get_snappy_backend(), ALGO_SNAPPY, "snappy", -1, -1, -1, -1},
  };

  TEST_ASSERT_EQUAL_size_t(BACKEND_COUNT, sizeof(cases) / sizeof(cases[0]));
  TEST_ASSERT_TRUE(index >= 0 && index < BACKEND_COUNT);
  return cases[index];
}

static unsigned char *alice;
static size_t alice_size;

void setUp(void) {
  if (!Py_IsInitialized()) {
    Py_Initialize();
  }

  alice = read_fixture(FIXTURE_DIR "/alice29.txt", &alice_size);
  TEST_ASSERT_NOT_NULL(alice);
}

void tearDown(void) {
  free(alice);
  alice = NULL;
}

// Deterministic bytes no codec can shrink, unlike a short repeating pattern
static unsigned char *incompressible(size_t size) {
  unsigned char *data = safe_malloc(size);
  uint32_t state = 0x12345678;
  for (size_t i = 0; i < size; i++) {
    state ^= state << 13;
    state ^= state >> 17;
    state ^= state << 5;
    data[i] = (unsigned char)state;
  }
  return data;
}

static size_t compressed_size(const CBackend *backend, int level) {
  unsigned char *output = NULL;
  size_t output_size = 0;

  TEST_ASSERT_EQUAL_INT_MESSAGE(0,
                                stream_compress_bytes(backend, alice,
                                                      alice_size, level,
                                                      &output, &output_size),
                                backend->name);

  free(output);
  return output_size;
}

TEST_RANGE([ 0, 5, 1 ])
void test_backend_identifies_itself(int index) {
  BackendCase c = backend_case(index);

  TEST_ASSERT_NOT_NULL_MESSAGE(c.backend, c.name);
  // Every backend is a hard link-time dependency, so none may be missing
  TEST_ASSERT_TRUE_MESSAGE(c.backend->is_available(), c.name);
  TEST_ASSERT_EQUAL_UINT8_MESSAGE(c.id, c.backend->id, c.name);
  TEST_ASSERT_EQUAL_STRING(c.name, c.backend->name);
}

TEST_RANGE([ 0, 5, 1 ])
void test_level_range_matches_the_library(int index) {
  BackendCase c = backend_case(index);

  TEST_ASSERT_EQUAL_INT_MESSAGE(c.min_level, c.backend->levels.min, c.name);
  TEST_ASSERT_EQUAL_INT_MESSAGE(c.max_level, c.backend->levels.max, c.name);
}

TEST_RANGE([ 0, 5, 1 ])
void test_text_round_trips_at_low_and_high_levels(int index) {
  BackendCase c = backend_case(index);

  assert_stream_round_trip(c.backend, alice, alice_size, c.level_lo);
  assert_stream_round_trip(c.backend, alice, alice_size, c.level_hi);
}

TEST_RANGE([ 0, 5, 1 ])
void test_higher_level_is_no_larger(int index) {
  BackendCase c = backend_case(index);

  TEST_ASSERT_LESS_OR_EQUAL_size_t_MESSAGE(
      compressed_size(c.backend, c.level_lo),
      compressed_size(c.backend, c.level_hi), c.name);
}

TEST_RANGE([ 0, 5, 1 ])
void test_repetitive_data_shrinks_and_round_trips(int index) {
  BackendCase c = backend_case(index);

  size_t input_size = 100000;
  unsigned char *data = safe_malloc(input_size);
  const char *pattern = "The quick brown fox jumps over the lazy dog. ";
  size_t pattern_len = strlen(pattern);
  for (size_t i = 0; i < input_size; i++) {
    data[i] = (unsigned char)pattern[i % pattern_len];
  }

  unsigned char *output = NULL;
  size_t output_size = 0;
  TEST_ASSERT_EQUAL_INT(0, stream_compress_bytes(c.backend, data, input_size,
                                                 -1, &output, &output_size));
  TEST_ASSERT_LESS_THAN_size_t_MESSAGE(input_size / 2, output_size, c.name);

  assert_stream_round_trip(c.backend, data, input_size, -1);

  free(output);
  free(data);
}

TEST_RANGE([ 0, 5, 1 ])
void test_incompressible_data_round_trips(int index) {
  BackendCase c = backend_case(index);

  size_t input_size = 64 * 1024;
  unsigned char *data = incompressible(input_size);

  assert_stream_round_trip(c.backend, data, input_size, -1);

  free(data);
}

TEST_RANGE([ 0, 5, 1 ])
void test_empty_input_round_trips(int index) {
  BackendCase c = backend_case(index);

  assert_stream_round_trip(c.backend, (const unsigned char *)"", 0, -1);
}

// ---- lz4 engine ----

// The driver's fread only comes up short at end of input
void test_lz4_engine_takes_uneven_input(void) {
  const CodecOps *ops = codec_lz4_ops();
  CodecParams params = {.level = -1, .checksum = 1};
  void *state = calloc(1, ops->state_size);
  TEST_ASSERT_NOT_NULL(state);
  TEST_ASSERT_EQUAL_INT(0, ops->begin(state, &params, 0, NULL));

  size_t out_size = ops->out_chunk;
  unsigned char *out = malloc(out_size);
  TEST_ASSERT_NOT_NULL(out);

  // A short piece leaves a partial block buffered, so the full chunk after it
  // completes that block and starts another
  const size_t pieces[] = {1000, CODEC_CHUNK, 1, CODEC_CHUNK};
  const unsigned char *next = incompressible(3 * CODEC_CHUNK);
  const unsigned char *input = next;
  for (size_t i = 0; i < sizeof(pieces) / sizeof(pieces[0]); i++) {
    CodecBuf buf = {next, pieces[i], out, out_size};
    while (buf.avail_in > 0) {
      buf.next_out = out;
      buf.avail_out = out_size;
      TEST_ASSERT_NOT_EQUAL_INT_MESSAGE(CODEC_ERR, ops->process(state, &buf, 0),
                                        "piece rejected");
    }
    next += pieces[i];
  }

  CodecBuf buf = {next, 0, out, out_size};
  TEST_ASSERT_EQUAL_INT(CODEC_DONE, ops->process(state, &buf, 1));

  ops->end(state);
  free(state);
  free(out);
  free((void *)input);
}
