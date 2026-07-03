/**
 * test_stubs.c - Test-harness definitions for symbols that live in _core.c.
 *
 * The real extension defines the comp_* exception objects in _core.c and
 * initializes them during module init (PyInit__core). The C test harness does
 * not link _core.c, because _core.c references the archive API (create_archive,
 * get_tar_archive, ...) which in turn pulls in the libarchive/libzip stack and
 * its platform-specific include/library paths.
 *
 * To keep the unit tests self-contained we provide the symbols here. They start
 * as NULL; tests that exercise error paths should call Py_Initialize() and then
 * create the exceptions (see ensure_comp_exceptions() usage in the standalone
 * tests) so that PyErr_SetString() has a valid exception type.
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>

PyObject *comp_Error = NULL;
PyObject *comp_HeaderError = NULL;
PyObject *comp_BackendError = NULL;
