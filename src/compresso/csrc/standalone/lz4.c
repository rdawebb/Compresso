#define PY_SSIZE_T_CLEAN
#include "../common.h"
#include "../standalone.h"
#include <Python.h>
#include <lz4frame.h>
#include <string.h>

#define LZ4_FILE_CHUNK 65536 // 64KB
#define LZ4_OUT_CHUNK                                                          \
  (LZ4_FILE_CHUNK + LZ4_FILE_CHUNK / 255 + 16) // Worst-case LZ4 frame output

static int lz4_level_from_generic(int level) {
  if (level < 0)
    return 0; // Default
  return level;
}

static int lz4_compress_file(const char *input_path, const char *output_path,
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

  LZ4F_compressionContext_t cctx;
  size_t ret = LZ4F_createCompressionContext(&cctx, LZ4F_VERSION);
  if (LZ4F_isError(ret)) {
    PyErr_SetString(comp_BackendError, "Failed to create lz4 context");
    fclose(input);
    fclose(output);
    return -1;
  }

  LZ4F_preferences_t prefs;
  memset(&prefs, 0, sizeof(prefs));
  prefs.compressionLevel = lz4_level_from_generic(level);
  // Embed an xxHash content checksum so decompression verifies integrity
  prefs.frameInfo.contentChecksumFlag = LZ4F_contentChecksumEnabled;

  unsigned char in_buf[LZ4_FILE_CHUNK];
  unsigned char out_buf[LZ4_OUT_CHUNK];
  int return_code = 0;

  Py_BEGIN_ALLOW_THREADS

      size_t header_size =
          LZ4F_compressBegin(cctx, out_buf, LZ4_OUT_CHUNK, &prefs);
  if (LZ4F_isError(header_size)) {
    return_code = -1;
    goto done_stream;
  }
  if (fwrite(out_buf, 1, header_size, output) != header_size ||
      ferror(output)) {
    return_code = -1;
    goto done_stream;
  }

  for (;;) {
    size_t nread = fread(in_buf, 1, LZ4_FILE_CHUNK, input);
    if (ferror(input)) {
      return_code = -1; // Read error
      break;
    }

    if (nread == 0) {
      size_t end_size = LZ4F_compressEnd(cctx, out_buf, LZ4_OUT_CHUNK, NULL);
      if (LZ4F_isError(end_size)) {
        return_code = -1;
        break;
      }
      if (end_size > 0) {
        if (fwrite(out_buf, 1, end_size, output) != end_size ||
            ferror(output)) {
          return_code = -1;
        }
      }
      break; // End of file
    }

    size_t bytes =
        LZ4F_compressUpdate(cctx, out_buf, LZ4_OUT_CHUNK, in_buf, nread, NULL);
    if (LZ4F_isError(bytes)) {
      return_code = -1;
      break;
    }
    if (bytes > 0) {
      if (fwrite(out_buf, 1, bytes, output) != bytes || ferror(output)) {
        return_code = -1;
        break;
      }
    }
  }

done_stream:
  Py_END_ALLOW_THREADS

      LZ4F_freeCompressionContext(cctx);

  if (return_code != 0) {
    if (!PyErr_Occurred())
      PyErr_SetString(comp_BackendError, "lz4 compression failed");
    fclose(input);
    fclose(output);
    return -1;
  }

  fclose(input);
  fclose(output);
  return 0;
}

static int lz4_decompress_file(const char *input_path,
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

  LZ4F_decompressionContext_t dctx;
  size_t ret = LZ4F_createDecompressionContext(&dctx, LZ4F_VERSION);
  if (LZ4F_isError(ret)) {
    PyErr_SetString(comp_BackendError, "Failed to create lz4 context");
    fclose(input);
    fclose(output);
    return -1;
  }

  unsigned char in_buf[LZ4_FILE_CHUNK];
  unsigned char out_buf[LZ4_OUT_CHUNK];
  int return_code = 0;

  Py_BEGIN_ALLOW_THREADS

      size_t input_size = 0;
  size_t input_pos = 0;

  for (;;) {
    if (input_pos == input_size) {
      input_size = fread(in_buf, 1, LZ4_FILE_CHUNK, input);
      if (ferror(input)) {
        return_code = -1; // Read error
        break;
      }
      input_pos = 0;
      if (input_size == 0) {
        break; // End of file
      }
    }

    size_t src_size = input_size - input_pos;
    size_t dst_size = LZ4_OUT_CHUNK;

    // The content checksum is verified as the frame is consumed
    ret = LZ4F_decompress(dctx, out_buf, &dst_size, in_buf + input_pos,
                          &src_size, NULL);
    if (LZ4F_isError(ret)) {
      return_code = -1; // Decompression / checksum error
      break;
    }

    input_pos += src_size;

    if (dst_size > 0) {
      if (fwrite(out_buf, 1, dst_size, output) != dst_size || ferror(output)) {
        return_code = -1;
        break;
      }
    }

    if (ret == 0) {
      break; // Frame fully decoded
    }
  }

  Py_END_ALLOW_THREADS

      LZ4F_freeDecompressionContext(dctx);

  if (return_code != 0) {
    if (!PyErr_Occurred())
      PyErr_SetString(comp_BackendError,
                      "lz4 decompression failed: corrupted or invalid data");
    fclose(input);
    fclose(output);
    return -1;
  }

  fclose(input);
  fclose(output);
  return 0;
}

static char *lz4_get_original_name(const char *compressed_path) {
  (void)compressed_path; // .lz4 does not store the original filename
  return NULL;
}

static int lz4_is_format(const unsigned char *magic, size_t size) {
  static const unsigned char MAGIC_LZ4[] = {0x04, 0x22, 0x4D, 0x18};
  return (size >= 4 && memcmp(magic, MAGIC_LZ4, 4) == 0);
}

static const StandaloneFormat lz4_format = {
    .name = "lz4",
    .extension = ".lz4",
    .compress_file = lz4_compress_file,
    .decompress_file = lz4_decompress_file,
    .get_original_name = lz4_get_original_name,
    .is_format = lz4_is_format,
};

const StandaloneFormat *get_lz4_format(void) { return &lz4_format; }
