#define PY_SSIZE_T_CLEAN
#include "../codec/codec.h"
#include "../common.h"
#include <Python.h>

static int zlib_is_available(void) {
  return 1; // zlib is always available if this code is compiled
}

// ---- Stream Compression/Decompression ----

static int zlib_compress_stream(FILE *src, FILE *dst, int level,
                                CoreContext *ctx) {
  CodecParams params = {.level = level, .wrap = CODEC_WRAP_ZLIB};
  return codec_run_stream(codec_zlib_ops(), &params, 0, src, dst, ctx);
}

static int zlib_decompress_stream(FILE *src, FILE *dst, uint64_t orig_size,
                                  CoreContext *ctx) {
  CodecParams params = {.orig_size = orig_size, .wrap = CODEC_WRAP_ZLIB};
  return codec_run_stream(codec_zlib_ops(), &params, 1, src, dst, ctx);
}

// ---- Backend Definition ----

static const CBackend zlib_backend = {
    .name = "zlib",
    .id = ALGO_ZLIB,
    .is_available = zlib_is_available,
    .compress_stream = zlib_compress_stream,
    .decompress_stream = zlib_decompress_stream,
};

const CBackend *get_zlib_backend(void) { return &zlib_backend; }
