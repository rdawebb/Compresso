#define PY_SSIZE_T_CLEAN
#include "../codec/codec.h"
#include "../standalone.h"

// The .bz2 frame (BZh header + per-block CRC32s) is produced by libbz2, which
// verifies CRCs as it reads
static int bzip2_compress_file(const char *input_path, const char *output_path,
                               int level, CoreContext *ctx) {
  CodecParams params = {.level = level};
  return codec_run_file(codec_bzip2_ops(), &params, 0, input_path, output_path,
                        ctx, "bzip2 compression failed");
}

static int bzip2_decompress_file(const char *input_path,
                                 const char *output_path, CoreContext *ctx) {
  CodecParams params = {0};
  return codec_run_file(codec_bzip2_ops(), &params, 1, input_path, output_path,
                        ctx, "bzip2 decompression failed");
}

static char *bzip2_get_original_name(const char *compressed_path) {
  (void)compressed_path; // .bz2 does not store the original filename
  return NULL;
}

static int bzip2_is_format(const unsigned char *magic, size_t size) {
  return (size >= 2 && magic[0] == 'B' && magic[1] == 'Z');
}

static const StandaloneFormat bzip2_format = {
    .name = "bzip2",
    .extension = ".bz2",
    .compress_file = bzip2_compress_file,
    .decompress_file = bzip2_decompress_file,
    .get_original_name = bzip2_get_original_name,
    .is_format = bzip2_is_format,
};

const StandaloneFormat *get_bzip2_format(void) { return &bzip2_format; }
