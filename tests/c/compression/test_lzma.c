/**
 * test_lzma.c - Tests for LZMA compression backend
 */

#include "../unity.h"
#include "../lib/backend_io.h"
#include <string.h>
#include <stdlib.h>
#include <stdio.h>

// Forward declaration
const CBackend *get_lzma_backend(void);

void setUp(void) {
    // Initialize Python for thread-local state
    if (!Py_IsInitialized()) {
        Py_Initialize();
    }
}

void tearDown(void) {
    // Clean up code runs after each test
}

void test_lzma_backend_is_available(void) {
    const CBackend *backend = get_lzma_backend();
    TEST_ASSERT_NOT_NULL(backend);

    if (backend->is_available()) {
        TEST_ASSERT_TRUE(backend->is_available());
    } else {
        TEST_IGNORE_MESSAGE("LZMA not available on this system");
    }
}

void test_lzma_backend_has_correct_id(void) {
    const CBackend *backend = get_lzma_backend();
    TEST_ASSERT_EQUAL_UINT8(ALGO_LZMA, backend->id);
}

void test_lzma_backend_has_correct_name(void) {
    const CBackend *backend = get_lzma_backend();
    TEST_ASSERT_EQUAL_STRING("lzma", backend->name);
}

void test_lzma_compress_file_basic(void) {
    const CBackend *backend = get_lzma_backend();
    if (!backend->is_available()) {
        TEST_IGNORE_MESSAGE("LZMA not available");
    }

    size_t file_size = 0;
    unsigned char *file_data = read_fixture("../fixtures/alice29.txt", &file_size);
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

void test_lzma_file_round_trip(void) {
    const CBackend *backend = get_lzma_backend();
    if (!backend->is_available()) {
        TEST_IGNORE_MESSAGE("LZMA not available");
    }

    size_t original_size = 0;
    unsigned char *original = read_fixture("../fixtures/alice29.txt", &original_size);
    TEST_ASSERT_NOT_NULL(original);

    assert_stream_round_trip(backend, original, original_size, 6);

    free(original);
}

void test_lzma_high_compression_ratio(void) {
    const CBackend *backend = get_lzma_backend();
    if (!backend->is_available()) {
        TEST_IGNORE_MESSAGE("LZMA not available");
    }

    size_t input_size = 100000;
    unsigned char *data = safe_malloc(input_size);
    memset(data, 'A', input_size);

    unsigned char *output = NULL;
    size_t output_size = 0;
    // Maximum compression
    int result = stream_compress_bytes(backend, data, input_size, 9,
                                       &output, &output_size);

    TEST_ASSERT_EQUAL_INT(0, result);
    // LZMA should achieve excellent compression on repetitive data
    TEST_ASSERT_LESS_THAN(input_size / 50, output_size);

    free(data);
    free(output);
}
