/**
 * test_lz4.c - Tests for LZ4 compression backend
 */

#include "../unity.h"
#include "../lib/backend_io.h"
#include <lz4hc.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>

// Forward declaration
const CBackend *get_lz4_backend(void);

void setUp(void) {
    // Initialize Python for thread-local state
    if (!Py_IsInitialized()) {
        Py_Initialize();
    }
}

void tearDown(void) {
    // Clean up code runs after each test
}

void test_lz4_backend_is_available(void) {
    const CBackend *backend = get_lz4_backend();
    TEST_ASSERT_NOT_NULL(backend);

    if (backend->is_available()) {
        TEST_ASSERT_TRUE(backend->is_available());
    } else {
        TEST_IGNORE_MESSAGE("LZ4 not available on this system");
    }
}

void test_lz4_backend_has_correct_id(void) {
    const CBackend *backend = get_lz4_backend();
    TEST_ASSERT_EQUAL_UINT8(ALGO_LZ4, backend->id);
}

void test_lz4_backend_has_correct_name(void) {
    const CBackend *backend = get_lz4_backend();
    TEST_ASSERT_EQUAL_STRING("lz4", backend->name);
}

void test_lz4_level_range_matches_the_library(void) {
    const CBackend *backend = get_lz4_backend();
    TEST_ASSERT_EQUAL_INT(0, backend->levels.min);
    TEST_ASSERT_EQUAL_INT(LZ4HC_CLEVEL_MAX, backend->levels.max);
}

void test_lz4_compress_file_basic(void) {
    const CBackend *backend = get_lz4_backend();
    if (!backend->is_available()) {
        TEST_IGNORE_MESSAGE("LZ4 not available");
    }

    size_t file_size = 0;
    unsigned char *file_data = read_fixture("../fixtures/alice29.txt", &file_size);
    TEST_ASSERT_NOT_NULL(file_data);
    TEST_ASSERT_GREATER_THAN(0, file_size);

    unsigned char *output = NULL;
    size_t output_size = 0;
    int result = stream_compress_bytes(backend, file_data, file_size, 1,
                                       &output, &output_size);

    TEST_ASSERT_EQUAL_INT(0, result);
    TEST_ASSERT_GREATER_THAN(0, output_size);

    free(file_data);
    free(output);
}

void test_lz4_file_round_trip(void) {
    const CBackend *backend = get_lz4_backend();
    if (!backend->is_available()) {
        TEST_IGNORE_MESSAGE("LZ4 not available");
    }

    size_t original_size = 0;
    unsigned char *original = read_fixture("../fixtures/alice29.txt", &original_size);
    TEST_ASSERT_NOT_NULL(original);

    assert_stream_round_trip(backend, original, original_size, 1);

    free(original);
}

void test_lz4_ultra_fast_mode(void) {
    const CBackend *backend = get_lz4_backend();
    if (!backend->is_available()) {
        TEST_IGNORE_MESSAGE("LZ4 not available");
    }

    size_t input_size = 100000;
    unsigned char *data = safe_malloc(input_size);
    for (size_t i = 0; i < input_size; i++) {
        data[i] = (unsigned char)((i / 100) % 26 + 'a');
    }

    unsigned char *output = NULL;
    size_t output_size = 0;
    // LZ4 with level 1 is extremely fast
    int result = stream_compress_bytes(backend, data, input_size, 1,
                                       &output, &output_size);

    TEST_ASSERT_EQUAL_INT(0, result);
    TEST_ASSERT_GREATER_THAN(0, output_size);

    free(data);
    free(output);
}

void test_lz4_small_data(void) {
    const CBackend *backend = get_lz4_backend();
    if (!backend->is_available()) {
        TEST_IGNORE_MESSAGE("LZ4 not available");
    }

    const char *input = "Small";
    unsigned char *output = NULL;
    size_t output_size = 0;

    int result = stream_compress_bytes(backend, (const unsigned char *)input,
                                       strlen(input), 1, &output, &output_size);

    TEST_ASSERT_EQUAL_INT(0, result);
    TEST_ASSERT_GREATER_THAN(0, output_size);

    free(output);
}

void test_lz4_binary_data_roundtrip(void) {
    const CBackend *backend = get_lz4_backend();
    if (!backend->is_available()) {
        TEST_IGNORE_MESSAGE("LZ4 not available");
    }

    size_t input_size = 10000;
    unsigned char *data = safe_malloc(input_size);
    for (size_t i = 0; i < input_size; i++) {
        data[i] = (unsigned char)(i % 256);
    }

    assert_stream_round_trip(backend, data, input_size, 1);

    free(data);
}
