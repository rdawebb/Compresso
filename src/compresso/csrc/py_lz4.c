#define PY_SSIZE_T_CLEAN
#define LZ4_CHUNK 65536 // 64KB
#define LZ4_OUT_CHUNK (LZ4_CHUNK + LZ4_CHUNK / 255 + 16) // worst-case output size for LZ4 frame
#include <Python.h>
#include <lz4frame.h>
#include "common.h"

static int
lz4_is_available(void) 
{
    return 1; // lz4 is always available if this code is compiled
}

static size_t
lz4_max_compressed_size(size_t input_size) 
{
    LZ4F_preferences_t prefs;
    memset(&prefs, 0, sizeof(prefs));
    return LZ4F_compressFrameBound(input_size, &prefs);
}

static int
LZ4_level_from_generic(int level)
{
    if (level < 0) return 0; // default
    return level;
}


// ---- Buffer Compression/Decompression ----

static int
lz4_compress_buffer(const unsigned char *input, size_t input_size,
                    unsigned char *output, size_t *output_capacity,
                    int level, size_t *output_size)
{
    LZ4F_preferences_t prefs;
    memset(&prefs, 0, sizeof(prefs));
    prefs.compressionLevel = LZ4_level_from_generic(level);

    size_t ret;
    Py_BEGIN_ALLOW_THREADS
    ret = LZ4F_compressFrame(output, *output_capacity,
                             input, input_size,
                             &prefs);
    Py_END_ALLOW_THREADS

    if (LZ4F_isError(ret)) {
        return -1; // compression failed
    }

    *output_size = ret;
    return 0; // success
}

static int
lz4_decompress_buffer(const unsigned char *input, size_t input_size,
                      unsigned char *output, size_t *output_capacity,
                      size_t *output_size)
{
    LZ4F_decompressionContext_t dctx;
    size_t ret = LZ4F_createDecompressionContext(&dctx, LZ4F_VERSION);
    if (LZ4F_isError(ret)) {
        return -1; // failed to create decompression context
    }

    size_t src_size = input_size;
    size_t dst_size = *output_capacity;

    size_t input_pos = 0;
    size_t output_pos = 0;
    int err = 0;

    Py_BEGIN_ALLOW_THREADS

    while (input_pos < src_size && output_pos < dst_size) {
        size_t input_chunk = src_size - input_pos;
        size_t output_chunk = dst_size - output_pos;

        size_t src_size_tmp = input_chunk;
        size_t dst_size_tmp = output_chunk;

        ret = LZ4F_decompress(dctx,
                              output + output_pos, &dst_size_tmp,
                              input + input_pos, &src_size_tmp,
                              NULL);
        
        if (LZ4F_isError(ret)) {
            err = -1; // decompression error
            break;
        }

        input_pos += src_size_tmp;
        output_pos += dst_size_tmp;

        if (ret == 0) {
            break; // decompression completed
        }
    }

    Py_END_ALLOW_THREADS

    LZ4F_freeDecompressionContext(dctx);

    if (err) {
        return -1; // decompression failed
    }

    *output_size = output_pos;
    return 0; // success
}


// ---- Stream Compression/Decompression ----

static int
lz4_compress_stream(FILE *src, FILE *dst, int level)
{
    LZ4F_compressionContext_t cctx;
    size_t ret = LZ4F_createCompressionContext(&cctx, LZ4F_VERSION);
    if (LZ4F_isError(ret)) {
        return -1; // failed to create compression context
    }

    LZ4F_preferences_t prefs;
    memset(&prefs, 0, sizeof(prefs));
    prefs.compressionLevel = LZ4_level_from_generic(level);

    unsigned char input[LZ4_CHUNK];
    unsigned char output[LZ4_OUT_CHUNK];

    int return_code = 0;

    Py_BEGIN_ALLOW_THREADS

    size_t header_size = LZ4F_compressBegin(cctx, output, LZ4_OUT_CHUNK, &prefs);
    if (LZ4F_isError(header_size)) {
        return_code = -1; // compression error
        goto done_stream;
    }
    if (fwrite(output, 1, header_size, dst) != header_size || ferror(dst)) {
        return_code = -1; // write error
        goto done_stream;
    }

    for (;;) {
        size_t nread = fread(input, 1, LZ4_CHUNK, src);
        if (ferror(src)) {
            return_code = -1; // read error
            break;
        }

        if (nread == 0) {
            size_t end_size = LZ4F_compressEnd(cctx, output, LZ4_OUT_CHUNK, NULL);
            if (LZ4F_isError(end_size)) {
                return_code = -1; // compression error
                break;
            }
            if (end_size > 0) {
                if (fwrite(output, 1, end_size, dst) != end_size || ferror(dst)) {
                    return_code = -1; // write error
                }
            }
            break; // end of file
        }

        size_t src_size = nread;
        size_t dst_size = LZ4_OUT_CHUNK;

        size_t bytes = LZ4F_compressUpdate(cctx,
                                      output, dst_size,
                                      input, src_size,
                                      NULL);
        if (LZ4F_isError(bytes)) {
            return_code = -1; // compression error
            break;
        }

        if (bytes > 0) {
            if (fwrite(output, 1, bytes, dst) != bytes || ferror(dst)) {
                return_code = -1; // write error
                break;
            }
        }
    }

done_stream:
    Py_END_ALLOW_THREADS

    LZ4F_freeCompressionContext(cctx);
    return return_code;
}

static int
lz4_decompress_stream(FILE *src, FILE *dst, uint64_t orig_size)
{
    (void)orig_size; // unused parameter

    LZ4F_decompressionContext_t dctx;
    size_t ret = LZ4F_createDecompressionContext(&dctx, LZ4F_VERSION);
    if (LZ4F_isError(ret)) {
        return -1; // failed to create decompression context
    }

    unsigned char input[LZ4_CHUNK];
    unsigned char output[LZ4_OUT_CHUNK];

    int return_code = 0;

    Py_BEGIN_ALLOW_THREADS

    size_t input_size = 0;
    size_t input_pos = 0;

    for (;;) {
        if (input_pos == input_size) {
            input_size = fread(input, 1, LZ4_CHUNK, src);
            if (ferror(src)) {
                return_code = -1; // read error
                break;
            }
            input_pos = 0;
            
            if (input_size == 0) {
                break; // end of file
            }
        }

        size_t src_size = input_size - input_pos;
        size_t dst_size = LZ4_OUT_CHUNK;

        ret = LZ4F_decompress(dctx,
                              output, &dst_size,
                              input + input_pos, &src_size,
                              NULL);

        if (LZ4F_isError(ret)) {
            return_code = -1; // decompression error
            break;
        }

        input_pos += src_size;

        if (dst_size > 0) {
            if (fwrite(output, 1, dst_size, dst) != dst_size || ferror(dst)) {
                return_code = -1; // write error
                break;
            }
        }

        if (ret == 0) {
            break; // decompression completed
        }
    }

    Py_END_ALLOW_THREADS

    LZ4F_freeDecompressionContext(dctx);
    return return_code;
}


// ---- Backend Definition ----

static const CBackend lz4_backend = {
    .name = "lz4",
    .id = ALGO_LZ4,
    .is_available = lz4_is_available,
    .max_compressed_size = lz4_max_compressed_size,
    .compress_buffer = lz4_compress_buffer,
    .decompress_buffer = lz4_decompress_buffer,
    .compress_stream = lz4_compress_stream,
    .decompress_stream = lz4_decompress_stream,
};

const CBackend *get_lz4_backend(void) {
    return &lz4_backend;
}