#define PY_SSIZE_T_CLEAN
#include "../codec/codec.h"
#include "../standalone.h"
#include <string.h>

// lzma_easy_encoder emits the complete .xz container with an embedded CRC64
// integrity check, verified by the decoder
static int xz_compress_file(const char *input_path, const char *output_path,
                            int level, CoreContext *ctx) {
  CodecParams params = {.level = level};
  return codec_run_file(codec_lzma_ops(), &params, 0, input_path, output_path,
                        ctx, "xz compression failed");
}

static int xz_decompress_file(const char *input_path, const char *output_path,
                              CoreContext *ctx) {
  CodecParams params = {0};
  return codec_run_file(codec_lzma_ops(), &params, 1, input_path, output_path,
                        ctx, "xz decompression failed");
}

static char *xz_get_original_name(const char *compressed_path) {
  (void)compressed_path; // .xz does not store the original filename
  return NULL;
}

static int xz_is_format(const unsigned char *magic, size_t size) {
  static const unsigned char MAGIC_XZ[] = {0xFD, 0x37, 0x7A, 0x58, 0x5A, 0x00};
  return (size >= 6 && memcmp(magic, MAGIC_XZ, 6) == 0);
}

static const StandaloneFormat xz_format = {
    .name = "xz",
    .extension = ".xz",
    .compress_file = xz_compress_file,
    .decompress_file = xz_decompress_file,
    .get_original_name = xz_get_original_name,
    .is_format = xz_is_format,
};

const StandaloneFormat *get_xz_format(void) { return &xz_format; }
