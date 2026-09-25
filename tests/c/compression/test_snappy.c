/**
 * test_snappy.c - Tests for Snappy compression backend
 */

#include "../unity.h"
#include "../lib/backend_io.h"
#include <string.h>
#include <stdlib.h>
#include <stdio.h>

// Forward declaration
const CBackend *get_snappy_backend(void);

void setUp(void) {
    // Initialize Python for thread-local state
    if (!Py_IsInitialized()) {
        Py_Initialize();
    }
}

void tearDown(void) {
    // Clean up code runs after each test
}

void test_snappy_backend_is_available(void) {
    const CBackend *backend = get_snappy_backend();
    TEST_ASSERT_NOT_NULL(backend);

    if (backend->is_available()) {
        TEST_ASSERT_TRUE(backend->is_available());
    } else {
        TEST_IGNORE_MESSAGE("Snappy not available on this system");
    }
}

void test_snappy_backend_has_correct_id(void) {
    const CBackend *backend = get_snappy_backend();
    TEST_ASSERT_EQUAL_UINT8(ALGO_SNAPPY, backend->id);
}

void test_snappy_backend_has_correct_name(void) {
    const CBackend *backend = get_snappy_backend();
    TEST_ASSERT_EQUAL_STRING("snappy", backend->name);
}

void test_snappy_compress_file_basic(void) {
    const CBackend *backend = get_snappy_backend();
    if (!backend->is_available()) {
        TEST_IGNORE_MESSAGE("Snappy not available");
    }

    size_t file_size = 0;
    unsigned char *file_data = read_fixture("../fixtures/alice29.txt", &file_size);
    TEST_ASSERT_NOT_NULL(file_data);
    TEST_ASSERT_GREATER_THAN(0, file_size);

    unsigned char *output = NULL;
    size_t output_size = 0;
    int result = stream_compress_bytes(backend, file_data, file_size, -1,
                                       &output, &output_size);

    TEST_ASSERT_EQUAL_INT(0, result);
    TEST_ASSERT_GREATER_THAN(0, output_size);

    free(file_data);
    free(output);
}

void test_snappy_file_round_trip(void) {
    const CBackend *backend = get_snappy_backend();
    if (!backend->is_available()) {
        TEST_IGNORE_MESSAGE("Snappy not available");
    }

    size_t original_size = 0;
    unsigned char *original = read_fixture("../fixtures/alice29.txt", &original_size);
    TEST_ASSERT_NOT_NULL(original);

    assert_stream_round_trip(backend, original, original_size, -1);

    free(original);
}

void test_snappy_high_speed(void) {
    const CBackend *backend = get_snappy_backend();
    if (!backend->is_available()) {
        TEST_IGNORE_MESSAGE("Snappy not available");
    }

    size_t input_size = 100000;
    unsigned char *data = safe_malloc(input_size);
    for (size_t i = 0; i < input_size; i++) {
        data[i] = (unsigned char)((i / 50) % 26 + 'A');
    }

    unsigned char *output = NULL;
    size_t output_size = 0;
    int result = stream_compress_bytes(backend, data, input_size, -1,
                                       &output, &output_size);

    TEST_ASSERT_EQUAL_INT(0, result);
    TEST_ASSERT_GREATER_THAN(0, output_size);

    free(data);
    free(output);
}

void test_snappy_binary_data_roundtrip(void) {
    const CBackend *backend = get_snappy_backend();
    if (!backend->is_available()) {
        TEST_IGNORE_MESSAGE("Snappy not available");
    }

    size_t input_size = 10000;
    unsigned char *data = safe_malloc(input_size);
    for (size_t i = 0; i < input_size; i++) {
        data[i] = (unsigned char)(i * 7 % 256);
    }

    assert_stream_round_trip(backend, data, input_size, -1);

    free(data);
}

void test_snappy_repetitive_data(void) {
    const CBackend *backend = get_snappy_backend();
    if (!backend->is_available()) {
        TEST_IGNORE_MESSAGE("Snappy not available");
    }

    size_t input_size = 50000;
    unsigned char *data = safe_malloc(input_size);
    memset(data, 'X', input_size);

    unsigned char *output = NULL;
    size_t output_size = 0;
    int result = stream_compress_bytes(backend, data, input_size, -1,
                                       &output, &output_size);

    TEST_ASSERT_EQUAL_INT(0, result);
    // Snappy should still achieve some compression on repetitive data
    TEST_ASSERT_LESS_THAN(input_size / 2, output_size);

    free(data);
    free(output);
}
