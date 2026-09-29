#define PY_SSIZE_T_CLEAN
#include "archives.h"
#include "unity.h"
#include "validate.h"
#include <Python.h>

void setUp(void) {
  if (!Py_IsInitialized()) {
    Py_Initialize();
  }
}

void tearDown(void) { PyErr_Clear(); }

static void assert_value_error(int rc) {
  TEST_ASSERT_EQUAL_INT(-1, rc);
  TEST_ASSERT_TRUE(PyErr_ExceptionMatches(PyExc_ValueError));
  PyErr_Clear();
}

// ---- validate_level ----

void test_level_accepts_the_range_bounds_and_default(void) {
  LevelRange range = {1, 9};
  TEST_ASSERT_EQUAL_INT(0, validate_level("x", range, 1));
  TEST_ASSERT_EQUAL_INT(0, validate_level("x", range, 9));
  TEST_ASSERT_EQUAL_INT(0, validate_level("x", range, -1));
}

void test_level_rejects_just_outside_the_range(void) {
  LevelRange range = {1, 9};
  assert_value_error(validate_level("x", range, 0));
  assert_value_error(validate_level("x", range, 10));
}

void test_level_without_levels_takes_only_the_default(void) {
  LevelRange none = LEVELS_NONE;
  TEST_ASSERT_EQUAL_INT(0, validate_level("x", none, -1));
  assert_value_error(validate_level("x", none, 0));
}

// ---- validate_overwrite_arg ----

void test_overwrite_accepts_the_four_modes(void) {
  for (int mode = 0; mode <= 3; mode++) {
    TEST_ASSERT_EQUAL_INT(0, validate_overwrite_arg(mode));
  }
}

void test_overwrite_rejects_out_of_range(void) {
  assert_value_error(validate_overwrite_arg(-1));
  assert_value_error(validate_overwrite_arg(4));
}

// ---- validate_compression_request ----

void test_request_checks_an_explicit_algo_range(void) {
  TEST_ASSERT_EQUAL_INT(
      0, validate_compression_request(ALGO_ZSTD, STRAT_BALANCED, 22, NULL));
  assert_value_error(
      validate_compression_request(ALGO_ZSTD, STRAT_BALANCED, 23, NULL));
}

void test_request_ignores_strategy_given_an_algo(void) {
  // 12 is valid for lz4 (the fast strategy's pick) but not for bzip2
  assert_value_error(
      validate_compression_request(ALGO_BZIP2, STRAT_FAST, 12, NULL));
}

void test_request_checks_the_strategy_backend_without_an_algo(void) {
  const CBackend *backend = choose_backend(STRAT_MAX_RATIO);
  TEST_ASSERT_NOT_NULL(backend);

  TEST_ASSERT_EQUAL_INT(
      0, validate_compression_request(ALGO_NONE, STRAT_MAX_RATIO,
                                      backend->levels.max, NULL));
  assert_value_error(validate_compression_request(
      ALGO_NONE, STRAT_MAX_RATIO, backend->levels.max + 1, NULL));
}

void test_request_rejects_an_invalid_pipeline(void) {
  CompressionPipeline zip_gz = {ARCHIVE_ZIP, FORMAT_GZIP, -1};
  assert_value_error(
      validate_compression_request(ALGO_NONE, STRAT_BALANCED, -1, &zip_gz));
}

void test_request_checks_a_pipeline_codec_stage(void) {
  CompressionPipeline tar_gz = {ARCHIVE_TAR, FORMAT_GZIP, -1};
  TEST_ASSERT_EQUAL_INT(
      0, validate_compression_request(ALGO_NONE, STRAT_BALANCED, 9, &tar_gz));
  assert_value_error(
      validate_compression_request(ALGO_NONE, STRAT_BALANCED, 10, &tar_gz));
}

void test_request_checks_a_container_without_a_codec(void) {
  CompressionPipeline zip = {ARCHIVE_ZIP, FORMAT_UNKNOWN, -1};
  CompressionPipeline tar = {ARCHIVE_TAR, FORMAT_UNKNOWN, -1};

  TEST_ASSERT_EQUAL_INT(
      0, validate_compression_request(ALGO_NONE, STRAT_BALANCED, 9, &zip));
  assert_value_error(
      validate_compression_request(ALGO_NONE, STRAT_BALANCED, 10, &zip));
  assert_value_error(
      validate_compression_request(ALGO_NONE, STRAT_BALANCED, 1, &tar));
}

void test_request_leaves_an_unsupported_container_to_the_caller(void) {
  // 7z has no backend, which create_archive reports by name
  CompressionPipeline sevenz = {ARCHIVE_7Z, FORMAT_UNKNOWN, -1};
  TEST_ASSERT_EQUAL_INT(
      0, validate_compression_request(ALGO_NONE, STRAT_BALANCED, 99, &sevenz));
}
