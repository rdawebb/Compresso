#define PY_SSIZE_T_CLEAN
#include "../common.h"
#include "../standalone.h"
#include <Python.h>
#include <lzma.h>
#include <stdio.h>
#include <string.h>

#define XZ_CHUNK 65536                                // 64KB
#define XZ_DECOMPRESS_MEMLIMIT (512ULL * 1024 * 1024) // 512MB

static uint32_t xz_level_to_preset(int level) {
  if (level < 0)
    level = 6; // Default
  if (level > 9)
    level = 9;
  return (uint32_t)level;
}

static int xz_compress_file(const char *input_path, const char *output_path,
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

  // lzma_easy_encoder emits the complete .xz container with an embedded
  // CRC64 integrity check
  lzma_stream strm = LZMA_STREAM_INIT;
  lzma_ret ret =
      lzma_easy_encoder(&strm, xz_level_to_preset(level), LZMA_CHECK_CRC64);
  if (ret != LZMA_OK) {
    PyErr_SetString(comp_BackendError, "Failed to initialize xz compression");
    fclose(input);
    fclose(output);
    return -1;
  }

  unsigned char in_buf[XZ_CHUNK];
  unsigned char out_buf[XZ_CHUNK];
  int return_code = 0;

  Py_BEGIN_ALLOW_THREADS

      lzma_action action = LZMA_RUN;

  while (1) {
    if (strm.avail_in == 0) {
      size_t nread = fread(in_buf, 1, XZ_CHUNK, input);
      if (ferror(input)) {
        return_code = -1; // Read error
        break;
      }
      strm.next_in = in_buf;
      strm.avail_in = nread;
      if (feof(input)) {
        action = LZMA_FINISH;
      }
    }

    strm.next_out = out_buf;
    strm.avail_out = XZ_CHUNK;

    ret = lzma_code(&strm, action);

    size_t write_size = XZ_CHUNK - strm.avail_out;
    if (write_size > 0) {
      if (fwrite(out_buf, 1, write_size, output) != write_size ||
          ferror(output)) {
        return_code = -1; // Write error
        break;
      }
    }

    if (ret == LZMA_STREAM_END) {
      break; // Finished
    }
    if (ret != LZMA_OK) {
      return_code = -1; // Compression error
      break;
    }
  }

  Py_END_ALLOW_THREADS

      lzma_end(&strm);

  if (return_code != 0) {
    if (!PyErr_Occurred())
      PyErr_SetString(comp_BackendError, "xz compression failed");
    fclose(input);
    fclose(output);
    return -1;
  }

  fclose(input);
  fclose(output);
  return 0;
}

static int xz_decompress_file(const char *input_path, const char *output_path) {
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

  lzma_stream strm = LZMA_STREAM_INIT;
  lzma_ret ret = lzma_stream_decoder(&strm, XZ_DECOMPRESS_MEMLIMIT, 0);
  if (ret != LZMA_OK) {
    PyErr_SetString(comp_BackendError, "Failed to initialize xz decompression");
    fclose(input);
    fclose(output);
    return -1;
  }

  unsigned char in_buf[XZ_CHUNK];
  unsigned char out_buf[XZ_CHUNK];
  int return_code = 0;
  lzma_ret final_ret = LZMA_OK;

  Py_BEGIN_ALLOW_THREADS

      lzma_action action = LZMA_RUN;

  while (1) {
    if (strm.avail_in == 0) {
      size_t nread = fread(in_buf, 1, XZ_CHUNK, input);
      if (ferror(input)) {
        return_code = -1; // Read error
        break;
      }
      strm.next_in = in_buf;
      strm.avail_in = nread;
      if (feof(input)) {
        action = LZMA_FINISH;
      }
    }

    strm.next_out = out_buf;
    strm.avail_out = XZ_CHUNK;

    ret = lzma_code(&strm, action);

    size_t write_size = XZ_CHUNK - strm.avail_out;
    if (write_size > 0) {
      if (fwrite(out_buf, 1, write_size, output) != write_size ||
          ferror(output)) {
        return_code = -1; // Write error
        break;
      }
    }

    if (ret == LZMA_STREAM_END) {
      break; // Finished, CRC64 verified by the decoder
    }
    if (ret != LZMA_OK) {
      return_code = -1; // Decompression / integrity error
      final_ret = ret;
      break;
    }
  }

  Py_END_ALLOW_THREADS

      lzma_end(&strm);

  if (return_code != 0) {
    if (!PyErr_Occurred()) {
      if (final_ret == LZMA_MEMLIMIT_ERROR) {
        PyErr_Format(comp_BackendError,
                     "xz decompression exceeded memory limit: %llu",
                     (unsigned long long)XZ_DECOMPRESS_MEMLIMIT);
      } else if (final_ret == LZMA_FORMAT_ERROR) {
        PyErr_SetString(comp_BackendError,
                        "xz format error: invalid compressed data");
      } else if (final_ret == LZMA_DATA_ERROR) {
        PyErr_SetString(comp_BackendError,
                        "xz data error: corrupted compressed data");
      } else {
        PyErr_SetString(comp_BackendError, "xz decompression failed");
      }
    }
    fclose(input);
    fclose(output);
    return -1;
  }

  fclose(input);
  fclose(output);
  return 0;
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
