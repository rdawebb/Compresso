#define PY_SSIZE_T_CLEAN
#include "validate.h"
#include <Python.h>

// -1 selects each backend's default
#define LEVEL_DEFAULT (-1)
#define LEVEL_MIN 0
#define LEVEL_MAX 22

int validate_compression_request(AlgoID algo, Strategy strategy, int level,
                                 const CompressionPipeline *pipeline) {
  (void)algo;
  (void)strategy;

  if (level != LEVEL_DEFAULT && (level < LEVEL_MIN || level > LEVEL_MAX)) {
    PyErr_Format(PyExc_ValueError,
                 "Compression level %d out of range (%d-%d, or -1 for default)",
                 level, LEVEL_MIN, LEVEL_MAX);
    return -1;
  }

  if (pipeline && !pipeline_is_valid(pipeline)) {
    PyErr_SetString(PyExc_ValueError, "Unsupported archive/codec combination");
    return -1;
  }

  return 0;
}
