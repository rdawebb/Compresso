#define PY_SSIZE_T_CLEAN
#include "common.h"
#include <Python.h>

// `.comp` payloads: get_capabilities lists the rows in this order
const CBackend BACKENDS[] = {
    {.name = "zlib",
     .id = ALGO_ZLIB,
     .levels = LEVELS_ZLIB,
     .engine = codec_zlib_ops,
     .enc = {.wrap = CODEC_WRAP_ZLIB},
     .dec = {.wrap = CODEC_WRAP_ZLIB}},
    {.name = "bzip2",
     .id = ALGO_BZIP2,
     .levels = LEVELS_BZIP2,
     .engine = codec_bzip2_ops},
    {.name = "lzma",
     .id = ALGO_LZMA,
     .levels = LEVELS_LZMA,
     .engine = codec_lzma_ops,
     .enc = {.extreme = 1}},
    {.name = "zstd",
     .id = ALGO_ZSTD,
     .levels = LEVELS_ZSTD,
     .engine = codec_zstd_ops,
     .enc = {.checksum = 1}},
    {.name = "lz4",
     .id = ALGO_LZ4,
     .levels = LEVELS_LZ4,
     .engine = codec_lz4_ops,
     .enc = {.checksum = 1}},
    {.name = "snappy",
     .id = ALGO_SNAPPY,
     .levels = LEVELS_NONE,
     .engine = codec_snappy_ops,
     .enc = {.checksum = 1}}, // The framing's CRC-32C, which it always writes
    {.name = NULL},
};

const CBackend *find_backend_by_id(uint8_t id) {
  for (const CBackend *b = BACKENDS; b->name; b++)
    if (b->id == id)
      return b;
  return NULL;
}

int backend_compress(const CBackend *backend, FILE *src, FILE *dst, int level,
                     CoreContext *ctx) {
  CodecParams params = backend->enc;
  params.level = level;
  return codec_run_stream(backend->engine(), &params, 0, src, dst, ctx);
}

int backend_decompress(const CBackend *backend, FILE *src, FILE *dst,
                       uint64_t orig_size, CoreContext *ctx) {
  CodecParams params = backend->dec;
  params.exact_size = 1;
  params.orig_size = orig_size;
  return codec_run_stream(backend->engine(), &params, 1, src, dst, ctx);
}
