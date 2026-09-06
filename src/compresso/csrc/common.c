#define PY_SSIZE_T_CLEAN
#include "common.h"
#include <Python.h>
#include <stdint.h>
#include <stdlib.h>

int validate_size(uint64_t size, uint64_t max_size, const char *name) {
  if (size == 0) {
    PyErr_Format(PyExc_ValueError, "%s is zero", name);
    return -1;
  }
  if (size > max_size) {
    PyErr_Format(PyExc_ValueError,
                 "%s (%llu bytes) exceeds maximum size (%llu bytes)", name,
                 (unsigned long long)size, (unsigned long long)max_size);
    return -1;
  }
  return 0;
}

void *safe_malloc(size_t size) {
  if (size == 0) {
    PyErr_SetString(PyExc_ValueError, "Cannot allocate zero bytes");
    return NULL;
  }
  if (size > SIZE_MAX / 2) {
    PyErr_Format(PyExc_MemoryError, "Allocation size (%zu bytes) is too large",
                 size);
    return NULL;
  }

  void *ptr = malloc(size);
  if (!ptr) {
    PyErr_NoMemory();
  }
  return ptr;
}

void set_backend_error(const CBackend *backend, const char *op,
                       const char *context) {
  PyErr_Format(comp_BackendError, "Backend '%s' %s failed (%s)",
               backend && backend->name ? backend->name : "unknown", op,
               context);
}
