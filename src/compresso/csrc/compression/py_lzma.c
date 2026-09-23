#define PY_SSIZE_T_CLEAN
#define LZMA_DECOMPRESS_MEMLIMIT (512ULL * 1024 * 1024) // 512MB
#include "../codec/codec.h"
#include "../common.h"
#include <Python.h>
#include <lzma.h>

static int lzma_is_available(void) {
  return 1; // lzma is always available if this code is compiled
}

static size_t lzma_max_compressed_size(size_t input_size) {
  const size_t overhead = 128 * 1024;
  size_t tmp, result;
  tmp = input_size / 3;

  if (add_overflow_size(input_size, tmp, &result) ||
      add_overflow_size(result, overhead, &result)) {
    return SIZE_MAX;
  }
  return result;
}

static uint32_t lzma_level_to_preset(int level) {
  if (level < 0)
    level = 6; // default
  if (level > 9)
    level = 9;
  return (uint32_t)level | LZMA_PRESET_EXTREME;
}

// ---- Buffer Compression/Decompression ----

static int lzma_compress_buffer(const unsigned char *input, size_t input_size,
                                unsigned char *output, size_t *output_capacity,
                                int level, size_t *output_size) {
  uint32_t preset = lzma_level_to_preset(level);
  lzma_ret ret;
  size_t output_pos = 0;

  Py_BEGIN_ALLOW_THREADS ret =
      lzma_easy_buffer_encode(preset, LZMA_CHECK_CRC64, NULL, input, input_size,
                              output, &output_pos, *output_capacity);
  Py_END_ALLOW_THREADS

      if (ret != LZMA_OK) {
    return -1; // compression failed
  }

  *output_size = output_pos;
  return 0; // success
}

static int lzma_decompress_buffer(const unsigned char *input, size_t input_size,
                                  unsigned char *output,
                                  size_t *output_capacity,
                                  size_t *output_size) {
  lzma_ret ret;
  uint64_t memlimit = LZMA_DECOMPRESS_MEMLIMIT;
  uint32_t flags = 0;
  size_t input_pos = 0;
  size_t output_pos = 0;

  Py_BEGIN_ALLOW_THREADS ret = lzma_stream_buffer_decode(
      &memlimit, flags, NULL, input, &input_pos, input_size, output,
      &output_pos, *output_capacity);
  Py_END_ALLOW_THREADS

      if (ret != LZMA_OK) {
    if (ret == LZMA_MEMLIMIT_ERROR) {
      PyErr_Format(comp_BackendError,
                   "LZMA decompression exceeded memory limit: %llu",
                   (unsigned long long)LZMA_DECOMPRESS_MEMLIMIT);
    } else if (ret == LZMA_FORMAT_ERROR) {
      PyErr_SetString(comp_BackendError,
                      "LZMA format error: invalid compressed data");
    } else if (ret == LZMA_DATA_ERROR) {
      PyErr_SetString(comp_BackendError,
                      "LZMA data error: corrupted compressed data");
    } else {
      PyErr_SetString(comp_BackendError, "LZMA decompression failed");
    }
    return -1; // decompression failed
  }

  *output_size = output_pos;
  return 0; // success
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
    .max_compressed_size = lzma_max_compressed_size,
    .compress_buffer = lzma_compress_buffer,
    .decompress_buffer = lzma_decompress_buffer,
    .compress_stream = lzma_compress_stream,
    .decompress_stream = lzma_decompress_stream,
};

const CBackend *get_lzma_backend(void) { return &lzma_backend; }
