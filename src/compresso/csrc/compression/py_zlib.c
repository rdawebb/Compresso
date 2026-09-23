#define PY_SSIZE_T_CLEAN
#include "../codec/codec.h"
#include "../common.h"
#include <Python.h>
#include <zlib.h>

static int zlib_is_available(void) {
  return 1; // zlib is always available if this code is compiled
}

static size_t zlib_max_compressed_size(size_t input_size) {
  return (size_t)compressBound((uLong)input_size);
}

// ---- Buffer Compression/Decompression ----

static int zlib_compress_buffer(const unsigned char *input, size_t input_size,
                                unsigned char *output, size_t *output_capacity,
                                int level, size_t *output_size) {
  uLongf dest_len = (uLongf)(*output_capacity);
  int ret;

  Py_BEGIN_ALLOW_THREADS ret =
      compress2(output, &dest_len, input, (uLongf)input_size,
                (level >= 0 && level <= 9) ? level : Z_DEFAULT_COMPRESSION);
  Py_END_ALLOW_THREADS

      if (ret != Z_OK) {
    return -1; // compression failed
  }

  *output_size = (size_t)dest_len;
  return 0; // success
}

static int zlib_decompress_buffer(const unsigned char *input, size_t input_size,
                                  unsigned char *output,
                                  size_t *output_capacity,
                                  size_t *output_size) {
  uLongf dest_len = (uLongf)(*output_capacity);
  int ret;

  Py_BEGIN_ALLOW_THREADS ret =
      uncompress(output, &dest_len, input, (uLongf)input_size);
  Py_END_ALLOW_THREADS

      if (ret != Z_OK) {
    return -1; // decompression failed
  }

  *output_size = (size_t)dest_len;
  return 0; // success
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
    .max_compressed_size = zlib_max_compressed_size,
    .compress_buffer = zlib_compress_buffer,
    .decompress_buffer = zlib_decompress_buffer,
    .compress_stream = zlib_compress_stream,
    .decompress_stream = zlib_decompress_stream,
};

const CBackend *get_zlib_backend(void) { return &zlib_backend; }
