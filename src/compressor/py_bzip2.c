#define PY_SSIZE_T_CLEAN
#define BZIP2_CHUNK 65536 // 64KB
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
bzip2_block_size_from_level(int level) {
    if (level <= 0) return 9; // default: max compression
    if (level > 9) return 9;
    return level;
}


// ---- Buffer Compression/Decompression ----

static int
bzip2_compress_buffer(const unsigned char *input, size_t input_size,
                      unsigned char *output, size_t *output_capacity,
                      int level, size_t *output_size)
{
    int blockSize100k;
    if (level <= 0) blockSize100k = 9; // default: max compression
    else if (level > 9) blockSize100k = 9;
    else blockSize100k = level;

    unsigned int dest_len = (unsigned int)(*output_capacity);
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
    unsigned int dest_len = (unsigned int)(*output_capacity);
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

// ---- Stream Compression/Decompression ----

static int
bzip2_compress_stream(FILE *src, FILE *dst, int level)
{
    int bzerr;
    int blockSize100k = bzip2_block_size_from_level(level);

    BZFILE *bzf = BZ2_bzWriteOpen(
        &bzerr,
        dst,
        blockSize100k,
        0, 30 // verbosity and workFactor (recommended default)
    );
    if (bzerr != BZ_OK || bzf == NULL) {
        return -1; // failed to open bzip2 stream
    }

    unsigned char buffer[BZIP2_CHUNK];
    int ret = 0;

    Py_BEGIN_ALLOW_THREADS

    for (;;) {
        size_t nread = fread(buffer, 1, BZIP2_CHUNK, src);
        if (ferror(src)) {
            ret = -1; // read error
            break;
        }

        if (nread > 0) {
            BZ2_bzWrite(&bzerr, bzf, buffer, (int)nread);
            if (bzerr != BZ_OK) {
                ret = -1; // write error
                break;
            }
        }

        if (feof(src)) {
            break; // end of file
        }
    }

    BZ2_bzWriteClose(&bzerr, bzf, 0, NULL, NULL);
    if (bzerr != BZ_OK) {
        ret = -1; // error closing bzip2 stream
    }

    Py_END_ALLOW_THREADS

    return ret;
}

static int
bzip2_decompress_stream(FILE *src, FILE *dst, uint64_t orig_size)
{
    (void)orig_size; // unused

    int bzerr;
    BZFILE *bzf = BZ2_bzReadOpen(
        &bzerr,
        src,
        0, 0, // small and verbosity flags
        NULL, 0
    );
    if (bzerr != BZ_OK || bzf == NULL) {
        return -1; // failed to open bzip2 stream
    }

    unsigned char buffer[BZIP2_CHUNK];
    int ret = 0;

    Py_BEGIN_ALLOW_THREADS

    for (;;) {
        int nread = BZ2_bzRead(&bzerr, bzf, buffer, BZIP2_CHUNK);
        if (bzerr == BZ_OK || bzerr == BZ_STREAM_END) {
            if (nread > 0) {
                if (fwrite(buffer, 1, nread, dst) != (size_t)nread || ferror(dst)) {
                    ret = -1; // write error
                    break;
                }
            }
            if (bzerr == BZ_STREAM_END) {
                break; // end of stream
            }
        } else {
            ret = -1; // read error
            break;
        }
    }

    BZ2_bzReadClose(&bzerr, bzf);

    Py_END_ALLOW_THREADS

    return ret;
}


// ---- Backend Definition ----

static const CBackend bzip2_backend = {
    .name = "bzip2",
    .id = ALGO_BZIP2,
    .is_available = bzip2_is_available,
    .max_compressed_size = bzip2_max_compressed_size,
    .compress_buffer = bzip2_compress_buffer,
    .decompress_buffer = bzip2_decompress_buffer,
    .compress_stream = bzip2_compress_stream,
    .decompress_stream = bzip2_decompress_stream,
};

const CBackend *get_bzip2_backend(void) {
    return &bzip2_backend;
}