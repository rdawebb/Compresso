#define PY_SSIZE_T_CLEAN
#include "../codec/codec.h"
#include "../standalone.h"

static int zstd_compress_file(const char *input_path, const OutputTarget *out,
                              int level, CoreContext *ctx) {
  // The XXH64 content checksum is verified as the frame ends
  CodecParams params = {.level = level, .checksum = 1};
  return codec_run_file(codec_zstd_ops(), &params, 0, input_path, out, ctx,
                        "zstd compression failed");
}

static int zstd_decompress_file(const char *input_path, const OutputTarget *out,
                                CoreContext *ctx) {
  CodecParams params = {.concatenated = 1};
  return codec_run_file(codec_zstd_ops(), &params, 1, input_path, out, ctx,
                        "zstd decompression failed: corrupted or invalid "
                        "data");
}

static char *zstd_get_original_name(const char *compressed_path) {
  (void)compressed_path; // .zst does not store the original filename
  return NULL;
}

static const StandaloneFormat zstd_format = {
    .name = "zstd",
    .extension = ".zst",
    .levels = LEVELS_ZSTD,
    .compress_file = zstd_compress_file,
    .decompress_file = zstd_decompress_file,
    .get_original_name = zstd_get_original_name,
};

const StandaloneFormat *get_zstd_format(void) { return &zstd_format; }
