/**
 * test_zstd.c - Tests for Zstandard compression backend
 */

#include "../unity.h"
#include "../lib/backend_io.h"
#include <string.h>
#include <stdlib.h>
#include <stdio.h>

// Forward declaration
const CBackend *get_zstd_backend(void);

void setUp(void) {
    // Initialize Python for thread-local state
    if (!Py_IsInitialized()) {
        Py_Initialize();
    }
}

void tearDown(void) {
    // Clean up code runs after each test
}

void test_zstd_backend_is_available(void) {
    const CBackend *backend = get_zstd_backend();
    TEST_ASSERT_NOT_NULL(backend);

    if (backend->is_available()) {
        TEST_ASSERT_TRUE(backend->is_available());
    } else {
        TEST_IGNORE_MESSAGE("Zstandard not available on this system");
    }
}

void test_zstd_backend_has_correct_id(void) {
    const CBackend *backend = get_zstd_backend();
    TEST_ASSERT_EQUAL_UINT8(ALGO_ZSTD, backend->id);
}

void test_zstd_backend_has_correct_name(void) {
    const CBackend *backend = get_zstd_backend();
    TEST_ASSERT_EQUAL_STRING("zstd", backend->name);
}

void test_zstd_compress_file_basic(void) {
    const CBackend *backend = get_zstd_backend();
    if (!backend->is_available()) {
        TEST_IGNORE_MESSAGE("zstd not available");
    }

    size_t file_size = 0;
    unsigned char *file_data = read_fixture("../fixtures/alice29.txt", &file_size);
    TEST_ASSERT_NOT_NULL(file_data);
    TEST_ASSERT_GREATER_THAN(0, file_size);

    unsigned char *output = NULL;
    size_t output_size = 0;
    int result = stream_compress_bytes(backend, file_data, file_size, 3,
                                       &output, &output_size);

    TEST_ASSERT_EQUAL_INT(0, result);
    TEST_ASSERT_GREATER_THAN(0, output_size);

    free(file_data);
    free(output);
}

void test_zstd_file_round_trip(void) {
    const CBackend *backend = get_zstd_backend();
    if (!backend->is_available()) {
        TEST_IGNORE_MESSAGE("zstd not available");
    }

    size_t original_size = 0;
    unsigned char *original = read_fixture("../fixtures/alice29.txt", &original_size);
    TEST_ASSERT_NOT_NULL(original);

    assert_stream_round_trip(backend, original, original_size, 3);

    free(original);
}

void test_zstd_fast_compression(void) {
    const CBackend *backend = get_zstd_backend();
    if (!backend->is_available()) {
        TEST_IGNORE_MESSAGE("zstd not available");
    }

    size_t input_size = 5000;
    unsigned char *data = safe_malloc(input_size);
    for (size_t i = 0; i < input_size; i++) {
        data[i] = (unsigned char)(i % 256);
    }

    unsigned char *output = NULL;
    size_t output_size = 0;
    // Level 1 is very fast
    int result = stream_compress_bytes(backend, data, input_size, 1,
                                       &output, &output_size);

    TEST_ASSERT_EQUAL_INT(0, result);
    TEST_ASSERT_GREATER_THAN(0, output_size);

    free(data);
    free(output);
}

void test_zstd_compression_levels(void) {
    const CBackend *backend = get_zstd_backend();
    if (!backend->is_available()) {
        TEST_IGNORE_MESSAGE("zstd not available");
    }

    // Create compressible data
    size_t input_size = 10000;
    unsigned char *data = safe_malloc(input_size);
    memset(data, 'B', input_size);

    unsigned char *fast = NULL, *best = NULL;
    size_t size_level1 = 0, size_level19 = 0;

    TEST_ASSERT_EQUAL_INT(0, stream_compress_bytes(backend, data, input_size, 1,
                                                   &fast, &size_level1));
    TEST_ASSERT_EQUAL_INT(0, stream_compress_bytes(backend, data, input_size, 19,
                                                   &best, &size_level19));

    TEST_ASSERT_GREATER_THAN(0, size_level1);
    TEST_ASSERT_GREATER_THAN(0, size_level19);

    free(data);
    free(fast);
    free(best);
}

void test_zstd_binary_data_roundtrip(void) {
    const CBackend *backend = get_zstd_backend();
    if (!backend->is_available()) {
        TEST_IGNORE_MESSAGE("zstd not available");
    }

    size_t input_size = 5000;
    unsigned char *data = safe_malloc(input_size);
    for (size_t i = 0; i < input_size; i++) {
        data[i] = (unsigned char)(i * 137 % 256);  // Pseudo-random
    }

    assert_stream_round_trip(backend, data, input_size, 5);

    free(data);
}
