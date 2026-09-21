#define PY_SSIZE_T_CLEAN
#include "../archives.h"
#include "../fsutil.h"
#include "../standalone.h"
#include <Python.h>
#include <errno.h>
#include <string.h>

const StandaloneFormat *find_standalone_format(Format format) {
  switch (format) {
  case FORMAT_GZIP:
    return get_gzip_format();
  case FORMAT_BZIP2:
    return get_bzip2_format();
  case FORMAT_XZ:
    return get_xz_format();
  case FORMAT_ZSTD:
    return get_zstd_format();
  case FORMAT_LZ4:
    return get_lz4_format();
  default:
    return NULL;
  }
}

int compress_standalone_file(const StandaloneFormat *fmt,
                             const char *input_path, const char *output_path,
                             int level, int overwrite_existing,
                             char *out_actual_path, size_t out_actual_path_size,
                             CoreContext *ctx) {
  char resolved[FS_PATH_MAX];
  int rc = fs_resolve_conflict(output_path, overwrite_existing, resolved,
                               sizeof(resolved));
  if (rc < 0) {
    if (errno == ENAMETOOLONG) {
      PyErr_Format(PyExc_ValueError, "Output path too long: %s", output_path);
    } else {
      PyErr_SetFromErrnoWithFilename(PyExc_OSError, output_path);
    }
    return -1;
  }
  if (rc > 0) { // SKIP: leave output_path untouched
    if (out_actual_path) {
      size_t len = strlen(output_path);
      if (len >= out_actual_path_size)
        len = out_actual_path_size - 1;
      memcpy(out_actual_path, output_path, len);
      out_actual_path[len] = '\0';
    }
    return 0;
  }

  if (fmt->compress_file(input_path, resolved, level, ctx) != 0)
    return -1;

  if (out_actual_path) {
    size_t len = strlen(resolved);
    if (len >= out_actual_path_size)
      len = out_actual_path_size - 1;
    memcpy(out_actual_path, resolved, len);
    out_actual_path[len] = '\0';
  }
  return 0;
}
