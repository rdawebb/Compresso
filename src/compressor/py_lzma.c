#define PY_SSIZE_T_CLEAN
#define LZMA_CHUNK 65536 // 64KB
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


// ---- Buffer Compression/Decompression ----

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


// ---- Stream Compression/Decompression ----

static int
lzma_compress_stream(FILE *src, FILE *dst, int level)
{
    uint32_t preset = lzma_level_to_preset(level);

    lzma_stream strm = LZMA_STREAM_INIT;
    lzma_ret ret = lzma_easy_encoder(&strm, preset, LZMA_CHECK_CRC64);
    if (ret != LZMA_OK) {
        return -1; // initialisation failed
    }

    unsigned char input[LZMA_CHUNK];
    unsigned char output[LZMA_CHUNK];

    int return_code = 0;

    Py_BEGIN_ALLOW_THREADS

    lzma_action action = LZMA_RUN;

    while (1) {
        if (strm.avail_in == 0) {
            size_t nread = fread(input, 1, LZMA_CHUNK, src);
            if (ferror(src)) {
                return_code = -1; // read error
                break;
            }

            strm.next_in = input;
            strm.avail_in = nread;

            if (feof(src)) {
                action = LZMA_FINISH;
            }
        }

        strm.next_out = output;
        strm.avail_out = LZMA_CHUNK;

        ret = lzma_code(&strm, action);

        size_t write_size = LZMA_CHUNK - strm.avail_out;
        if (write_size > 0) {
            if (fwrite(output, 1, write_size, dst) != write_size || ferror(dst)) {
                return_code = -1; // write error
                break;
            }
        }

        if (ret == LZMA_STREAM_END) {
            break; // finished
        }

        if (ret != LZMA_OK) {
            return_code = -1; // compression error
            break;
        }
    }

    Py_END_ALLOW_THREADS

    lzma_end(&strm);
    return return_code;
}

static int
lzma_decompress_stream(FILE *src, FILE *dst, uint64_t orig_size)
{
    (void)orig_size; // unused parameter

    lzma_stream strm = LZMA_STREAM_INIT;

    uint64_t memlimit = UINT64_MAX;
    uint32_t flags = 0;
    lzma_ret ret = lzma_stream_decoder(&strm, memlimit, flags);
    if (ret != LZMA_OK) {
        return -1; // initialisation failed
    }

    unsigned char input[LZMA_CHUNK];
    unsigned char output[LZMA_CHUNK];

    int return_code = 0;

    Py_BEGIN_ALLOW_THREADS

    while (1) {
        if (strm.avail_in == 0) {
            size_t nread = fread(input, 1, LZMA_CHUNK, src);
            if (ferror(src)) {
                return_code = -1; // read error
                break;
            }

            strm.next_in = input;
            strm.avail_in = nread;
        }

        if (strm.avail_in == 0 && feof(src)) {
            break; // end of file
        }

        strm.next_out = output;
        strm.avail_out = LZMA_CHUNK;

        ret = lzma_code(&strm, LZMA_RUN);

        size_t write_size = LZMA_CHUNK - strm.avail_out;
        if (write_size > 0) {
            if (fwrite(output, 1, write_size, dst) != write_size || ferror(dst)) {
                return_code = -1; // write error
                break;
            }
        }

        if (ret == LZMA_STREAM_END) {
            break; // finished
        }

        if (ret != LZMA_OK) {
            return_code = -1; // decompression error
            break;
        }
    }

    Py_END_ALLOW_THREADS

    lzma_end(&strm);
    return return_code;
}


// ---- Backend Definition ----

static const CBackend lzma_backend = {
    .name = "lzma",
    .id = ALGO_LZMA,
    .is_available = lzma_is_available,
    .max_compressed_size = lzma_max_compressed_size,
    .compress_buffer = lzma_compress_buffer,
    .decompress_buffer = lzma_decompress_buffer,
    .compress_stream = lzma_compress_stream,
    .decompress_stream = lzma_decompress_stream,
};

const CBackend *get_lzma_backend(void) {
    return &lzma_backend;
}