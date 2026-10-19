#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "standalone.h"
#include "common.h"
#include <stdio.h>
#include <string.h>

const FormatDesc *find_standalone_format(Format format) {
  const FormatDesc *desc = format_by_id(format);
  return desc && desc->kind == KIND_CONTAINER ? desc : NULL;
}

int standalone_compress(const FormatDesc *fmt, const char *input_path,
                        const OutputTarget *out, int level, CoreContext *ctx) {
  CodecParams params = fmt->enc;
  params.level = level;
  params.threads = ctx ? ctx->threads : 0;
  char failure[64];
  snprintf(failure, sizeof(failure), "%s compression failed", fmt->name);
  return codec_run_source(fmt->engine(), &params, 0, input_path, NULL, out, ctx,
                          failure);
}

int standalone_decompress(const FormatDesc *fmt, const char *input_path,
                          FILE *input, const OutputTarget *out,
                          CoreContext *ctx) {
  char failure[64];
  snprintf(failure, sizeof(failure), "%s decompression failed", fmt->name);
  CodecParams params = fmt->dec;
  params.threads = ctx ? ctx->threads : 0;
  return codec_run_source(fmt->engine(), &params, 1, input_path, input, out,
                          ctx, failure);
}

// GZIP header (RFC 1952): a fixed 10-byte record, addressed by byte offset
// rather than declared as a struct so that padding can't reach the file
#define GZIP_HEADER_SIZE 10
#define GZIP_OFF_FLAGS 3

#define FEXTRA 0x04
#define FNAME 0x08

char *gzip_original_name(const char *compressed_path) {
  FILE *f = fs_fopen(compressed_path, "rb");
  if (!f)
    return NULL;

  uint8_t header[GZIP_HEADER_SIZE];
  if (fread(header, sizeof(header), 1, f) != 1) {
    fclose(f);
    return NULL;
  }

  uint8_t flags = header[GZIP_OFF_FLAGS];

  if (header[0] != 0x1f || header[1] != 0x8b || !(flags & FNAME)) {
    fclose(f);
    return NULL;
  }

  if (flags & FEXTRA) {
    uint8_t xlen_buf[2];
    if (fread(xlen_buf, sizeof(xlen_buf), 1, f) != 1 ||
        fseek(f, (long)get_le(xlen_buf, 2), SEEK_CUR) != 0) {
      fclose(f);
      return NULL;
    }
  }

  // gzip's own limit (MAX_PATH_LEN); a name cut short by EOF (which also
  // catches an xlen running past the end) or by the limit is ignored
  char name_buf[1024];
  size_t i = 0;
  int c;
  while ((c = fgetc(f)) > 0 && i < sizeof(name_buf) - 1) {
    name_buf[i++] = (char)c;
  }
  name_buf[i] = '\0';

  fclose(f);

  return c == 0 && i > 0 ? strdup(name_buf) : NULL;
}
