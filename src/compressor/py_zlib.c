#define PY_SSIZE_T_CLEAN
#define ZLIB_CHUNK 65536 // 64KB
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


// ---- Buffer Compression/Decompression ----

static int
zlib_compress_buffer(const unsigned char *input, size_t input_size,
                     unsigned char *output, size_t *output_capacity,
                     int level, size_t *output_size)
{
    uLongf dest_len = (uLongf)output_capacity;
    int ret;

    Py_BEGIN_ALLOW_THREADS
    ret = compress2(output, &dest_len, input, (uLongf)input_size,
                    (level >= 0 && level <= 9) ? level : Z_DEFAULT_COMPRESSION);
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


// ---- Streaming Compression/Decompression ----

static int
zlib_compress_stream(FILE *src, FILE *dst, int level)
{
    int ret;
    int flush;
    unsigned char input[ZLIB_CHUNK];
    unsigned char output[ZLIB_CHUNK];
    z_stream strm;

    memset(&strm, 0, sizeof(strm));

    int zlevel = (level >= 0 && level <= 9) ? level : Z_DEFAULT_COMPRESSION;

    ret = deflateInit(&strm, zlevel);
    if (ret != Z_OK) {
        return -1; // initialization failed
    }

    Py_BEGIN_ALLOW_THREADS

    do {
        strm.avail_in = (uInt)fread(input, 1, ZLIB_CHUNK, src);
        if (ferror(src)) {
            ret = Z_ERRNO;
            break;
        }
        flush = feof(src) ? Z_FINISH : Z_NO_FLUSH;

        strm.next_in = input;

        do {
            strm.avail_out = ZLIB_CHUNK;
            strm.next_out = output;

            ret = deflate(&strm, flush);
            if (ret == Z_STREAM_ERROR) {
                break;
            }

            size_t have = ZLIB_CHUNK - strm.avail_out;
            if (fwrite(output, 1, have, dst) != have || ferror(dst)) {
                ret = Z_ERRNO;
                break;
            }
        } while (strm.avail_out == 0);

        if (ret == Z_ERRNO || ret == Z_STREAM_ERROR) {
            break;
        }
    } while (flush != Z_FINISH);

    deflateEnd(&strm);

    Py_END_ALLOW_THREADS

    if (ret != Z_STREAM_END) {
        return -1; // compression failed
    }

    return 0; // success
}

static int
zlib_decompress_stream(FILE *src, FILE *dst, uint64_t orig_size)
{
    (void)orig_size; // unused parameter

    int ret;
    unsigned char input[ZLIB_CHUNK];
    unsigned char output[ZLIB_CHUNK];
    z_stream strm;

    memset(&strm, 0, sizeof(strm));
    ret = inflateInit(&strm);
    if (ret != Z_OK) {
        return -1; // initialization failed
    }

    Py_BEGIN_ALLOW_THREADS

    do {
        strm.avail_in = (uInt)fread(input, 1, ZLIB_CHUNK, src);
        if (ferror(src)) {
            ret = Z_ERRNO;
            break;
        }
        if (strm.avail_in == 0) {
            break; // end of file
        }
        strm.next_in = input;

        do {
            strm.avail_out = ZLIB_CHUNK;
            strm.next_out = output;

            ret = inflate(&strm, Z_NO_FLUSH);
            if (ret == Z_STREAM_ERROR) {
                ret = Z_DATA_ERROR;
                break;
            }
            switch (ret) {
                case Z_NEED_DICT:
                case Z_DATA_ERROR:
                case Z_MEM_ERROR:
                    goto done;
            }

            size_t have = ZLIB_CHUNK - strm.avail_out;
            if (fwrite(output, 1, have, dst) != have || ferror(dst)) {
                ret = Z_ERRNO;
                goto done;
            }
        } while (strm.avail_out == 0);
    } while (ret != Z_STREAM_END);

done:
    inflateEnd(&strm);

    Py_END_ALLOW_THREADS

    if (ret != Z_STREAM_END) {
        return -1; // decompression failed
    }

    return 0; // success
}


// ---- Backend Definition ----

static const CBackend zlib_backend = {
    .name = "zlib",
    .id = ALGO_ZLIB,
    .is_available = zlib_is_available,
    .max_compressed_size = zlib_max_compressed_size,
    .compress_buffer = zlib_compress_buffer,
    .decompress_buffer = zlib_decompress_buffer,
    .compress_stream = zlib_compress_stream,
    .decompress_stream = zlib_decompress_stream,
};

const CBackend *get_zlib_backend(void) {
    return &zlib_backend;
}