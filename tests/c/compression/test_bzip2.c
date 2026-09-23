/**
 * test_bzip2.c - Tests for bzip2 compression backend
 */

#include "../unity.h"
#include "../lib/backend_io.h"
#include <string.h>
#include <stdlib.h>
#include <stdio.h>

// Forward declaration
const CBackend *get_bzip2_backend(void);

void setUp(void) {
    // Initialize Python for thread-local state
    if (!Py_IsInitialized()) {
        Py_Initialize();
    }
}

void tearDown(void) {
    // Clean up code runs after each test
}

void test_bzip2_backend_is_available(void) {
    const CBackend *backend = get_bzip2_backend();
    TEST_ASSERT_NOT_NULL(backend);

    if (backend->is_available()) {
        TEST_ASSERT_TRUE(backend->is_available());
    } else {
        TEST_IGNORE_MESSAGE("bzip2 not available on this system");
    }
}

void test_bzip2_backend_has_correct_id(void) {
    const CBackend *backend = get_bzip2_backend();
    TEST_ASSERT_EQUAL_UINT8(ALGO_BZIP2, backend->id);
}

void test_bzip2_backend_has_correct_name(void) {
    const CBackend *backend = get_bzip2_backend();
    TEST_ASSERT_EQUAL_STRING("bzip2", backend->name);
}

void test_bzip2_compress_file_basic(void) {
    const CBackend *backend = get_bzip2_backend();
    if (!backend->is_available()) {
        TEST_IGNORE_MESSAGE("bzip2 not available");
    }

    size_t file_size = 0;
    unsigned char *file_data = read_fixture("../fixtures/alice29.txt", &file_size);
    TEST_ASSERT_NOT_NULL(file_data);
    TEST_ASSERT_GREATER_THAN(0, file_size);

    unsigned char *output = NULL;
    size_t output_size = 0;
    int result = stream_compress_bytes(backend, file_data, file_size, 9,
                                       &output, &output_size);

    TEST_ASSERT_EQUAL_INT(0, result);
    TEST_ASSERT_GREATER_THAN(0, output_size);

    free(file_data);
    free(output);
}

void test_bzip2_file_round_trip(void) {
    const CBackend *backend = get_bzip2_backend();
    if (!backend->is_available()) {
        TEST_IGNORE_MESSAGE("bzip2 not available");
    }

    size_t original_size = 0;
    unsigned char *original = read_fixture("../fixtures/alice29.txt", &original_size);
    TEST_ASSERT_NOT_NULL(original);

    assert_stream_round_trip(backend, original, original_size, 9);

    free(original);
}

void test_bzip2_large_file_compression(void) {
    const CBackend *backend = get_bzip2_backend();
    if (!backend->is_available()) {
        TEST_IGNORE_MESSAGE("bzip2 not available");
    }

    size_t input_size = 100000;
    unsigned char *data = safe_malloc(input_size);
    const char *pattern = "Repeating data pattern for large file test. ";
    size_t pattern_len = strlen(pattern);
    for (size_t i = 0; i < input_size; i++) {
        data[i] = pattern[i % pattern_len];
    }

    unsigned char *output = NULL;
    size_t output_size = 0;
    int result = stream_compress_bytes(backend, data, input_size, 9,
                                       &output, &output_size);

    TEST_ASSERT_EQUAL_INT(0, result);
    TEST_ASSERT_GREATER_THAN(0, output_size);
    // bzip2 should compress repetitive data well
    TEST_ASSERT_LESS_THAN(input_size / 2, output_size);

    free(data);
    free(output);
}
