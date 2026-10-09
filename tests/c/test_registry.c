// Makes Unity define TEST_RANGE; the runner generator expands each one
#define UNITY_SUPPORT_TEST_CASES

#include "common.h"
#include "unity.h"

static const struct {
  const char *name;
  uint8_t id;
} EXPECTED_BACKENDS[] = {
    {"zlib", ALGO_ZLIB}, {"bzip2", ALGO_BZIP2}, {"lzma", ALGO_LZMA},
    {"zstd", ALGO_ZSTD}, {"lz4", ALGO_LZ4},     {"snappy", ALGO_SNAPPY},
};

// The TEST_RANGEs over EXPECTED_BACKENDS below must span exactly its rows
_Static_assert(sizeof(EXPECTED_BACKENDS) / sizeof(EXPECTED_BACKENDS[0]) == 6,
               "update TEST_RANGE");

static const struct {
  const char *text;
  Strategy expected;
} STRATEGIES[] = {
    {"balanced", STRAT_BALANCED},
    {"fast", STRAT_FAST},
    {"max_ratio", STRAT_MAX_RATIO},
    {"", STRAT_BALANCED}, // What the frontend passes when no strategy is set
    {"fsat", STRAT_UNKNOWN},
    {"FAST", STRAT_UNKNOWN}, // Names are case-sensitive, as algorithms are
};

_Static_assert(sizeof(STRATEGIES) / sizeof(STRATEGIES[0]) == 6,
               "update TEST_RANGE");

void setUp(void) {}
void tearDown(void) {}

TEST_RANGE([ 0, 5, 1 ])
void test_find_backend_by_id(int index) {
  const CBackend *backend = find_backend_by_id(EXPECTED_BACKENDS[index].id);

  TEST_ASSERT_NOT_NULL_MESSAGE(backend, EXPECTED_BACKENDS[index].name);
  TEST_ASSERT_EQUAL_UINT8(EXPECTED_BACKENDS[index].id, backend->id);
  TEST_ASSERT_EQUAL_STRING(EXPECTED_BACKENDS[index].name, backend->name);
}

void test_find_backend_by_id_invalid(void) {
  TEST_ASSERT_NULL(find_backend_by_id(255));
}

TEST_RANGE([ 0, 5, 1 ])
void test_strategy_from_string(int index) {
  TEST_ASSERT_EQUAL_MESSAGE(STRATEGIES[index].expected,
                            strategy_from_string(STRATEGIES[index].text),
                            STRATEGIES[index].text);
}

void test_strategy_from_string_null_is_balanced(void) {
  TEST_ASSERT_EQUAL(STRAT_BALANCED, strategy_from_string(NULL));
}

TEST_RANGE([ 0, 5, 1 ])
void test_algo_from_string(int index) {
  TEST_ASSERT_EQUAL_MESSAGE(EXPECTED_BACKENDS[index].id,
                            algo_from_string(EXPECTED_BACKENDS[index].name),
                            EXPECTED_BACKENDS[index].name);
}

void test_algo_from_string_unknown(void) {
  TEST_ASSERT_EQUAL(ALGO_NONE, algo_from_string(NULL));
  TEST_ASSERT_EQUAL(ALGO_NONE, algo_from_string(""));
  TEST_ASSERT_EQUAL(ALGO_NONE, algo_from_string("ZLIB"));
  TEST_ASSERT_EQUAL(ALGO_NONE, algo_from_string("xz"));
}

void test_choose_backend(void) {
  TEST_ASSERT_EQUAL_UINT8(ALGO_ZSTD, choose_backend(STRAT_BALANCED)->id);
  TEST_ASSERT_EQUAL_UINT8(ALGO_LZ4, choose_backend(STRAT_FAST)->id);
  TEST_ASSERT_EQUAL_UINT8(ALGO_LZMA, choose_backend(STRAT_MAX_RATIO)->id);
  TEST_ASSERT_EQUAL_UINT8(ALGO_ZSTD, choose_backend(STRAT_UNKNOWN)->id);
}
