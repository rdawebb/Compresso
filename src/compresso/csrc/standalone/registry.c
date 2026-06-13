#define PY_SSIZE_T_CLEAN
#include "../archives.h"
#include "../standalone.h"
#include <Python.h>

const StandaloneFormat *get_bzip2_format(void) { return NULL; }
const StandaloneFormat *get_xz_format(void) { return NULL; }
const StandaloneFormat *get_zstd_format(void) { return NULL; }
const StandaloneFormat *get_lz4_format(void) { return NULL; }

const StandaloneFormat *find_standalone_format(Format format) {
  switch (format) {
  case FORMAT_GZIP:  return get_gzip_format();
  case FORMAT_BZIP2: return get_bzip2_format();
  case FORMAT_XZ:    return get_xz_format();
  case FORMAT_ZSTD:  return get_zstd_format();
  case FORMAT_LZ4:   return get_lz4_format();
  default:           return NULL;
  }
}
