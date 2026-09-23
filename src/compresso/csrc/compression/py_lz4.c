#define PY_SSIZE_T_CLEAN
#include "../codec/codec.h"
#include "../common.h"
#include <Python.h>
#include <lz4frame.h>

static int lz4_is_available(void) {
  return 1; // lz4 is always available if this code is compiled
}

static size_t lz4_max_compressed_size(size_t input_size) {
  LZ4F_preferences_t prefs;
  memset(&prefs, 0, sizeof(prefs));
  return LZ4F_compressFrameBound(input_size, &prefs);
}

static int LZ4_level_from_generic(int level) {
  if (level < 0)
    return 0; // default
  return level;
}

// ---- Buffer Compression/Decompression ----

static int lz4_compress_buffer(const unsigned char *input, size_t input_size,
                               unsigned char *output, size_t *output_capacity,
                               int level, size_t *output_size) {
  LZ4F_preferences_t prefs;
  memset(&prefs, 0, sizeof(prefs));
  prefs.compressionLevel = LZ4_level_from_generic(level);

  size_t ret;
  Py_BEGIN_ALLOW_THREADS ret =
      LZ4F_compressFrame(output, *output_capacity, input, input_size, &prefs);
  Py_END_ALLOW_THREADS

      if (LZ4F_isError(ret)) {
    return -1; // compression failed
  }

  *output_size = ret;
  return 0; // success
}

static int lz4_decompress_buffer(const unsigned char *input, size_t input_size,
                                 unsigned char *output, size_t *output_capacity,
                                 size_t *output_size) {
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

    ret = LZ4F_decompress(dctx, output + output_pos, &dst_size_tmp,
                          input + input_pos, &src_size_tmp, NULL);

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
    .max_compressed_size = lz4_max_compressed_size,
    .compress_buffer = lz4_compress_buffer,
    .decompress_buffer = lz4_decompress_buffer,
    .compress_stream = lz4_compress_stream,
    .decompress_stream = lz4_decompress_stream,
};

const CBackend *get_lz4_backend(void) { return &lz4_backend; }
