#define PY_SSIZE_T_CLEAN
#include "archives.h"
#include "common.h"
#include <Python.h>
#include <string.h>

static int decompress_compresso_file(const char *src_path, const char *dst_path,
                                     AlgoID algo);

// ---- I/O Helpers ----

static unsigned char *__attribute__((unused))
read_file_to_memory(const char *path, size_t *out_size) {
  FILE *f = fopen(path, "rb");
  if (!f) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, path);
    return NULL;
  }

#if defined(_WIN32) || defined(_WIN64)

  if (_fseeki64(f, 0, SEEK_END) != 0) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, path);
    fclose(f);
    return NULL;
  }

  __int64 len = _ftelli64(f);
  if (len < 0) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, path);
    fclose(f);
    return NULL;
  }

  if (len > (size_t)-1) {
    PyErr_SetString(PyExc_MemoryError, "File is too large to fit in memory");
    fclose(f);
    return NULL;
  }

  if (_fseeki64(f, 0, SEEK_SET) != 0) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, path);
    fclose(f);
    return NULL;
  }

#else

  if (fseeko(f, 0, SEEK_END) != 0) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, path);
    fclose(f);
    return NULL;
  }

  off_t len = ftello(f);
  if (len < 0) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, path);
    fclose(f);
    return NULL;
  }

  if ((uintmax_t)len > SIZE_MAX) {
    PyErr_SetString(PyExc_MemoryError, "File is too large to fit in memory");
    fclose(f);
    return NULL;
  }

  if (fseeko(f, 0, SEEK_SET) != 0) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, path);
    fclose(f);
    return NULL;
  }

#endif

  if (validate_size((uint64_t)len, MAX_FILE_SIZE, "Input file size") != 0) {
    fclose(f);
    return NULL;
  }

  unsigned char *buffer = (unsigned char *)safe_malloc((size_t)len);
  if (!buffer) {
    fclose(f);
    return NULL;
  }

  size_t read = fread(buffer, 1, (size_t)len, f);
  fclose(f);

  if (read != (size_t)len) {
    free(buffer);
    PyErr_SetString(PyExc_IOError, "Failed to read entire file");
    return NULL;
  }

  *out_size = (size_t)len;
  return buffer;
}

static int __attribute__((unused))
write_memory_to_file(const char *path, const unsigned char *data, size_t size) {
  FILE *f = fopen(path, "wb");
  if (!f) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, path);
    return -1;
  }

  size_t written = fwrite(data, 1, size, f);
  int err = ferror(f);
  fclose(f);

  if (err || written != size) {
    PyErr_SetString(PyExc_IOError, "Failed to write entire file");
    return -1;
  }

  return 0;
}

// ---- Public API ----

int compress_file(const char *src_path, const char *dst_path, AlgoID algo,
                  Strategy strategy, int level) {
  init_backends();

  const CBackend *backend = NULL;

  int return_code = 0;
  FILE *src = NULL;
  FILE *dst = NULL;

  if (algo != ALGO_NONE) {
    backend = find_backend_by_id(algo);
    if (!backend) {
      PyErr_SetString(PyExc_ValueError,
                      "Specified compression algorithm not available");
      return_code = -1;
      goto done;
    }
  } else {
    backend = choose_backend(strategy);
    if (!backend) {
      PyErr_SetString(comp_Error, "No available compression backend found");
      return_code = -1;
      goto done;
    }
  }

  src = fopen(src_path, "rb");
  if (!src) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, src_path);
    return_code = -1;
    goto done;
  }

  dst = fopen(dst_path, "wb");
  if (!dst) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, dst_path);
    return_code = -1;
    goto done;
  }

#if defined(_WIN32) || defined(_WIN64)

  if (_fseeki64(src, 0, SEEK_END) != 0) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, src_path);
    return_code = -1;
    goto done;
  }

  __int64 len = _ftelli64(src);
  if (len < 0) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, src_path);
    return_code = -1;
    goto done;
  }

  if (validate_size((uint64_t)len, MAX_FILE_SIZE, "Input file size") != 0) {
    return_code = -1;
    goto done;
  }

  if (_fseeki64(src, 0, SEEK_SET) != 0) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, src_path);
    return_code = -1;
    goto done;
  }

#else

  if (fseeko(src, 0, SEEK_END) != 0) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, src_path);
    return_code = -1;
    goto done;
  }

  off_t len = ftello(src);
  if (len < 0) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, src_path);
    return_code = -1;
    goto done;
  }

  if (validate_size((uint64_t)len, MAX_FILE_SIZE, "Input file size") != 0) {
    return_code = -1;
    goto done;
  }

  if (fseeko(src, 0, SEEK_SET) != 0) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, src_path);
    return_code = -1;
    goto done;
  }

