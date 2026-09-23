#define PY_SSIZE_T_CLEAN
#include "../codec/codec.h"
#include "../standalone.h"
#include <string.h>

static int zstd_compress_file(const char *input_path, const char *output_path,
                              int level, CoreContext *ctx) {
  // The XXH64 content checksum is verified as the frame ends
  CodecParams params = {.level = level, .checksum = 1};
  return codec_run_file(codec_zstd_ops(), &params, 0, input_path, output_path,
                        ctx, "zstd compression failed");
}

static int zstd_decompress_file(const char *input_path, const char *output_path,
                                CoreContext *ctx) {
  CodecParams params = {0};
  return codec_run_file(codec_zstd_ops(), &params, 1, input_path, output_path,
                        ctx, "zstd decompression failed: corrupted or invalid "
                             "data");
}

static char *zstd_get_original_name(const char *compressed_path) {
  (void)compressed_path; // .zst does not store the original filename
  return NULL;
}

static int zstd_is_format(const unsigned char *magic, size_t size) {
  static const unsigned char MAGIC_ZSTD[] = {0x28, 0xB5, 0x2F, 0xFD};
  return (size >= 4 && memcmp(magic, MAGIC_ZSTD, 4) == 0);
}

static const StandaloneFormat zstd_format = {
    .name = "zstd",
    .extension = ".zst",
    .compress_file = zstd_compress_file,
    .decompress_file = zstd_decompress_file,
    .get_original_name = zstd_get_original_name,
    .is_format = zstd_is_format,
};

const StandaloneFormat *get_zstd_format(void) { return &zstd_format; }
