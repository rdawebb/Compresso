// Test-harness definitions for symbols that live in _core.c

#define PY_SSIZE_T_CLEAN
#include <Python.h>

PyObject *comp_Error = NULL;
PyObject *comp_HeaderError = NULL;
PyObject *comp_BackendError = NULL;
PyObject *comp_Cancelled = NULL;