#endif

  CHeader header;
  memcpy(header.magic, C_MAGIC, C_MAGIC_LEN);
  header.version = 1;
  header.algo = backend->id;
  header.level = (uint8_t)((level >= 0 && level <= 254) ? level : 255);
  header.flags = 0;
  header.orig_size = (uint64_t)len;

  if (fwrite(&header, 1, sizeof(header), dst) != sizeof(header) ||
      ferror(dst)) {
    PyErr_SetString(comp_HeaderError, "Failed to write header to output file");
    return_code = -1;
    goto done;
  }

  if (backend->compress_stream) {
    return_code = backend->compress_stream(src, dst, level);
    if (return_code != 0) {
      set_backend_error(backend, "compression", "streaming compression");
      goto done;
    }
  } else {
    size_t input_size = (size_t)len;
    unsigned char *input_buffer = (unsigned char *)safe_malloc(input_size);
    if (!input_buffer) {
      return_code = -1;
      goto done;
    }

    size_t read = fread(input_buffer, 1, input_size, src);
    if (read != input_size || ferror(src)) {
      free(input_buffer);
      PyErr_SetString(PyExc_IOError, "Failed to read input file");
      return_code = -1;
      goto done;
    }

    size_t max_payload = backend->max_compressed_size(input_size);
    if (max_payload == SIZE_MAX) {
      free(input_buffer);
      PyErr_SetString(PyExc_OverflowError,
                      "Compressed size calculation overflow");
      return_code = -1;
      goto done;
    }

    unsigned char *output_buffer = (unsigned char *)safe_malloc(max_payload);
    if (!output_buffer) {
      free(input_buffer);
      return_code = -1;
      goto done;
    }

    size_t output_size = 0;
    if (backend->compress_buffer(input_buffer, input_size, output_buffer,
                                 &max_payload, level, &output_size) != 0) {
      free(input_buffer);
      free(output_buffer);
      set_backend_error(backend, "compression", "buffer compression");
      return_code = -1;
      goto done;
    }

    free(input_buffer);

    if (fwrite(output_buffer, 1, output_size, dst) != output_size ||
        ferror(dst)) {
      free(output_buffer);
      PyErr_SetString(PyExc_IOError,
                      "Failed to write compressed data to output file");
      return_code = -1;
      goto done;
    }

    free(output_buffer);
  }

done:
  if (src)
    fclose(src);
  if (dst)
    fclose(dst);
  return return_code;
}

int decompress_file(const char *src_path, const char *dst_path, AlgoID algo) {
  init_backends();

  Format format = detect_format_from_path(src_path);
  if (format == FORMAT_UNKNOWN) {
    PyErr_SetString(comp_Error, "Unknown or unsupported format");
    return -1;
  }

  if (format == FORMAT_GZIP || format == FORMAT_BZIP2 || format == FORMAT_XZ ||
      format == FORMAT_ZSTD || format == FORMAT_LZ4) {
    const StandaloneFormat *standalone = NULL;

    switch (format) {
    case FORMAT_GZIP:
      standalone = get_gzip_format();
      break;
    case FORMAT_BZIP2:
      standalone = get_bzip2_format();
      break;
    case FORMAT_XZ:
      standalone = get_xz_format();
      break;
    case FORMAT_ZSTD:
      standalone = get_zstd_format();
      break;
    case FORMAT_LZ4:
      standalone = get_lz4_format();
      break;
    default:
      break;
    }

    if (!standalone) {
      PyErr_SetString(comp_Error, "Standalone format backend not available");
      return -1;
    }

    return standalone->decompress_file(src_path, dst_path);
  }

  if (format_is_archive(format)) {
    PyErr_SetString(comp_Error,
                    "Use archive decompression API for archive formats");
    return -1;
  }

  if (format == FORMAT_COMPRESSO) {
    return decompress_compresso_file(src_path, dst_path, algo);
  }

  PyErr_Format(comp_Error, "Unknown or unsupported format: %s",
               format_name_string(format));
  return -1;
}

