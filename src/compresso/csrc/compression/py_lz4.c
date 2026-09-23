#define PY_SSIZE_T_CLEAN
#include "../codec/codec.h"
#include "../common.h"
#include <Python.h>

static int lz4_is_available(void) {
  return 1; // lz4 is always available if this code is compiled
}

// ---- Stream Compression/Decompression ----

static int lz4_compress_stream(FILE *src, FILE *dst, int level,
                               CoreContext *ctx) {
  CodecParams params = {.level = level};
  return codec_run_stream(codec_lz4_ops(), &params, 0, src, dst, ctx);
}

static int lz4_decompress_stream(FILE *src, FILE *dst, uint64_t orig_size,
                                 CoreContext *ctx) {
  CodecParams params = {.orig_size = orig_size};
  return codec_run_stream(codec_lz4_ops(), &params, 1, src, dst, ctx);
}

// ---- Backend Definition ----

static const CBackend lz4_backend = {
    .name = "lz4",
    .id = ALGO_LZ4,
    .is_available = lz4_is_available,
    .compress_stream = lz4_compress_stream,
    .decompress_stream = lz4_decompress_stream,
};

const CBackend *get_lz4_backend(void) { return &lz4_backend; }
