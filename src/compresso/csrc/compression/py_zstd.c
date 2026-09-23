#define PY_SSIZE_T_CLEAN
#include "../codec/codec.h"
#include "../common.h"
#include <Python.h>
#include <zstd.h>

static int zstd_is_available(void) {
  return 1; // zstd is always available if this code is compiled
}

static size_t zstd_max_compressed_size(size_t input_size) {
  return ZSTD_compressBound(input_size);
}

static int zstd_level_from_generic(int level) {
  if (level < ZSTD_minCLevel())
    return ZSTD_CLEVEL_DEFAULT;
  if (level > ZSTD_maxCLevel())
    return ZSTD_maxCLevel();
  return level;
}

// ---- Buffer Compression/Decompression ----

static int zstd_compress_buffer(const unsigned char *input, size_t input_size,
                                unsigned char *output, size_t *output_capacity,
                                int level, size_t *output_size) {
  int zlevel =
      (level >= 0) ? zstd_level_from_generic(level) : ZSTD_CLEVEL_DEFAULT;

  int ret;
  Py_BEGIN_ALLOW_THREADS ret =
      ZSTD_compress(output, *output_capacity, input, input_size, zlevel);
  Py_END_ALLOW_THREADS

      if (ZSTD_isError(ret)) {
    return -1; // compression failed
  }

  *output_size = ret;
  return 0; // success
}

static int zstd_decompress_buffer(const unsigned char *input, size_t input_size,
                                  unsigned char *output,
                                  size_t *output_capacity,
                                  size_t *output_size) {
  int ret;
  Py_BEGIN_ALLOW_THREADS ret =
      ZSTD_decompress(output, *output_capacity, input, input_size);
  Py_END_ALLOW_THREADS

      if (ZSTD_isError(ret)) {
    return -1; // decompression failed
  }

  *output_size = ret;
  return 0; // success
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
    .max_compressed_size = zstd_max_compressed_size,
    .compress_buffer = zstd_compress_buffer,
    .decompress_buffer = zstd_decompress_buffer,
    .compress_stream = zstd_compress_stream,
    .decompress_stream = zstd_decompress_stream,
};

const CBackend *get_zstd_backend(void) { return &zstd_backend; }