static int decompress_compresso_file(const char *src_path, const char *dst_path,
                                     AlgoID algo) {
  init_backends();

  int return_code = 0;
  FILE *src = NULL;
  FILE *dst = NULL;

  src = fopen(src_path, "rb");
  if (!src) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, src_path);
    return_code = -1;
    goto done;
  }

  dst = fopen(dst_path, "wb");
  if (!dst) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, dst_path);
    return_code = -1;
    goto done;
  }

  CHeader header;
  if (fread(&header, 1, sizeof(header), src) != sizeof(header)) {
    PyErr_SetString(comp_HeaderError, "Failed to read header from input file");
    return_code = -1;
    goto done;
  }

  if (memcmp(header.magic, C_MAGIC, C_MAGIC_LEN) != 0) {
    PyErr_SetString(comp_HeaderError, "Invalid file magic number");
    return_code = -1;
    goto done;
  }

  if (header.version != 1) {
    PyErr_SetString(comp_HeaderError, "Unsupported file version");
    return_code = -1;
    goto done;
  }

  const CBackend *backend = NULL;

  if (algo != ALGO_NONE) {
    backend = find_backend_by_id(algo);
    if (!backend) {
      PyErr_SetString(comp_BackendError,
                      "Specified compression algorithm not available");
      return_code = -1;
      goto done;
    }
  } else {
    backend = find_backend_by_id(header.algo);
    if (!backend) {
      PyErr_SetString(comp_HeaderError,
                      "Compression algorithm from file not available");
      return_code = -1;
      goto done;
    }
  }
  uint64_t orig_size = header.orig_size;
  if (validate_size(orig_size, MAX_DECOMPRESSED_SIZE,
                    "Original file size in header") != 0) {
    return_code = -1;
    goto done;
  }

  if (backend->decompress_stream) {
    return_code = backend->decompress_stream(src, dst, orig_size);
    if (return_code != 0) {
      set_backend_error(backend, "decompression", "streaming decompression");
      goto done;
    }
  } else {

#if defined(_WIN32) || defined(_WIN64)

    if (_fseeki64(src, 0, SEEK_END) != 0) {
      PyErr_SetFromErrnoWithFilename(PyExc_OSError, src_path);
      return_code = -1;
      goto done;
    }

    __int64 end_pos = _ftelli64(src);
    if (end_pos < 0) {
      PyErr_SetFromErrnoWithFilename(PyExc_OSError, src_path);
      return_code = -1;
      goto done;
    }

    __int64 payload_start = (__int64)sizeof(CHeader);
    __int64 payload_len = end_pos - payload_start;
    if (payload_len <= 0) {
      PyErr_SetString(PyExc_ValueError, "No compressed data found in file");
      return_code = -1;
      goto done;
    }

    if (validate_size(payload_len, MAX_COMPRESSED_SIZE,
                      "Compressed data size") != 0) {
      return_code = -1;
      goto done;
    }

    if (_fseeki64(src, payload_start, SEEK_SET) != 0) {
      PyErr_SetFromErrnoWithFilename(PyExc_OSError, src_path);
      return_code = -1;
      goto done;
    }

#else

    if (fseeko(src, 0, SEEK_END) != 0) {
      PyErr_SetFromErrnoWithFilename(PyExc_OSError, src_path);
      return_code = -1;
      goto done;
    }

    off_t end_pos = ftello(src);
    if (end_pos < 0) {
      PyErr_SetFromErrnoWithFilename(PyExc_OSError, src_path);
      return_code = -1;
      goto done;
    }

    off_t payload_start = (off_t)sizeof(CHeader);
    off_t payload_len = end_pos - payload_start;
    if (payload_len <= 0) {
      PyErr_SetString(PyExc_ValueError, "No compressed data found in file");
      return_code = -1;
      goto done;
    }

    if (validate_size(payload_len, MAX_COMPRESSED_SIZE,
                      "Compressed data size") != 0) {
      return_code = -1;
      goto done;
    }

    if (fseeko(src, payload_start, SEEK_SET) != 0) {
      PyErr_SetFromErrnoWithFilename(PyExc_OSError, src_path);
      return_code = -1;
      goto done;
    }

#endif

    if (payload_len > (long)SIZE_MAX) {
      PyErr_SetString(PyExc_MemoryError,
                      "Compressed data too large to fit in memory");
      return_code = -1;
      goto done;
    }

    size_t comp_size = (size_t)payload_len;
    unsigned char *comp_buffer = (unsigned char *)safe_malloc(comp_size);
    if (!comp_buffer) {
      return_code = -1;
      goto done;
    }
    size_t read = fread(comp_buffer, 1, comp_size, src);
    if (read != comp_size || ferror(src)) {
      free(comp_buffer);
      PyErr_SetString(PyExc_IOError,
                      "Failed to read compressed data from input file");
      return_code = -1;
      goto done;
    }

    size_t output_capacity;
    if (backend->id == ALGO_SNAPPY) {
      output_capacity = snappy_decompressed_size(comp_buffer, comp_size);
      if (output_capacity == 0) {
        free(comp_buffer);
        PyErr_SetString(PyExc_RuntimeError,
                        "Failed to determine decompressed size for Snappy");
        return_code = -1;
        goto done;
      }
    } else {
      if (validate_size(orig_size, SIZE_MAX, "Original size") != 0) {
        free(comp_buffer);
        return_code = -1;
        goto done;
      }
      output_capacity = (size_t)orig_size;
    }
    unsigned char *output_buffer =
        (unsigned char *)safe_malloc(output_capacity);
    if (!output_buffer) {
      free(comp_buffer);
      return_code = -1;
      goto done;
    }

    size_t output_size = 0;
    if (backend->decompress_buffer(comp_buffer, comp_size, output_buffer,
                                   &output_capacity, &output_size) != 0 ||
        output_size != (size_t)orig_size) {
      free(comp_buffer);
      free(output_buffer);
      set_backend_error(backend, "decompression", "buffer decompression");
      return_code = -1;
      goto done;
    }

    free(comp_buffer);

    if (fwrite(output_buffer, 1, output_size, dst) != output_size ||
        ferror(dst)) {
      free(output_buffer);
      PyErr_SetString(PyExc_IOError,
                      "Failed to write decompressed data to output file");
      return_code = -1;
      goto done;
    }

    free(output_buffer);
  }

done:
  if (src)
    fclose(src);
  if (dst)
    fclose(dst);
  return return_code;
}
