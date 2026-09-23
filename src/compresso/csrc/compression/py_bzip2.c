#define PY_SSIZE_T_CLEAN
#include "../codec/codec.h"
#include "../common.h"
#include <Python.h>

static int bzip2_is_available(void) {
  return 1; // bzip2 is always available if this code is compiled
}

// ---- Stream Compression/Decompression ----

static int bzip2_compress_stream(FILE *src, FILE *dst, int level,
                                 CoreContext *ctx) {
  CodecParams params = {.level = level};
  return codec_run_stream(codec_bzip2_ops(), &params, 0, src, dst, ctx);
}

static int bzip2_decompress_stream(FILE *src, FILE *dst, uint64_t orig_size,
                                   CoreContext *ctx) {
  CodecParams params = {.orig_size = orig_size};
  return codec_run_stream(codec_bzip2_ops(), &params, 1, src, dst, ctx);
}

// ---- Backend Definition ----

static const CBackend bzip2_backend = {
    .name = "bzip2",
    .id = ALGO_BZIP2,
    .is_available = bzip2_is_available,
    .compress_stream = bzip2_compress_stream,
    .decompress_stream = bzip2_decompress_stream,
};

const CBackend *get_bzip2_backend(void) { return &bzip2_backend; }
