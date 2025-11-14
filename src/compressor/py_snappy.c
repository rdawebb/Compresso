#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <snappy-c.h>
#include "common.h"

static int
snappy_is_available(void) {
    return 1; // snappy is always available if this code is compiled
}

static size_t
snappy_max_compressed_size(size_t input_size) {
    return snappy_max_compressed_length(input_size);
}


// ---- Buffer Compression/Decompression ----

static int
snappy_compress_buffer(const unsigned char *input, size_t input_size,
                       unsigned char *output, size_t *output_capacity,
                       int level, size_t *output_size)
{
    (void)level; // snappy does not use compression level

    size_t dest_len = *output_capacity;
    snappy_status status;

    Py_BEGIN_ALLOW_THREADS
    status = snappy_compress((const char *)input, input_size,
                             (char *)output, &dest_len);
    Py_END_ALLOW_THREADS

    if (status != SNAPPY_OK) {
        return -1; // compression failed
    }

    *output_size = dest_len;
    return 0; // success
}

static int
snappy_decompress_buffer(const unsigned char *input, size_t input_size,
                         unsigned char *output, size_t *output_capacity,
                         size_t *output_size)
{
    size_t dest_len = *output_capacity;
    snappy_status status;

    Py_BEGIN_ALLOW_THREADS
    status = snappy_uncompress((const char *)input, input_size,
                               (char *)output, &dest_len);
    Py_END_ALLOW_THREADS

    if (status != SNAPPY_OK) {
        return -1; // decompression failed
    }

    *output_size = dest_len;
    return 0; // success
}

// ---- Decompression Buffer Size Helper ----

size_t snappy_decompressed_size(const unsigned char *input, size_t input_size) {
    size_t result = 0;
    snappy_status status = snappy_uncompressed_length((const char *)input, input_size, &result);
    return (status == SNAPPY_OK) ? result : 0;
}

// ---- Backend Definition ----

static const CBackend snappy_backend = {
    .name = "snappy",
    .id = ALGO_SNAPPY,
    .is_available = snappy_is_available,
    .max_compressed_size = snappy_max_compressed_size,
    .compress_buffer = snappy_compress_buffer,
    .decompress_buffer = snappy_decompress_buffer,
    .compress_stream = NULL, // stream functions not implemented
    .decompress_stream = NULL,
};

const CBackend *get_snappy_backend(void) {
    return &snappy_backend;
}