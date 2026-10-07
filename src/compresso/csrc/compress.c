#define PY_SSIZE_T_CLEAN
#include "archives.h"
#include "common.h"
#include "fsutil.h"
#include "standalone.h"
#include <Python.h>
#include <string.h>

static int decompress_compresso_file(const char *src_path,
                                     const OutputTarget *out, AlgoID algo,
                                     CoreContext *ctx);

// ---- Public API ----

int compress_file(const char *src_path, const char *dst_path, AlgoID algo,
                  Strategy strategy, int level, int overwrite_existing,
                  char *out_actual_path, size_t out_actual_path_size,
                  CoreContext *ctx) {
  init_backends();

  OutputTarget out = {.path = dst_path,
                      .overwrite = overwrite_existing,
                      .actual = out_actual_path,
                      .actual_size = out_actual_path_size,
                      .keep_source_metadata = 1};
  int checked = output_check(src_path, &out);
  if (checked != 0) {
    return checked < 0 ? -1 : 0;
  }

  const CBackend *backend = NULL;
  if (algo != ALGO_NONE) {
    backend = find_backend_by_id(algo);
    if (!backend) {
      PyErr_SetString(PyExc_ValueError,
                      "Specified compression algorithm not available");
      return -1;
    }
  } else {
    backend = choose_backend(strategy);
    if (!backend) {
      PyErr_SetString(comp_Error, "No available compression backend found");
      return -1;
    }
  }

  int return_code = 0;
  FILE *dst = NULL;
  char temp[FS_PATH_MAX] = "";

  FILE *src = fs_fopen(src_path, "rb");
  if (!src) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, src_path);
    return -1;
  }

  int64_t len = fs_stream_size(src);
  if (len < 0) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, src_path);
    return_code = -1;
    goto done;
  }

  dst = output_open(&out, temp);
  if (!dst) {
    return_code = -1;
    goto done;
  }

  CHeader header;
  memcpy(header.magic, C_MAGIC, C_MAGIC_LEN);
  header.version = 1;
  header.algo = backend->id;
  header.level = (uint8_t)((level >= 0 && level <= 254) ? level : 255);
  header.flags = backend->checksummed ? C_FLAG_CHECKSUMMED : 0;
  header.orig_size = (uint64_t)len;

  uint8_t header_buf[C_HEADER_SIZE];
  c_header_pack(&header, header_buf);

  if (fwrite(header_buf, 1, sizeof(header_buf), dst) != sizeof(header_buf) ||
      ferror(dst)) {
    PyErr_SetString(comp_HeaderError, "Failed to write header to output file");
    return_code = -1;
    goto done;
  }

  // fs_stream_size left src at the start, so the stage covers the whole input
  ctx_begin_stage_stream(ctx, src);

  return_code = backend->compress_stream(src, dst, level, ctx);
  if (return_code != 0) {
    if (return_code != COMP_CANCELLED && !PyErr_Occurred()) {
      set_backend_error(backend, "compression", "streaming compression");
    }
  }

done:
  return output_finish(return_code, src, dst, temp, &out, NULL);
}

int decompress_file(const char *src_path, const char *dst_path, AlgoID algo,
                    int overwrite_existing, char *out_actual_path,
                    size_t out_actual_path_size, CoreContext *ctx) {
  init_backends();

  if (check_source_readable(src_path) != 0) {
    return -1;
  }

  Format format = detect_format_from_path(src_path);
  if (format == FORMAT_UNKNOWN) {
    PyErr_SetString(comp_Error, "Unknown or unsupported format");
    return -1;
  }

  OutputTarget out = {.path = dst_path,
                      .overwrite = overwrite_existing,
                      .actual = out_actual_path,
                      .actual_size = out_actual_path_size,
                      .keep_source_metadata = 1};

  // The standalone formats each open and size their own input
  const StandaloneFormat *standalone = find_standalone_format(format);
  if (standalone) {
    return standalone->decompress_file(src_path, &out, ctx);
  }

  if (format_is_archive(format)) {
    PyErr_SetString(comp_Error,
                    "Use archive decompression API for archive formats");
    return -1;
  }

  if (format == FORMAT_COMPRESSO) {
    return decompress_compresso_file(src_path, &out, algo, ctx);
  }

  PyErr_Format(comp_Error, "Unknown or unsupported format: %s",
               format_name_string(format));
  return -1;
}

static int decompress_compresso_file(const char *src_path,
                                     const OutputTarget *out, AlgoID algo,
                                     CoreContext *ctx) {
  init_backends();

  int checked = output_check(src_path, out);
  if (checked != 0) {
    return checked < 0 ? -1 : 0;
  }

  int return_code = 0;
  FILE *dst = NULL;
  char temp[FS_PATH_MAX] = "";

  FILE *src = fs_fopen(src_path, "rb");
  if (!src) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, src_path);
    return -1;
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

  const CBackend *backend = find_backend_by_id(header.algo);
  if (!backend) {
    PyErr_SetString(comp_HeaderError,
                    "Compression algorithm from file not available");
    return_code = -1;
    goto done;
  }
  if (algo != ALGO_NONE && algo != header.algo) {
    const CBackend *requested = find_backend_by_id(algo);
    PyErr_Format(PyExc_ValueError,
                 "File was compressed with %s, not the requested %s",
                 backend->name, requested ? requested->name : "algorithm");
    return_code = -1;
    goto done;
  }

  uint64_t orig_size = header.orig_size;

  // Only once the header is known good, so a bad one creates nothing
  dst = output_open(out, temp);
  if (!dst) {
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
  return output_finish(return_code, src, dst, temp, out, NULL);
}
