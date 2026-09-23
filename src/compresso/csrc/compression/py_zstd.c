#define PY_SSIZE_T_CLEAN
#include "../codec/codec.h"
#include "../common.h"
#include <Python.h>

static int zstd_is_available(void) {
  return 1; // zstd is always available if this code is compiled
}

// ---- Stream Compression/Decompression ----

static int zstd_compress_stream(FILE *src, FILE *dst, int level,
                                CoreContext *ctx) {
  CodecParams params = {.level = level};
  return codec_run_stream(codec_zstd_ops(), &params, 0, src, dst, ctx);
}

static int zstd_decompress_stream(FILE *src, FILE *dst, uint64_t orig_size,
                                  CoreContext *ctx) {
  CodecParams params = {.orig_size = orig_size};
  return codec_run_stream(codec_zstd_ops(), &params, 1, src, dst, ctx);
}

// ---- Backend Definition ----

static const CBackend zstd_backend = {
    .name = "zstd",
    .id = ALGO_ZSTD,
    .is_available = zstd_is_available,
    .compress_stream = zstd_compress_stream,
    .decompress_stream = zstd_decompress_stream,
};

const CBackend *get_zstd_backend(void) { return &zstd_backend; }
