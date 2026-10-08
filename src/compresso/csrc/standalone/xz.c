#define PY_SSIZE_T_CLEAN
#include "../codec/codec.h"
#include "../standalone.h"

// lzma_easy_encoder emits the complete .xz container with an embedded CRC64
// integrity check, verified by the decoder
static int xz_compress_file(const char *input_path, const OutputTarget *out,
                            int level, CoreContext *ctx) {
  CodecParams params = {.level = level, .label = "xz"};
  return codec_run_file(codec_lzma_ops(), &params, 0, input_path, out, ctx,
                        "xz compression failed");
}

static int xz_decompress_file(const char *input_path, FILE *input,
                              const OutputTarget *out, CoreContext *ctx) {
  CodecParams params = {.concatenated = 1, .label = "xz"};
  return codec_run_source(codec_lzma_ops(), &params, 1, input_path, input, out,
                          ctx, "xz decompression failed");
}

static char *xz_get_original_name(const char *compressed_path) {
  (void)compressed_path; // .xz does not store the original filename
  return NULL;
}

static const StandaloneFormat xz_format = {
    .name = "xz",
    .extension = ".xz",
    .levels = LEVELS_LZMA,
    .compress_file = xz_compress_file,
    .decompress_file = xz_decompress_file,
    .get_original_name = xz_get_original_name,
};

const StandaloneFormat *get_xz_format(void) { return &xz_format; }
