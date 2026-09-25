#define PY_SSIZE_T_CLEAN
#include "common.h"
#include <Python.h>
#include <string.h>

#define BACKEND_ID_MAX 32

static const CBackend *backend_by_id[BACKEND_ID_MAX] = {NULL};
static const CBackend *registered_backends[BACKEND_ID_MAX];
static size_t num_registered_backends = 0;

static void register_backend(const CBackend *b) {
  if (!b) {
    return;
  }
  if (!b->is_available || !b->compress_stream || !b->decompress_stream) {
    return;
  }
  if (!b->is_available()) {
    return;
  }
  if (num_registered_backends <
      sizeof(registered_backends) / sizeof(registered_backends[0])) {
    registered_backends[num_registered_backends++] = b;

    if (b->id < BACKEND_ID_MAX) {
      backend_by_id[b->id] = b;
    }
  }
}

// ---- Backend Registry ----

static int backends_init = 0;

void init_backends(void) {
  if (backends_init)
    return;
  backends_init = 1;

  register_backend(get_zlib_backend());
  register_backend(get_bzip2_backend());
  register_backend(get_lzma_backend());
  register_backend(get_zstd_backend());
  register_backend(get_lz4_backend());
  register_backend(get_snappy_backend());
}

// ---- Backend Lookup ----

const CBackend *find_backend_by_name(const char *name) {
  if (!name)
    return NULL;
  init_backends();
  for (size_t i = 0; i < num_registered_backends; i++) {
    if (strcmp(registered_backends[i]->name, name) == 0) {
      return registered_backends[i];
    }
  }
  return NULL;
}

const CBackend *find_backend_by_id(uint8_t id) {
  init_backends();
  if (id < BACKEND_ID_MAX) {
    return backend_by_id[id];
  }
  return NULL;
}

// ---- Capability Check Helper ----

// ---- Strategy Selection ----

const CBackend *choose_backend(Strategy strat) {
  init_backends();

  const CBackend *zlib = NULL, *bzip2 = NULL, *lzma = NULL;
  const CBackend *zstd = NULL, *lz4 = NULL, *snappy = NULL;

  for (size_t i = 0; i < num_registered_backends; i++) {
    const CBackend *b = registered_backends[i];
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
  init_backends();

  PyObject *list = PyList_New((Py_ssize_t)num_registered_backends);
  if (!list) {
    return NULL;
  }

  for (size_t i = 0; i < num_registered_backends; i++) {
    const CBackend *b = registered_backends[i];
    if (!b) {
      Py_INCREF(Py_None);
      PyList_SetItem(list, (Py_ssize_t)i, Py_None);
      continue;
    }

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

    PyList_SetItem(list, (Py_ssize_t)i, dict);
  }

  return list;
}
