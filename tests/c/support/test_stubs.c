// Test-harness definitions for symbols that live in _core.c and archives/

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
      PyErr_NewException("compresso.CorruptDataError", comp_Error, NULL);
  comp_Cancelled = PyErr_NewException("compresso.Cancelled", comp_Error, NULL);
  comp_ExtractionPolicyError =
      PyErr_NewException("compresso.ExtractionPolicyError", comp_Error, NULL);
  comp_TrailingDataWarning = PyErr_NewException("compresso.TrailingDataWarning",
                                                PyExc_UserWarning, NULL);
}

// archives/{tar,zip}.c need libarchive/libzip, so the format table points at
// these instead, with just the fields validate.c reads, copied from there
const CArchive TAR_ARCHIVE = {
    .name = "tar", .levels = LEVELS_NONE};
const CArchive ZIP_ARCHIVE = {
    .name = "zip", .levels = LEVELS_ZLIB};
