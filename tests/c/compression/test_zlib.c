/**
 * test_zlib.c - Tests for zlib compression backend
 */

#include "../unity.h"
#include "../lib/backend_io.h"
#include <string.h>
#include <stdlib.h>
#include <stdio.h>

// Forward declaration
const CBackend *get_zlib_backend(void);

void setUp(void) {
    // Initialize Python for thread-local state
    if (!Py_IsInitialized()) {
        Py_Initialize();
    }
}

void tearDown(void) {
    // Clean up code runs after each test
}

void test_zlib_backend_is_available(void) {
    const CBackend *backend = get_zlib_backend();
    TEST_ASSERT_NOT_NULL(backend);
    TEST_ASSERT_TRUE(backend->is_available());
}

void test_zlib_backend_has_correct_id(void) {
    const CBackend *backend = get_zlib_backend();
    TEST_ASSERT_EQUAL_UINT8(ALGO_ZLIB, backend->id);
}

void test_zlib_backend_has_correct_name(void) {
    const CBackend *backend = get_zlib_backend();
    TEST_ASSERT_EQUAL_STRING("zlib", backend->name);
}

void test_zlib_compress_small_file(void) {
    const CBackend *backend = get_zlib_backend();

    size_t file_size = 0;
    unsigned char *file_data = read_fixture("../fixtures/xargs.1", &file_size);
    TEST_ASSERT_NOT_NULL(file_data);
    TEST_ASSERT_GREATER_THAN(0, file_size);

    unsigned char *output = NULL;
    size_t output_size = 0;
    int result = stream_compress_bytes(backend, file_data, file_size, 6,
                                       &output, &output_size);

    TEST_ASSERT_EQUAL_INT(0, result);
    TEST_ASSERT_GREATER_THAN(0, output_size);

    free(file_data);
    free(output);
}

void test_zlib_decompress_file_roundtrip(void) {
    const CBackend *backend = get_zlib_backend();

    size_t original_size = 0;
    unsigned char *original = read_fixture("../fixtures/alice29.txt", &original_size);
    TEST_ASSERT_NOT_NULL(original);

    assert_stream_round_trip(backend, original, original_size, 6);

    free(original);
}

void test_zlib_round_trip_large_file(void) {
    const CBackend *backend = get_zlib_backend();

    // Create large test data
    size_t input_size = 100000;
    unsigned char *data = safe_malloc(input_size);
    TEST_ASSERT_NOT_NULL(data);

    // Fill with pattern
    const char *pattern = "The quick brown fox jumps over the lazy dog. ";
    size_t pattern_len = strlen(pattern);
    for (size_t i = 0; i < input_size; i++) {
        data[i] = pattern[i % pattern_len];
    }

    assert_stream_round_trip(backend, data, input_size, 6);

    free(data);
}

void test_zlib_different_compression_levels(void) {
    const CBackend *backend = get_zlib_backend();

    size_t file_size = 0;
    unsigned char *file_data = read_fixture("../fixtures/alice29.txt", &file_size);
    TEST_ASSERT_NOT_NULL(file_data);

    unsigned char *fast = NULL, *best = NULL;
    size_t size_level1 = 0, size_level9 = 0;

    TEST_ASSERT_EQUAL_INT(0, stream_compress_bytes(backend, file_data, file_size,
                                                   1, &fast, &size_level1));
    TEST_ASSERT_EQUAL_INT(0, stream_compress_bytes(backend, file_data, file_size,
                                                   9, &best, &size_level9));

    TEST_ASSERT_GREATER_THAN(0, size_level9);
    TEST_ASSERT_LESS_OR_EQUAL(size_level1, size_level9);

    free(file_data);
    free(fast);
    free(best);
}

void test_zlib_empty_input(void) {
    const CBackend *backend = get_zlib_backend();

    // zlib should handle empty input gracefully
    assert_stream_round_trip(backend, (const unsigned char *)"", 0, 6);
}
