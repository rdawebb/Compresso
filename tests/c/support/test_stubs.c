// Test-harness definitions for symbols that live in _core.c and archives/

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "test_stubs.h"
#include "archives.h"

const CompErrors *test_errors(void) {
  static CompErrors errors;
  if (errors.error) {
    return &errors;
  }
  errors.error = PyErr_NewException("compresso.Error", NULL, NULL);
  errors.header =
      PyErr_NewException("compresso.HeaderError", errors.error, NULL);
  errors.backend =
      PyErr_NewException("compresso.BackendError", errors.error, NULL);
  errors.corrupt_data =
      PyErr_NewException("compresso.CorruptDataError", errors.error, NULL);
  errors.cancelled =
      PyErr_NewException("compresso.Cancelled", errors.error, NULL);
  errors.extraction_policy =
      PyErr_NewException("compresso.ExtractionPolicyError", errors.error, NULL);
  errors.trailing_data_warning = PyErr_NewException(
      "compresso.TrailingDataWarning", PyExc_UserWarning, NULL);
  return &errors;
}

CoreContext *test_ctx(void) {
  static CoreContext ctx;
  ctx = (CoreContext){.errors = test_errors()};
  return &ctx;
}

// archives/{tar,zip}.c need libarchive/libzip, so the format table points at
// these instead, with just the fields validate.c reads, copied from there
const CArchive TAR_ARCHIVE = {.name = "tar", .levels = LEVELS_NONE};
const CArchive ZIP_ARCHIVE = {.name = "zip", .levels = LEVELS_ZLIB};
