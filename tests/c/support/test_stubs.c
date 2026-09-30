// Test-harness definitions for symbols that live in _core.c and archives.c

#define PY_SSIZE_T_CLEAN
#include "archives.h"
#include <Python.h>

PyObject *comp_Error = NULL;
PyObject *comp_HeaderError = NULL;
PyObject *comp_BackendError = NULL;
PyObject *comp_Cancelled = NULL;
PyObject *comp_TrailingDataWarning = NULL;

// archives.c needs libarchive/libzip, so validate.c's lookup is answered here
// with just the fields it reads, copied from archives/{tar,zip}.c
static const CArchive stub_tar = {
    .name = "tar", .id = ARCHIVE_TAR, .levels = LEVELS_NONE};
static const CArchive stub_zip = {
    .name = "zip", .id = ARCHIVE_ZIP, .levels = LEVELS_ZLIB};

const CArchive *find_archive_by_id(uint8_t id) {
  switch (id) {
  case ARCHIVE_TAR:
    return &stub_tar;
  case ARCHIVE_ZIP:
    return &stub_zip;
  default:
    return NULL;
  }
}
