// Test-harness definitions for symbols that live in _core.c and archives.c

#define PY_SSIZE_T_CLEAN
#include "test_stubs.h"
#include "archives.h"
#include <Python.h>

PyObject *comp_Error = NULL;
PyObject *comp_HeaderError = NULL;
PyObject *comp_BackendError = NULL;
PyObject *comp_CorruptDataError = NULL;
PyObject *comp_ExtractionPolicyError = NULL;
PyObject *comp_Cancelled = NULL;
PyObject *comp_TrailingDataWarning = NULL;

void ensure_comp_exceptions(void) {
  if (comp_Error) {
    return;
  }
  comp_Error = PyErr_NewException("compresso.Error", NULL, NULL);
  comp_HeaderError =
      PyErr_NewException("compresso.HeaderError", comp_Error, NULL);
  comp_BackendError =
      PyErr_NewException("compresso.BackendError", comp_Error, NULL);
  comp_CorruptDataError =
      PyErr_NewException("compresso.CorruptDataError", comp_BackendError, NULL);
  comp_Cancelled = PyErr_NewException("compresso.Cancelled", comp_Error, NULL);
  comp_ExtractionPolicyError =
      PyErr_NewException("compresso.ExtractionPolicyError", comp_Error, NULL);
  comp_TrailingDataWarning = PyErr_NewException("compresso.TrailingDataWarning",
                                                PyExc_UserWarning, NULL);
}

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
