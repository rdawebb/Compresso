#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <lzma.h>
#include "common.h"

static int
lzma_is_available(void) {
    return 1; // lzma is always available if this code is compiled
}

static size_t
lzma_max_compressed_size(size_t input_size) {
    return input_size + input_size / 3 + 128 * 1024; // safe upper bound
}

static uint32_t
lzma_level_to_preset(int level) {
    if (level < 0) level = 6; // default
    if (level > 9) level = 9;
    return (uint32_t)level | LZMA_PRESET_EXTREME;
}

static int
lzma_compress_buffer(const unsigned char *input, size_t input_size,
                     unsigned char *output, size_t *output_capacity,
                     int level, size_t *output_size)
{
    uint32_t preset = lzma_level_to_preset(level);
    lzma_ret ret;
    size_t output_pos = 0;

    Py_BEGIN_ALLOW_THREADS
    ret = lzma_easy_buffer_encode(
        preset,
        LZMA_CHECK_CRC64,
        NULL,
        input, input_size,
        output, &output_pos, *output_capacity
    );
    Py_END_ALLOW_THREADS

    if (ret != LZMA_OK) {
        return -1; // compression failed
    }

    *output_size = output_pos;
    return 0; // success
}

static int
lzma_decompress_buffer(const unsigned char *input, size_t input_size,
                       unsigned char *output, size_t *output_capacity,
                       size_t *output_size)
{
    lzma_ret ret;
    uint64_t memlimit = UINT64_MAX;
    uint32_t flags = 0;
    size_t input_pos = 0;
    size_t output_pos = 0;

    Py_BEGIN_ALLOW_THREADS
    ret = lzma_stream_buffer_decode(
        &memlimit,
        flags,
        NULL,
        input, &input_pos, input_size,
        output, &output_pos, *output_capacity
    );
    Py_END_ALLOW_THREADS

    if (ret != LZMA_OK) {
        return -1; // decompression failed
    }

    *output_size = output_pos;
    return 0; // success
}

static const CBackend lzma_backend = {
    .name = "lzma",
    .id = ALGO_LZMA,
    .is_available = lzma_is_available,
    .max_compressed_size = lzma_max_compressed_size,
    .compress_buffer = lzma_compress_buffer,
    .decompress_buffer = lzma_decompress_buffer,
};

const CBackend *get_lzma_backend(void) {
    return &lzma_backend;
}