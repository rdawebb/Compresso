#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <lz4.h>
#include "common.h"

static int
lz4_is_available(void) {
    return 1; // lz4 is always available if this code is compiled
}

static size_t
lz4_max_compressed_size(size_t input_size) {
    return (size_t)LZ4_compressBound((int)input_size);
}


// ---- Buffer Compression/Decompression ----

static int
lz4_compress_buffer(const unsigned char *input, size_t input_size,
                    unsigned char *output, size_t *output_capacity,
                    int level, size_t *output_size)
{
    (void)level; // lz4 does not use compression level

    int ret;
    Py_BEGIN_ALLOW_THREADS
    ret = LZ4_compress_default((const char *)input, (char *)output,
                               (int)input_size, (int)(*output_capacity));
    Py_END_ALLOW_THREADS

    if (ret <= 0) {
        return -1; // compression failed
    }

    *output_size = (size_t)ret;
    return 0; // success
}

static int
lz4_decompress_buffer(const unsigned char *input, size_t input_size,
                      unsigned char *output, size_t *output_capacity,
                      size_t *output_size)
{
    int ret;
    Py_BEGIN_ALLOW_THREADS
    ret = LZ4_decompress_safe((const char *)input, (char *)output,
                              (int)input_size, (int)(*output_capacity));
    Py_END_ALLOW_THREADS

    if (ret < 0) {
        return -1; // decompression failed
    }

    *output_size = (size_t)ret;
    return 0; // success
}


// ---- Backend Definition ----

static const CBackend lz4_backend = {
    .name = "lz4",
    .id = ALGO_LZ4,
    .is_available = lz4_is_available,
    .max_compressed_size = lz4_max_compressed_size,
    .compress_buffer = lz4_compress_buffer,
    .decompress_buffer = lz4_decompress_buffer,
    .compress_stream = NULL, // stream functions not implemented
    .decompress_stream = NULL,
};

const CBackend *get_lz4_backend(void) {
    return &lz4_backend;
}