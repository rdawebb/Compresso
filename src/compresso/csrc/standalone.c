#define PY_SSIZE_T_CLEAN
#include "standalone.h"
#include "common.h"
#include <Python.h>
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
  char failure[64];
  snprintf(failure, sizeof(failure), "%s compression failed", fmt->name);
  return codec_run_file(fmt->engine(), &params, 0, input_path, out, ctx,
                        failure);
}

int standalone_decompress(const FormatDesc *fmt, const char *input_path,
                          FILE *input, const OutputTarget *out,
                          CoreContext *ctx) {
  char failure[64];
  snprintf(failure, sizeof(failure), "%s decompression failed", fmt->name);
  return codec_run_source(fmt->engine(), &fmt->dec, 1, input_path, input, out,
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

  if (!(flags & FNAME)) {
    fclose(f);
    return NULL;
  }

  // Skip extra field if present
  if (flags & FEXTRA) {
    uint8_t xlen_buf[2];
    if (fread(xlen_buf, sizeof(xlen_buf), 1, f) != 1) {
      fclose(f);
      return NULL;
    }
    fseek(f, read_le16(xlen_buf), SEEK_CUR);
  }

  // Read filename
  char name_buf[256];
  size_t i = 0;
  int c;
  while ((c = fgetc(f)) != 0 && c != EOF && i < sizeof(name_buf) - 1) {
    name_buf[i++] = c;
  }
  name_buf[i] = '\0';

  fclose(f);

  if (i > 0) {
    return strdup(name_buf);
  }
  return NULL;
}
