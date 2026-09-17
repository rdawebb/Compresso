#define PY_SSIZE_T_CLEAN
#include "../common.h"
#include "../fsutil.h"
#include "../standalone.h"
#include <Python.h>
#include <bzlib.h>
#include <stdio.h>

#define BZIP2_CHUNK 65536 // 64KB

static int bzip2_block_size_from_level(int level) {
  if (level <= 0)
    return 9; // Default: max compression
  if (level > 9)
    return 9;
  return level;
}

static int bzip2_compress_file(const char *input_path, const char *output_path,
                               int level, CoreContext *ctx) {
  FILE *input = fs_fopen(input_path, "rb");
  if (!input) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, input_path);
    return -1;
  }

  ctx_begin_stage_stream(ctx, input);

  FILE *output = fs_fopen(output_path, "wb");
  if (!output) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, output_path);
    fclose(input);
    return -1;
  }

  int bzerr;
  int blockSize100k = bzip2_block_size_from_level(level);

  // The .bz2 frame (BZh header + per-block CRC32s) is produced by libbz2
  BZFILE *bzf = BZ2_bzWriteOpen(&bzerr, output, blockSize100k, 0,
                                30 // Verbosity and workFactor (default)
  );
  if (bzerr != BZ_OK || bzf == NULL) {
    PyErr_SetString(comp_BackendError,
                    "Failed to initialize bzip2 compression");
    fclose(input);
    fclose(output);
    return -1;
  }

  unsigned char buffer[BZIP2_CHUNK];
  int ret = 0;

  Py_BEGIN_ALLOW_THREADS

      for (;;) {
    size_t nread = fread(buffer, 1, BZIP2_CHUNK, input);
    if (ferror(input)) {
      ret = -1; // Read error
      break;
    }

    int advance = ctx_advance(ctx, nread);
    if (advance != 0) {
      ret = advance;
      break;
    }

    if (nread > 0) {
      BZ2_bzWrite(&bzerr, bzf, buffer, (int)nread);
      if (bzerr != BZ_OK) {
        ret = -1; // Write error
        break;
      }
    }

    if (feof(input)) {
      break; // End of file
    }
  }

  Py_END_ALLOW_THREADS

      int close_err;
  // Abandon rather than flush when the run failed or was cancelled
  BZ2_bzWriteClose(&close_err, bzf, ret != 0, NULL, NULL);
  if (ret == 0 && close_err != BZ_OK) {
    ret = -1; // Error finalising stream
  }

  return codec_finish_file(ret, input, output, output_path,
                           "bzip2 compression failed");
}

static int bzip2_decompress_file(const char *input_path,
                                 const char *output_path, CoreContext *ctx) {
  FILE *input = fs_fopen(input_path, "rb");
  if (!input) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, input_path);
    return -1;
  }

  ctx_begin_stage_stream(ctx, input);

  FILE *output = fs_fopen(output_path, "wb");
  if (!output) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, output_path);
    fclose(input);
    return -1;
  }

  int bzerr;
  BZFILE *bzf = BZ2_bzReadOpen(&bzerr, input, 0, 0, // Small and verbosity flags
                               NULL, 0);
  if (bzerr != BZ_OK || bzf == NULL) {
    PyErr_SetString(comp_BackendError,
                    "Failed to initialize bzip2 decompression");
    fclose(input);
    fclose(output);
    return -1;
  }

  unsigned char buffer[BZIP2_CHUNK];
  int ret = 0;
  int saved_err = BZ_OK;

  Py_BEGIN_ALLOW_THREADS

      for (;;) {
    int nread = BZ2_bzRead(&bzerr, bzf, buffer, BZIP2_CHUNK);
    if (bzerr == BZ_OK || bzerr == BZ_STREAM_END) {
      // libbz2 verifies the per-block CRC32 as it reads
      if (nread > 0) {
        if (fwrite(buffer, 1, nread, output) != (size_t)nread ||
            ferror(output)) {
          ret = -1; // Write error
          break;
        }
      }
      if (bzerr == BZ_STREAM_END) {
        break; // End of stream
      }

      // BZ2_bzRead does the reading, so no per-chunk input delta to report
      int advance = ctx_set_position(ctx, (uint64_t)fs_ftell(input));
      if (advance != 0) {
        ret = advance;
        break;
      }
    } else {
      ret = -1; // Read / CRC / Data error
      break;
    }
  }

  saved_err = bzerr;
  BZ2_bzReadClose(&bzerr, bzf);

  Py_END_ALLOW_THREADS

      const char *message =
          (saved_err == BZ_DATA_ERROR || saved_err == BZ_DATA_ERROR_MAGIC)
              ? "bzip2 data error: corrupted or invalid compressed data"
              : "bzip2 decompression failed";

  return codec_finish_file(ret, input, output, output_path, message);
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
