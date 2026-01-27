#include "unity.h"
#include "../../src/compresso/csrc/common.h"
#include <stdlib.h>
#include <string.h>

// Forward declarations
const CBackend *find_backend_by_name(const char *name);
const CBackend *find_backend_by_id(uint8_t id);

void setUp(void) {
}

void tearDown(void) {
}

void test_find_backend_by_name_zlib(void) {
    const CBackend *backend = find_backend_by_name("zlib");
    TEST_ASSERT_NOT_NULL(backend);
    TEST_ASSERT_EQUAL_STRING("zlib", backend->name);
    TEST_ASSERT_EQUAL(ALGO_ZLIB, backend->id);
}

void test_find_backend_by_name_bzip2(void) {
    const CBackend *backend = find_backend_by_name("bzip2");
    TEST_ASSERT_NOT_NULL(backend);
    TEST_ASSERT_EQUAL_STRING("bzip2", backend->name);
    TEST_ASSERT_EQUAL(ALGO_BZIP2, backend->id);
}

void test_find_backend_by_name_lzma(void) {
    const CBackend *backend = find_backend_by_name("lzma");
    TEST_ASSERT_NOT_NULL(backend);
    TEST_ASSERT_EQUAL_STRING("lzma", backend->name);
    TEST_ASSERT_EQUAL(ALGO_LZMA, backend->id);
}

void test_find_backend_by_name_zstd(void) {
    const CBackend *backend = find_backend_by_name("zstd");
    TEST_ASSERT_NOT_NULL(backend);
    TEST_ASSERT_EQUAL_STRING("zstd", backend->name);
    TEST_ASSERT_EQUAL(ALGO_ZSTD, backend->id);
}

void test_find_backend_by_name_lz4(void) {
    const CBackend *backend = find_backend_by_name("lz4");
    TEST_ASSERT_NOT_NULL(backend);
    TEST_ASSERT_EQUAL_STRING("lz4", backend->name);
    TEST_ASSERT_EQUAL(ALGO_LZ4, backend->id);
}

void test_find_backend_by_name_snappy(void) {
    const CBackend *backend = find_backend_by_name("snappy");
    TEST_ASSERT_NOT_NULL(backend);
    TEST_ASSERT_EQUAL_STRING("snappy", backend->name);
    TEST_ASSERT_EQUAL(ALGO_SNAPPY, backend->id);
}

void test_find_backend_by_name_invalid(void) {
    const CBackend *backend = find_backend_by_name("invalid_backend");
    TEST_ASSERT_NULL(backend);
}

void test_find_backend_by_name_null(void) {
    const CBackend *backend = find_backend_by_name(NULL);
    TEST_ASSERT_NULL(backend);
}

void test_find_backend_by_id_zlib(void) {
    const CBackend *backend = find_backend_by_id(ALGO_ZLIB);
    TEST_ASSERT_NOT_NULL(backend);
    TEST_ASSERT_EQUAL(ALGO_ZLIB, backend->id);
}

void test_find_backend_by_id_bzip2(void) {
    const CBackend *backend = find_backend_by_id(ALGO_BZIP2);
    TEST_ASSERT_NOT_NULL(backend);
    TEST_ASSERT_EQUAL(ALGO_BZIP2, backend->id);
}

void test_find_backend_by_id_lzma(void) {
    const CBackend *backend = find_backend_by_id(ALGO_LZMA);
    TEST_ASSERT_NOT_NULL(backend);
    TEST_ASSERT_EQUAL(ALGO_LZMA, backend->id);
}

void test_find_backend_by_id_zstd(void) {
    const CBackend *backend = find_backend_by_id(ALGO_ZSTD);
    TEST_ASSERT_NOT_NULL(backend);
    TEST_ASSERT_EQUAL(ALGO_ZSTD, backend->id);
}

void test_find_backend_by_id_lz4(void) {
    const CBackend *backend = find_backend_by_id(ALGO_LZ4);
    TEST_ASSERT_NOT_NULL(backend);
    TEST_ASSERT_EQUAL(ALGO_LZ4, backend->id);
}

void test_find_backend_by_id_snappy(void) {
    const CBackend *backend = find_backend_by_id(ALGO_SNAPPY);
    TEST_ASSERT_NOT_NULL(backend);
    TEST_ASSERT_EQUAL(ALGO_SNAPPY, backend->id);
}

void test_find_backend_by_id_invalid(void) {
    const CBackend *backend = find_backend_by_id(255);
    TEST_ASSERT_NULL(backend);
}

void test_backend_availability(void) {
    // Test that at least zlib is available (it should always be)
    const CBackend *backend = find_backend_by_name("zlib");
    TEST_ASSERT_NOT_NULL(backend);

    if (backend->is_available) {
        int available = backend->is_available();
        TEST_ASSERT_TRUE(available);
    }
}

void test_backend_max_compressed_size(void) {
    const CBackend *backend = find_backend_by_name("zlib");
    TEST_ASSERT_NOT_NULL(backend);

    if (backend->max_compressed_size) {
        size_t input_size = 1000;
        size_t max_size = backend->max_compressed_size(input_size);
        TEST_ASSERT_GREATER_THAN(input_size, max_size);
    }
}

void test_strategy_from_string_balanced(void) {
    Strategy strat = strategy_from_string("balanced");
    TEST_ASSERT_EQUAL(STRAT_BALANCED, strat);
}

void test_strategy_from_string_fast(void) {
    Strategy strat = strategy_from_string("fast");
    TEST_ASSERT_EQUAL(STRAT_FAST, strat);
}

void test_strategy_from_string_max_ratio(void) {
    Strategy strat = strategy_from_string("max_ratio");
    TEST_ASSERT_EQUAL(STRAT_MAX_RATIO, strat);
}

void test_strategy_from_string_default(void) {
    Strategy strat = strategy_from_string("unknown");
    TEST_ASSERT_EQUAL(STRAT_BALANCED, strat);
}
