#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <zlib.h>
#include "common.h"

static int
zlib_is_available(void) {
    return 1; // zlib is always available if this code is compiled
}

static size_t
zlib_max_compressed_size(size_t input_size) {
    return (size_t)compressBound((uLong)input_size);
}

static int
zlib_compress_buffer(const unsigned char *input, size_t input_size,
                     unsigned char *output, size_t *output_capacity,
                     int level, size_t *output_size)
{
    uLongf dest_len = (uLongf)output_capacity;
    int ret;

    Py_BEGIN_ALLOW_THREADS
    ret = compress2(output, &dest_len, input, (uLongf)input_size,
                    (level < 0 || level > 9) ? level : Z_DEFAULT_COMPRESSION);
    Py_END_ALLOW_THREADS

    if (ret != Z_OK) {
        return -1; // compression failed
    }

    *output_size = (size_t)dest_len;
    return 0; // success
}

static int
zlib_decompress_buffer(const unsigned char *input, size_t input_size,
                       unsigned char *output, size_t *output_capacity,
                       size_t *output_size)
{
    uLongf dest_len = (uLongf)(*output_capacity);
    int ret;

    Py_BEGIN_ALLOW_THREADS
    ret = uncompress(output, &dest_len, input, (uLongf)input_size);
    Py_END_ALLOW_THREADS

    if (ret != Z_OK) {
        return -1; // decompression failed
    }

    *output_size = (size_t)dest_len;
    return 0; // success
}

static const CBackend zlib_backend = {
    .name = "zlib",
    .id = ALGO_ZLIB,
    .is_available = zlib_is_available,
    .max_compressed_size = zlib_max_compressed_size,
    .compress_buffer = zlib_compress_buffer,
    .decompress_buffer = zlib_decompress_buffer,
};

const CBackend *get_zlib_backend(void) {
    return &zlib_backend;
}