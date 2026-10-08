#define PY_SSIZE_T_CLEAN
#include "common.h"
#include <Python.h>

// ---- Strategy Selection ----

const CBackend *choose_backend(Strategy strat) {
  const CBackend *zlib = NULL, *bzip2 = NULL, *lzma = NULL;
  const CBackend *zstd = NULL, *lz4 = NULL, *snappy = NULL;

  for (const CBackend *b = BACKENDS; b->name; b++) {
    switch (b->id) {
    case ALGO_ZLIB:
      zlib = b;
      break;
    case ALGO_BZIP2:
      bzip2 = b;
      break;
    case ALGO_LZMA:
      lzma = b;
      break;
    case ALGO_ZSTD:
      zstd = b;
      break;
    case ALGO_LZ4:
      lz4 = b;
      break;
    case ALGO_SNAPPY:
      snappy = b;
      break;
    default:
      break;
    }
  }

  switch (strat) {
  case STRAT_FAST:
    if (lz4)
      return lz4;
    if (snappy)
      return snappy;
    if (zstd)
      return zstd;
    if (zlib)
      return zlib;
    if (lzma)
      return lzma;
    if (bzip2)
      return bzip2;
    break;
  case STRAT_MAX_RATIO:
    if (lzma)
      return lzma;
    if (zstd)
      return zstd;
    if (bzip2)
      return bzip2;
    if (zlib)
      return zlib;
    if (lz4)
      return lz4;
    if (snappy)
      return snappy;
    break;
  case STRAT_BALANCED:
  default:
    if (zstd)
      return zstd;
    if (zlib)
      return zlib;
    if (lzma)
      return lzma;
    if (bzip2)
      return bzip2;
    if (lz4)
      return lz4;
    if (snappy)
      return snappy;
    break;
  }

  return NULL;
}

// ---- Capability Check ----

// One end of level range as a new reference; None for a backend without levels
static PyObject *level_to_py(LevelRange levels, int level) {
  if (level_range_is_empty(levels))
    Py_RETURN_NONE;
  return PyLong_FromLong((long)level);
}

PyObject *get_capabilities(void) {
  PyObject *list = PyList_New(0);
  if (!list) {
    return NULL;
  }

  for (const CBackend *b = BACKENDS; b->name; b++) {
    PyObject *dict = PyDict_New();
    if (!dict) {
      Py_DECREF(list);
      return NULL;
    }

    PyObject *name = PyUnicode_FromString(b->name ? b->name : "");
    PyObject *id = PyLong_FromLong((long)b->id);
    PyObject *min_level = level_to_py(b->levels, b->levels.min);
    PyObject *max_level = level_to_py(b->levels, b->levels.max);

    int failed = !name || !id || !min_level || !max_level ||
                 PyDict_SetItemString(dict, "name", name) < 0 ||
                 PyDict_SetItemString(dict, "id", id) < 0 ||
                 PyDict_SetItemString(dict, "min_level", min_level) < 0 ||
                 PyDict_SetItemString(dict, "max_level", max_level) < 0;

    Py_XDECREF(name);
    Py_XDECREF(id);
    Py_XDECREF(min_level);
    Py_XDECREF(max_level);

    if (failed) {
      Py_DECREF(dict);
      Py_DECREF(list);
      return NULL;
    }

    int appended = PyList_Append(list, dict);
    Py_DECREF(dict);
    if (appended < 0) {
      Py_DECREF(list);
      return NULL;
    }
  }

  return list;
}
