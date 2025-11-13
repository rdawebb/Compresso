#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <bzlib.h>
#include "common.h"

static int
bzip2_is_available(void) {
    return 1; // bzip2 is always available if this code is compiled
}

static size_t
bzip2_max_compressed_size(size_t input_size) {
    return input_size + (input_size / 100) + 600; // per bzip2 documentation
}

static int
bzip2_compress_buffer(const unsigned char *input, size_t input_size,
                      unsigned char *output, size_t *output_capacity,
                      int level, size_t *output_size)
{
    int blockSize100k;
    if (level <= 0) blockSize100k = 9; // default: max compression
    else if (level > 9) blockSize100k = 9;
    else blockSize100k = level;

    unsigned int dest_len = (unsigned int)output_capacity;
    int ret;

    Py_BEGIN_ALLOW_THREADS
    ret = BZ2_bzBuffToBuffCompress(
        (char *) output, &dest_len, (char *) input, (unsigned int) input_size,
        blockSize100k, 
        0, 30 // verbosity and workFactor (recommended default)
    );
    Py_END_ALLOW_THREADS

    if (ret != BZ_OK) {
        return -1; // compression failed
    }

    *output_size = (size_t)dest_len;
    return 0; // success
}

static int
bzip2_decompress_buffer(const unsigned char *input, size_t input_size,
                        unsigned char *output, size_t *output_capacity,
                        size_t *output_size)
{
    unsigned int dest_len = (unsigned int)output_capacity;
    int ret;

    Py_BEGIN_ALLOW_THREADS
    ret = BZ2_bzBuffToBuffDecompress(
        (char *) output, &dest_len, (char *) input, (unsigned int) input_size,
        0, 0 // small and verbosity flags
    );
    Py_END_ALLOW_THREADS

    if (ret != BZ_OK) {
        return -1; // decompression failed
    }

    *output_size = (size_t)dest_len;
    return 0; // success
}

static const CBackend bzip2_backend = {
    .name = "bzip2",
    .id = ALGO_BZIP2,
    .is_available = bzip2_is_available,
    .max_compressed_size = bzip2_max_compressed_size,
    .compress_buffer = bzip2_compress_buffer,
    .decompress_buffer = bzip2_decompress_buffer,
};

const CBackend *get_bzip2_backend(void) {
    return &bzip2_backend;
}