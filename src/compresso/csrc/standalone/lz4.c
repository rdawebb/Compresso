#define PY_SSIZE_T_CLEAN
#include "../codec/codec.h"
#include "../standalone.h"
#include <string.h>

static int lz4_compress_file(const char *input_path, const char *output_path,
                             int level, CoreContext *ctx) {
  // The xxHash content checksum is verified as the frame is consumed
  CodecParams params = {.level = level, .checksum = 1};
  return codec_run_file(codec_lz4_ops(), &params, 0, input_path, output_path,
                        ctx, "lz4 compression failed");
}

static int lz4_decompress_file(const char *input_path, const char *output_path,
                               CoreContext *ctx) {
  CodecParams params = {0};
  return codec_run_file(codec_lz4_ops(), &params, 1, input_path, output_path,
                        ctx, "lz4 decompression failed: corrupted or invalid "
                             "data");
}

static char *lz4_get_original_name(const char *compressed_path) {
  (void)compressed_path; // .lz4 does not store the original filename
  return NULL;
}

static int lz4_is_format(const unsigned char *magic, size_t size) {
  static const unsigned char MAGIC_LZ4[] = {0x04, 0x22, 0x4D, 0x18};
  return (size >= 4 && memcmp(magic, MAGIC_LZ4, 4) == 0);
}

static const StandaloneFormat lz4_format = {
    .name = "lz4",
    .extension = ".lz4",
    .compress_file = lz4_compress_file,
    .decompress_file = lz4_decompress_file,
    .get_original_name = lz4_get_original_name,
    .is_format = lz4_is_format,
};

const StandaloneFormat *get_lz4_format(void) { return &lz4_format; }
