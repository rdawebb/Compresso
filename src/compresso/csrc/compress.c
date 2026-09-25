#define PY_SSIZE_T_CLEAN
#include "archives.h"
#include "common.h"
#include "fsutil.h"
#include "standalone.h"
#include <Python.h>
#include <errno.h>
#include <string.h>

static int decompress_compresso_file(const char *src_path, const char *dst_path,
                                     AlgoID algo, CoreContext *ctx);

// ---- Public API ----

int compress_file(const char *src_path, const char *dst_path, AlgoID algo,
                  Strategy strategy, int level, int overwrite_existing,
                  char *out_actual_path, size_t out_actual_path_size,
                  CoreContext *ctx) {
  init_backends();

  const CBackend *backend = NULL;

  int return_code = 0;
  FILE *src = NULL;
  FILE *dst = NULL;

  // Resolved before anything is opened, so ERROR/SKIP never touch the
  // existing destination; must `return` directly rather than `goto done`,
  // since codec_finish_file() below unconditionally unlinks dst_path on
  // failure, which would delete the file these modes are protecting
  char resolved_dst[FS_PATH_MAX];
  int resolve_rc = fs_resolve_conflict(dst_path, overwrite_existing,
                                       resolved_dst, sizeof(resolved_dst));
  if (resolve_rc < 0) {
    if (errno == ENAMETOOLONG) {
      PyErr_Format(PyExc_ValueError, "Output path too long: %s", dst_path);
    } else {
      PyErr_SetFromErrnoWithFilename(PyExc_OSError, dst_path);
    }
    return -1;
  }
  if (resolve_rc > 0) { // SKIP: leave dst_path untouched
    if (out_actual_path) {
      size_t len = strlen(dst_path);
      if (len >= out_actual_path_size)
        len = out_actual_path_size - 1;
      memcpy(out_actual_path, dst_path, len);
      out_actual_path[len] = '\0';
    }
    return 0;
  }
  dst_path = resolved_dst;

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

  src = fs_fopen(src_path, "rb");
  if (!src) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, src_path);
    return_code = -1;
    goto done;
  }

  dst = fs_fopen(dst_path, "wb");
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

  uint8_t header_buf[C_HEADER_SIZE];
  c_header_pack(&header, header_buf);

  if (fwrite(header_buf, 1, sizeof(header_buf), dst) != sizeof(header_buf) ||
      ferror(dst)) {
    PyErr_SetString(comp_HeaderError, "Failed to write header to output file");
    return_code = -1;
    goto done;
  }

  // src is rewound to the start, so the stage covers the whole input
  ctx_begin_stage_stream(ctx, src);

  return_code = backend->compress_stream(src, dst, level, ctx);
  if (return_code != 0) {
    if (return_code != COMP_CANCELLED && !PyErr_Occurred()) {
      set_backend_error(backend, "compression", "streaming compression");
    }
  }

done:
  return_code = codec_finish_file(return_code, src, dst, dst_path, NULL);
  if (return_code == 0 && out_actual_path) {
    size_t len = strlen(dst_path);
    if (len >= out_actual_path_size)
      len = out_actual_path_size - 1;
    memcpy(out_actual_path, dst_path, len);
    out_actual_path[len] = '\0';
  }
  return return_code;
}

int decompress_file(const char *src_path, const char *dst_path, AlgoID algo,
                    CoreContext *ctx) {
  init_backends();

  Format format = detect_format_from_path(src_path);
  if (format == FORMAT_UNKNOWN) {
    PyErr_SetString(comp_Error, "Unknown or unsupported format");
    return -1;
  }

  // The standalone formats each open and size their own input
  const StandaloneFormat *standalone = find_standalone_format(format);
  if (standalone) {
    return standalone->decompress_file(src_path, dst_path, ctx);
  }

  if (format_is_archive(format)) {
    PyErr_SetString(comp_Error,
                    "Use archive decompression API for archive formats");
    return -1;
  }

  if (format == FORMAT_COMPRESSO) {
    return decompress_compresso_file(src_path, dst_path, algo, ctx);
  }

  PyErr_Format(comp_Error, "Unknown or unsupported format: %s",
               format_name_string(format));
  return -1;
}

static int decompress_compresso_file(const char *src_path, const char *dst_path,
                                     AlgoID algo, CoreContext *ctx) {
  init_backends();

  int return_code = 0;
  FILE *src = NULL;
  FILE *dst = NULL;

  src = fs_fopen(src_path, "rb");
  if (!src) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, src_path);
    return_code = -1;
    goto done;
  }

  dst = fs_fopen(dst_path, "wb");
  if (!dst) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, dst_path);
    return_code = -1;
    goto done;
  }

  uint8_t header_buf[C_HEADER_SIZE];
  if (fread(header_buf, 1, sizeof(header_buf), src) != sizeof(header_buf)) {
    PyErr_SetString(comp_HeaderError, "Failed to read header from input file");
    return_code = -1;
    goto done;
  }

  CHeader header;
  c_header_unpack(header_buf, &header);

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

  // Progress counts input bytes consumed
  ctx_begin_stage_stream(ctx, src);

  return_code = backend->decompress_stream(src, dst, orig_size, ctx);
  if (return_code != 0) {
    if (return_code != COMP_CANCELLED && !PyErr_Occurred()) {
      set_backend_error(backend, "decompression", "streaming decompression");
    }
  }

done:
  return codec_finish_file(return_code, src, dst, dst_path, NULL);
}
