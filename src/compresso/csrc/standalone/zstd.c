#define PY_SSIZE_T_CLEAN
#include "../common.h"
#include "../standalone.h"
#include <Python.h>
#include <string.h>
#include <zstd.h>

#define ZSTD_FILE_CHUNK 65536 // 64KB

static int zstd_level_from_generic(int level) {
  if (level < ZSTD_minCLevel())
    return ZSTD_CLEVEL_DEFAULT;
  if (level > ZSTD_maxCLevel())
    return ZSTD_maxCLevel();
  return level;
}

static int zstd_compress_file(const char *input_path, const char *output_path,
                              int level) {
  FILE *input = fopen(input_path, "rb");
  if (!input) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, input_path);
    return -1;
  }

  FILE *output = fopen(output_path, "wb");
  if (!output) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, output_path);
    fclose(input);
    return -1;
  }

  int zlevel =
      (level >= 0) ? zstd_level_from_generic(level) : ZSTD_CLEVEL_DEFAULT;

  ZSTD_CCtx *cctx = ZSTD_createCCtx();
  if (!cctx) {
    PyErr_SetString(comp_BackendError, "Failed to create zstd context");
    fclose(input);
    fclose(output);
    return -1;
  }

  ZSTD_CCtx_setParameter(cctx, ZSTD_c_compressionLevel, zlevel);
  // Embed an XXH64 content checksum so decompression verifies integrity
  ZSTD_CCtx_setParameter(cctx, ZSTD_c_checksumFlag, 1);

  unsigned char in_buf[ZSTD_FILE_CHUNK];
  unsigned char out_buf[ZSTD_FILE_CHUNK];
  int err = 0;

  Py_BEGIN_ALLOW_THREADS

      for (;;) {
    size_t read = fread(in_buf, 1, ZSTD_FILE_CHUNK, input);
    if (ferror(input)) {
      err = -1;
      break;
    }

    int last_chunk = feof(input);
    ZSTD_inBuffer inbuf = {in_buf, read, 0};

    while (inbuf.pos < inbuf.size || last_chunk) {
      ZSTD_outBuffer outbuf = {out_buf, ZSTD_FILE_CHUNK, 0};
      ZSTD_EndDirective mode = last_chunk ? ZSTD_e_end : ZSTD_e_continue;

      size_t r = ZSTD_compressStream2(cctx, &outbuf, &inbuf, mode);
      if (ZSTD_isError(r)) {
        err = -1;
        break;
      }

      if (outbuf.pos > 0) {
        if (fwrite(out_buf, 1, outbuf.pos, output) != outbuf.pos ||
            ferror(output)) {
          err = -1;
          break;
        }
      }

      if (!last_chunk && inbuf.pos == inbuf.size && outbuf.pos == 0) {
        break; // Need more input
      }
      if (last_chunk && r == 0) {
        break; // Finished
      }
    }

    if (err || last_chunk) {
      break;
    }
  }

  Py_END_ALLOW_THREADS

      ZSTD_freeCCtx(cctx);

  if (err) {
    if (!PyErr_Occurred())
      PyErr_SetString(comp_BackendError, "zstd compression failed");
    fclose(input);
    fclose(output);
    return -1;
  }

  fclose(input);
  fclose(output);
  return 0;
}

static int zstd_decompress_file(const char *input_path,
                                const char *output_path) {
  FILE *input = fopen(input_path, "rb");
  if (!input) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, input_path);
    return -1;
  }

  FILE *output = fopen(output_path, "wb");
  if (!output) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, output_path);
    fclose(input);
    return -1;
  }

  ZSTD_DStream *dstream = ZSTD_createDStream();
  if (!dstream) {
    PyErr_SetString(comp_BackendError, "Failed to create zstd context");
    fclose(input);
    fclose(output);
    return -1;
  }

  size_t ret = ZSTD_initDStream(dstream);
  if (ZSTD_isError(ret)) {
    PyErr_SetString(comp_BackendError,
                    "Failed to initialize zstd decompression");
    ZSTD_freeDStream(dstream);
    fclose(input);
    fclose(output);
    return -1;
  }

  unsigned char in_buf[ZSTD_FILE_CHUNK];
  unsigned char out_buf[ZSTD_FILE_CHUNK];
  int err = 0;

  Py_BEGIN_ALLOW_THREADS

      size_t input_size = 0;
  size_t input_pos = 0;

  for (;;) {
    if (input_pos == input_size) {
      input_size = fread(in_buf, 1, ZSTD_FILE_CHUNK, input);
      if (ferror(input)) {
        err = -1;
        break;
      }
      input_pos = 0;
      if (input_size == 0) {
        break; // End of input
      }
    }

    ZSTD_inBuffer inbuf = {in_buf + input_pos, input_size - input_pos, 0};
    ZSTD_outBuffer outbuf = {out_buf, ZSTD_FILE_CHUNK, 0};

    // The XXH64 content checksum is verified automatically as the frame ends
    size_t r = ZSTD_decompressStream(dstream, &outbuf, &inbuf);
    if (ZSTD_isError(r)) {
      err = -1;
      break;
    }

    input_pos += inbuf.pos;

    if (outbuf.pos > 0) {
      if (fwrite(out_buf, 1, outbuf.pos, output) != outbuf.pos ||
          ferror(output)) {
        err = -1;
        break;
      }
    }

    if (r == 0 && input_pos == input_size && feof(input)) {
      break; // Finished
    }
  }

  Py_END_ALLOW_THREADS

      ZSTD_freeDStream(dstream);

  if (err) {
    if (!PyErr_Occurred())
      PyErr_SetString(comp_BackendError,
                      "zstd decompression failed: corrupted or invalid data");
    fclose(input);
    fclose(output);
    return -1;
  }

  fclose(input);
  fclose(output);
  return 0;
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
