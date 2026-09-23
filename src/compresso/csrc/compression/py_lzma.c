#define PY_SSIZE_T_CLEAN
#include "../codec/codec.h"
#include "../common.h"
#include <Python.h>

static int lzma_is_available(void) {
  return 1; // lzma is always available if this code is compiled
}

// ---- Stream Compression/Decompression ----

static int lzma_compress_stream(FILE *src, FILE *dst, int level,
                                CoreContext *ctx) {
  CodecParams params = {.level = level, .extreme = 1};
  return codec_run_stream(codec_lzma_ops(), &params, 0, src, dst, ctx);
}

static int lzma_decompress_stream(FILE *src, FILE *dst, uint64_t orig_size,
                                  CoreContext *ctx) {
  CodecParams params = {.orig_size = orig_size};
  return codec_run_stream(codec_lzma_ops(), &params, 1, src, dst, ctx);
}

// ---- Backend Definition ----

static const CBackend lzma_backend = {
    .name = "lzma",
    .id = ALGO_LZMA,
    .is_available = lzma_is_available,
    .compress_stream = lzma_compress_stream,
    .decompress_stream = lzma_decompress_stream,
};

const CBackend *get_lzma_backend(void) { return &lzma_backend; }
