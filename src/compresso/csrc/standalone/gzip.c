#define PY_SSIZE_T_CLEAN
#include "../codec/codec.h"
#include "../magics.h"
#include "../common.h"
#include "../fsutil.h"
#include "../standalone.h"
#include <Python.h>
#include <stdio.h>
#include <string.h>

// GZIP header (RFC 1952): a fixed 10-byte record, addressed by byte offset
// rather than declared as a struct so that padding can't reach the file
#define GZIP_HEADER_SIZE 10
#define GZIP_OFF_MAGIC0 0
#define GZIP_OFF_MAGIC1 1
#define GZIP_OFF_METHOD 2
#define GZIP_OFF_FLAGS 3

#define FEXTRA 0x04
#define FNAME 0x08

// zlib reports a bad container as a plain data error, so the header is checked
// here first to keep saying which part of it was wrong
static int gzip_check_header(const char *path) {
  FILE *f = fs_fopen(path, "rb");
  if (!f) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, path);
    return -1;
  }

  uint8_t header[3];
  size_t read = fread(header, 1, sizeof(header), f);
  fclose(f);

  if (read != sizeof(header)) {
    PyErr_SetString(comp_HeaderError, "Failed to read GZIP header");
    return -1;
  }

  if (header[GZIP_OFF_MAGIC0] != 0x1f || header[GZIP_OFF_MAGIC1] != 0x8b) {
    PyErr_SetString(comp_HeaderError, "Invalid GZIP magic number");
    return -1;
  }

  if (header[GZIP_OFF_METHOD] != 0x08) {
    PyErr_SetString(comp_HeaderError, "Unsupported compression method");
    return -1;
  }

  return 0;
}

static int gzip_compress_file(const char *input_path, const char *output_path,
                              int level, CoreContext *ctx) {
  CodecParams params = {
      .level = level, .wrap = CODEC_WRAP_GZIP, .label = "gzip"};
  return codec_run_file(codec_zlib_ops(), &params, 0, input_path, output_path,
                        ctx, "gzip compression failed");
}

static int gzip_decompress_file(const char *input_path, const char *output_path,
                                CoreContext *ctx) {
  if (gzip_check_header(input_path) != 0) {
    return -1;
  }

  // zlib consumes the header's optional fields and checks the trailer's CRC32
  // and ISIZE itself
  CodecParams params = {.wrap = CODEC_WRAP_GZIP, .label = "gzip"};
  return codec_run_file(codec_zlib_ops(), &params, 1, input_path, output_path,
                        ctx, "gzip decompression failed");
}

static char *gzip_get_original_name(const char *compressed_path) {
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

static int gzip_is_format(const unsigned char *magic, size_t size) {
  return magic_is_gzip(magic, size);
}

static const StandaloneFormat gzip_format = {
    .name = "gzip",
    .extension = ".gz",
    .levels = LEVELS_ZLIB,
    .compress_file = gzip_compress_file,
    .decompress_file = gzip_decompress_file,
    .get_original_name = gzip_get_original_name,
    .is_format = gzip_is_format,
};

const StandaloneFormat *get_gzip_format(void) { return &gzip_format; }
