/**
 * test_zlib.c - Tests for zlib compression backend
 */

#include "../unity.h"
#include "../../../src/compresso/csrc/common.h"
#include <string.h>
#include <stdlib.h>
#include <stdio.h>

// Forward declaration
const CBackend *get_zlib_backend(void);

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
    TEST_ASSERT_NOT_NULL(backend->compress_buffer);

    // Read test file
    size_t file_size = 0;
    unsigned char *file_data = read_file("../fixtures/xargs.1", &file_size);
    TEST_ASSERT_NOT_NULL(file_data);
    TEST_ASSERT_GREATER_THAN(0, file_size);

    // Compress
    size_t output_capacity = file_size * 2;
    unsigned char *compressed = malloc(output_capacity);
    size_t compressed_size = 0;

    int result = backend->compress_buffer(
        file_data,
        file_size,
        compressed,
        &output_capacity,
        6,  // compression level
        &compressed_size
    );

    TEST_ASSERT_EQUAL_INT(0, result);
    TEST_ASSERT_GREATER_THAN(0, compressed_size);

    free(file_data);
    free(compressed);
}

void test_zlib_decompress_file_roundtrip(void) {
    const CBackend *backend = get_zlib_backend();
    TEST_ASSERT_NOT_NULL(backend->decompress_buffer);

    // Read test file
    size_t original_size = 0;
    unsigned char *original = read_file("../fixtures/alice29.txt", &original_size);
    TEST_ASSERT_NOT_NULL(original);
    TEST_ASSERT_GREATER_THAN(0, original_size);

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
    size_t decompressed_capacity = original_size;
    unsigned char *decompressed = malloc(decompressed_capacity);
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

void test_zlib_round_trip_large_file(void) {
    const CBackend *backend = get_zlib_backend();

    // Create large test data
    size_t data_size = 100000;
    unsigned char *original = malloc(data_size);
    TEST_ASSERT_NOT_NULL(original);

    // Fill with pattern
    const char *pattern = "The quick brown fox jumps over the lazy dog. ";
    size_t pattern_len = strlen(pattern);
    for (size_t i = 0; i < data_size; i++) {
        original[i] = pattern[i % pattern_len];
    }

    // Compress
    size_t compressed_capacity = data_size * 2;
    unsigned char *compressed = malloc(compressed_capacity);
    size_t compressed_size = 0;

    backend->compress_buffer(
        original,
        data_size,
        compressed,
        &compressed_capacity,
        6,
        &compressed_size
    );

    // Decompress
    size_t decompressed_capacity = data_size;
    unsigned char *decompressed = malloc(decompressed_capacity);
    size_t decompressed_size = 0;

    backend->decompress_buffer(
        compressed,
        compressed_size,
        decompressed,
        &decompressed_capacity,
        &decompressed_size
    );

    TEST_ASSERT_EQUAL_size_t(data_size, decompressed_size);
    TEST_ASSERT_EQUAL_MEMORY(original, decompressed, data_size);

    free(original);
    free(compressed);
    free(decompressed);
}

void test_zlib_different_compression_levels(void) {
    const CBackend *backend = get_zlib_backend();

    size_t file_size = 0;
    unsigned char *file_data = read_file("../fixtures/alice29.txt", &file_size);
    TEST_ASSERT_NOT_NULL(file_data);

    size_t output_capacity = file_size * 2;
    unsigned char *output = malloc(output_capacity);

    // Test level 1 (fast)
    size_t cap1 = output_capacity;
    size_t size_level1 = 0;
    backend->compress_buffer(file_data, file_size, output, &cap1, 1, &size_level1);

    // Test level 9 (best)
    size_t cap9 = output_capacity;
    size_t size_level9 = 0;
    backend->compress_buffer(file_data, file_size, output, &cap9, 9, &size_level9);

    // Both should succeed
    TEST_ASSERT_GREATER_THAN(0, size_level1);
    TEST_ASSERT_GREATER_THAN(0, size_level9);

    free(file_data);
    free(output);
}

void test_zlib_empty_input(void) {
    const CBackend *backend = get_zlib_backend();

    unsigned char output[100];
    size_t output_capacity = sizeof(output);
    size_t output_size = 0;

    int result = backend->compress_buffer(
        (const unsigned char *)"",
        0,
        output,
        &output_capacity,
        6,
        &output_size
    );

    // zlib should handle empty input gracefully
    TEST_ASSERT_EQUAL_INT(0, result);
}
