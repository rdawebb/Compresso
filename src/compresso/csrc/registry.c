#define PY_SSIZE_T_CLEAN
#include "common.h"
#include <Python.h>

// ---- Strategy Selection ----

#define COUNT(array) (sizeof(array) / sizeof((array)[0]))

// Each strategy's backends, best first; the first one built is the default
static const AlgoID PRIORITY[STRAT_COUNT][6] = {
    [STRAT_BALANCED] = {ALGO_ZSTD, ALGO_ZLIB, ALGO_LZMA, ALGO_BZIP2, ALGO_LZ4,
                        ALGO_SNAPPY},
    [STRAT_FAST] = {ALGO_LZ4, ALGO_SNAPPY, ALGO_ZSTD, ALGO_ZLIB, ALGO_LZMA,
                    ALGO_BZIP2},
    [STRAT_MAX_RATIO] = {ALGO_LZMA, ALGO_ZSTD, ALGO_BZIP2, ALGO_ZLIB, ALGO_LZ4,
                         ALGO_SNAPPY},
};

const CBackend *choose_backend(Strategy strat) {
  if (strat < 0 || strat >= STRAT_COUNT)
    strat = STRAT_BALANCED;

  for (size_t i = 0; i < COUNT(PRIORITY[strat]); i++) {
    const CBackend *b = find_backend_by_id(PRIORITY[strat][i]);
    if (b)
      return b;
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

// Each strategy's name, mapped to `id`'s place in its priorities (0 is first)
static PyObject *ranks_to_py(uint8_t id) {
  PyObject *ranks = PyDict_New();
  if (!ranks)
    return NULL;

  for (int s = 0; s < STRAT_COUNT; s++) {
    size_t i = 0;
    while (i < COUNT(PRIORITY[s]) && PRIORITY[s][i] != id)
      i++;

    PyObject *rank = PyLong_FromSize_t(i);
    if (!rank || PyDict_SetItemString(ranks, STRATEGY_NAMES[s], rank) < 0) {
      Py_XDECREF(rank);
      Py_DECREF(ranks);
      return NULL;
    }
    Py_DECREF(rank);
  }
  return ranks;
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
    PyObject *rank = ranks_to_py(b->id);

    int failed = !name || !id || !min_level || !max_level || !rank ||
                 PyDict_SetItemString(dict, "name", name) < 0 ||
                 PyDict_SetItemString(dict, "id", id) < 0 ||
                 PyDict_SetItemString(dict, "min_level", min_level) < 0 ||
                 PyDict_SetItemString(dict, "max_level", max_level) < 0 ||
                 PyDict_SetItemString(dict, "rank", rank) < 0;

    Py_XDECREF(name);
    Py_XDECREF(id);
    Py_XDECREF(min_level);
    Py_XDECREF(max_level);
    Py_XDECREF(rank);

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
