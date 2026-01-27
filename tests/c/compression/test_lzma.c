/**
 * test_lzma.c - Tests for LZMA compression backend
 */

#include "../unity.h"
#include "../../../src/compresso/csrc/common.h"
#include <string.h>
#include <stdlib.h>
#include <stdio.h>

// Forward declaration
const CBackend *get_lzma_backend(void);

// Helper function to read file into memory
static unsigned char* read_file(const char *filename, size_t *out_size) {
    FILE *f = fopen(filename, "rb");
    if (!f) return NULL;

    fseek(f, 0, SEEK_END);
    size_t size = ftell(f);
    fseek(f, 0, SEEK_SET);

    unsigned char *buffer = malloc(size);
    if (!buffer) {
        fclose(f);
        return NULL;
    }

    size_t read = fread(buffer, 1, size, f);
    fclose(f);

    if (read != size) {
        free(buffer);
        return NULL;
    }

    *out_size = size;
    return buffer;
}

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

    TEST_ASSERT_NOT_NULL(backend->compress_buffer);

    // Read test file
    size_t file_size = 0;
    unsigned char *file_data = read_file("../fixtures/alice29.txt", &file_size);
    TEST_ASSERT_NOT_NULL(file_data);
    TEST_ASSERT_GREATER_THAN(0, file_size);

    size_t output_capacity = file_size * 2;
    unsigned char *output = malloc(output_capacity);
    size_t output_size = 0;

    int result = backend->compress_buffer(
        file_data,
        file_size,
        output,
        &output_capacity,
        6,
        &output_size
    );

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

    // Read test file
    size_t original_size = 0;
    unsigned char *original = read_file("../fixtures/alice29.txt", &original_size);
    TEST_ASSERT_NOT_NULL(original);

    // Compress
    size_t compressed_capacity = original_size * 2;
    unsigned char *compressed = malloc(compressed_capacity);
    size_t compressed_size = 0;

    backend->compress_buffer(
        original,
        original_size,
        compressed,
        &compressed_capacity,
        6,
        &compressed_size
    );

    // Decompress
    unsigned char *decompressed = malloc(original_size);
    size_t decompressed_capacity = original_size;
    size_t decompressed_size = 0;

    int result = backend->decompress_buffer(
        compressed,
        compressed_size,
        decompressed,
        &decompressed_capacity,
        &decompressed_size
    );

    TEST_ASSERT_EQUAL_INT(0, result);
    TEST_ASSERT_EQUAL_size_t(original_size, decompressed_size);
    TEST_ASSERT_EQUAL_MEMORY(original, decompressed, original_size);

    free(original);
    free(compressed);
    free(decompressed);
}

void test_lzma_high_compression_ratio(void) {
    const CBackend *backend = get_lzma_backend();
    if (!backend->is_available()) {
        TEST_IGNORE_MESSAGE("LZMA not available");
    }

    // Create highly repetitive data that LZMA should compress well
    size_t input_size = 100000;
    unsigned char *data = malloc(input_size);
    memset(data, 'A', input_size);

    size_t output_capacity = input_size * 2;
    unsigned char *output = malloc(output_capacity);
    size_t output_size = 0;

    int result = backend->compress_buffer(
        data,
        input_size,
        output,
        &output_capacity,
        9,  // Maximum compression
        &output_size
    );

    TEST_ASSERT_EQUAL_INT(0, result);
    // LZMA should achieve excellent compression on repetitive data
    TEST_ASSERT_LESS_THAN(input_size / 50, output_size);

    free(data);
    free(output);
}
