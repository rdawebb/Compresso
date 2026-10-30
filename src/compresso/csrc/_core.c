#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "codec/crc32c.h"
#include "common.h"
#include "fsutil.h"
#include "standalone.h"
#include "validate.h"

// ---- Cancel Token ----

// A cancellation flag the C loops can poll without holding the GIL
typedef struct {
  PyObject_HEAD
  COMPRESSO_ATOMIC_INT flag; // One-way
} CancelTokenObject;

static PyObject *cancel_token_cancel(PyObject *self,
                                     PyObject *Py_UNUSED(ignored)) {
  comp_atomic_store(&((CancelTokenObject *)self)->flag, 1);
  Py_RETURN_NONE;
}

static PyObject *cancel_token_get_cancelled(PyObject *self,
                                            void *Py_UNUSED(closure)) {
  return PyBool_FromLong(comp_atomic_load(&((CancelTokenObject *)self)->flag));
}

static PyMethodDef cancel_token_methods[] = {
    {"cancel", cancel_token_cancel, METH_NOARGS,
     "Request cancellation of any operation using this token.\n"
     "Safe to call from any thread, and before the operation starts."},
    {NULL, NULL, 0, NULL}};

static PyGetSetDef cancel_token_getset[] = {
    {"cancelled", cancel_token_get_cancelled, NULL,
     "True once cancel() has been called.", NULL},
    {NULL, NULL, NULL, NULL, NULL}};

static PyTypeObject CancelTokenType = {
    PyVarObject_HEAD_INIT(NULL, 0).tp_name = "compresso._core.CancelToken",
    .tp_doc = "Cancellation flag shared with a running compression.",
    .tp_basicsize = sizeof(CancelTokenObject),
    .tp_itemsize = 0,
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_new = PyType_GenericNew,
    .tp_methods = cancel_token_methods,
    .tp_getset = cancel_token_getset,
};

// ---- Progress Bridge ----

// Called from ctx_advance inside a codec loop running with the GIL released, so
// it reacquires the GIL first
static int progress_bridge(CoreContext *ctx, uint64_t done, uint64_t total) {
  PyGILState_STATE gstate = PyGILState_Ensure();

  int result = 0;
  // NULL when the caller asked for no progress; the context stays attached so
  // the signal check below still runs
  PyObject *callback = (PyObject *)ctx->userdata;
  if (callback) {
    PyObject *ret = PyObject_CallFunction(
        callback, "KK", (unsigned long long)done, (unsigned long long)total);
    if (!ret) {
      result = -1; // The callback raised; its exception propagates
    } else {
      Py_DECREF(ret);
    }
  }

  // Runs pending signal handlers, so a Ctrl-C mid-compression raises promptly
  if (result == 0 && PyErr_CheckSignals() != 0) {
    result = -1;
  }

  PyGILState_Release(gstate);
  return result;
}

// ---- Log Bridge ----

// Logs to the "compresso" logger; may run inside a codec loop with the GIL
// released, or on an error path whose exception it must leave in place
static void log_bridge(CoreContext *ctx, int level, const char *message) {
  (void)ctx;
  PyGILState_STATE gstate = PyGILState_Ensure();

#if PY_VERSION_HEX >= 0x030C0000
  PyObject *pending = PyErr_GetRaisedException();
#else
  PyObject *pending_type, *pending, *pending_tb;
  PyErr_Fetch(&pending_type, &pending, &pending_tb);
#endif

  // A byte that isn't UTF-8 is shown as \xNN; a lone surrogate would break any
  // handler writing UTF-8
  PyObject *text =
      PyUnicode_DecodeUTF8(message, strlen(message), "backslashreplace");
  PyObject *logging = text ? PyImport_ImportModule("logging") : NULL;
  PyObject *logger =
      logging ? PyObject_CallMethod(logging, "getLogger", "s", "compresso")
              : NULL;
  PyObject *result =
      logger ? PyObject_CallMethod(logger, "log", "iO", level, text) : NULL;

  // Logging must never fail the operation it describes
  if (!result) {
    PyErr_WriteUnraisable(NULL);
  }

  Py_XDECREF(result);
  Py_XDECREF(logger);
  Py_XDECREF(logging);
  Py_XDECREF(text);

#if PY_VERSION_HEX >= 0x030C0000
  PyErr_SetRaisedException(pending);
#else
  PyErr_Restore(pending_type, pending, pending_tb);
#endif
  PyGILState_Release(gstate);
}

// Populates `ctx` from `module`'s exceptions and optional progress=, cancel=
// and threads= arguments; returns -1 with an exception set if one is invalid
//
// The bridge is installed even without progress= or cancel=, since it is also
// where PyErr_CheckSignals runs
static int core_context_init(CoreContext *ctx, PyObject *module,
                             PyObject *progress, PyObject *cancel,
                             int threads) {
  memset(ctx, 0, sizeof(*ctx));
  ctx->errors = PyModule_GetState(module);

  if (threads < 0) {
    PyErr_Format(PyExc_ValueError,
                 "threads must be 0 (one per core) or more, not %d", threads);
    return -1;
  }
  ctx->threads = threads;
  ctx->on_progress = progress_bridge;
  ctx->on_log = log_bridge;

  if (progress && progress != Py_None) {
    if (!PyCallable_Check(progress)) {
      PyErr_SetString(PyExc_TypeError, "progress must be callable or None");
      return -1;
    }
    ctx->userdata = progress;
  }

  if (cancel && cancel != Py_None) {
    if (!Py_IS_TYPE(cancel, &CancelTokenType)) {
      PyErr_SetString(PyExc_TypeError, "cancel must be a CancelToken or None");
      return -1;
    }
    ctx->cancel_flag = &((CancelTokenObject *)cancel)->flag;
  }

  return 0;
}

// Each call's tail: raises a failure, or finishes the progress and returns the
// path actually written (RENAME may change it), or None for a NULL path; `ctx`
// is only read on success
static PyObject *finish_call(PyObject *module, int rc, CoreContext *ctx,
                             const char *actual_path) {
  if (rc != 0) {
    // An exception raised by callback or a signal handler is more specific
    if (rc == COMP_CANCELLED && !PyErr_Occurred()) {
      const CompErrors *errors = PyModule_GetState(module);
      PyErr_SetString(errors->cancelled, "Operation cancelled");
    }
    return NULL;
  }
  if (ctx_finish(ctx) != 0)
    return NULL;
  if (!actual_path)
    Py_RETURN_NONE;
  return PyUnicode_DecodeFSDefault(actual_path);
}

// ---- Path Conversion ----

// Paths are parsed with O& and PyUnicode_FSConverter, which takes str, bytes
// or os.PathLike and rejects embedded NULs; the caller Py_DECREFs the bytes

typedef struct {
  PyObject *keepalive; // Tuple of the bytes backing paths
  const char **paths;
  size_t count;
} PathList;

// O& converter for a sequence of paths; call with obj NULL to release
static int path_list_converter(PyObject *obj, void *out) {
  PathList *list = out;
  if (!obj) {
    Py_CLEAR(list->keepalive);
    free(list->paths);
    list->paths = NULL;
    return 1;
  }

  // Prevents a bare path from being treated as a sequence of characters
  if (PyUnicode_Check(obj) || PyBytes_Check(obj)) {
    PyErr_Format(PyExc_TypeError,
                 "input_paths must be a sequence of paths, not %s",
                 Py_TYPE(obj)->tp_name);
    return 0;
  }

  PyObject *items = PySequence_Tuple(obj);
  if (!items) {
    return 0;
  }

  Py_ssize_t n = PyTuple_GET_SIZE(items);
  list->keepalive = PyTuple_New(n);
  list->paths = n > 0 ? safe_malloc((size_t)n * sizeof(char *)) : NULL;
  list->count = (size_t)n;
  if (!list->keepalive || (n > 0 && !list->paths)) {
    goto fail;
  }

  for (Py_ssize_t i = 0; i < n; i++) {
    PyObject *bytes = NULL;
    if (!PyUnicode_FSConverter(PyTuple_GET_ITEM(items, i), &bytes)) {
      goto fail;
    }
    PyTuple_SET_ITEM(list->keepalive, i, bytes); // Steals the reference
    list->paths[i] = PyBytes_AS_STRING(bytes);
  }

  Py_DECREF(items);
  return Py_CLEANUP_SUPPORTED;

fail:
  Py_DECREF(items);
  path_list_converter(NULL, list);
  return 0;
}

// Glob patterns for `include` and `exclude`, decoded as UTF-8 as archives store
// entry names; `keepalive` holds the str objects that own that UTF-8
static int pattern_list_converter(PyObject *obj, void *out) {
  PathList *list = out;
  if (!obj) {
    Py_CLEAR(list->keepalive);
    free(list->paths);
    list->paths = NULL;
    return 1;
  }

  if (PyUnicode_Check(obj) || PyBytes_Check(obj)) {
    PyErr_Format(PyExc_TypeError, "patterns must be a sequence of str, not %s",
                 Py_TYPE(obj)->tp_name);
    return 0;
  }

  list->keepalive = PySequence_Tuple(obj);
  if (!list->keepalive) {
    return 0;
  }

  Py_ssize_t n = PyTuple_GET_SIZE(list->keepalive);
  list->paths = n > 0 ? safe_malloc((size_t)n * sizeof(char *)) : NULL;
  list->count = (size_t)n;
  if (n > 0 && !list->paths) {
    goto fail;
  }

  for (Py_ssize_t i = 0; i < n; i++) {
    PyObject *item = PyTuple_GET_ITEM(list->keepalive, i);
    if (!PyUnicode_Check(item)) {
      PyErr_Format(PyExc_TypeError, "patterns must contain only str, not %s",
                   Py_TYPE(item)->tp_name);
      goto fail;
    }
    list->paths[i] = PyUnicode_AsUTF8(item);
    if (!list->paths[i]) {
      goto fail;
    }
  }
  return Py_CLEANUP_SUPPORTED;

fail:
  pattern_list_converter(NULL, list);
  return 0;
}

// ---- Argument Parsing ----

// Raises ValueError for an unknown strategy name
static int parse_strategy(const char *name, Strategy *out) {
  *out = strategy_from_string(name);
  if (*out == STRAT_UNKNOWN) {
    PyErr_Format(PyExc_ValueError,
                 "Unknown strategy: %s (expected fast, balanced or max_ratio)",
                 name);
    return -1;
  }
  return 0;
}

// ---- Module Methods ----

static PyObject *py_compress_file(PyObject *self, PyObject *args,
                                  PyObject *kwargs) {
  static char *kwlist[] = {"src_path", "dst_path",  "algo",    "strategy",
                           "level",    "overwrite", "threads", "progress",
                           "cancel",   NULL};

  PyObject *src_path_bytes = NULL;
  PyObject *dst_path_bytes = NULL;
  const char *algo_name = NULL;
  const char *strategy_name = NULL;
  int level = -1;
  int overwrite_existing = 0; // ERROR by default; frontends opt into RENAME
  int threads = 0;            // One per core
  PyObject *progress = NULL;
  PyObject *cancel = NULL;

  if (!PyArg_ParseTupleAndKeywords(
          args, kwargs, "O&O&|ssi$iiOO", kwlist, PyUnicode_FSConverter,
          &src_path_bytes, PyUnicode_FSConverter, &dst_path_bytes, &algo_name,
          &strategy_name, &level, &overwrite_existing, &threads, &progress,
          &cancel)) {
    return NULL; // Error already set
  }

  AlgoID algo = algo_from_string(algo_name);
  Strategy strat;
  CoreContext ctx;
  int result = -1;
  if (validate_overwrite_arg(overwrite_existing) != 0) {
    goto done;
  }

  if (algo_name && algo_name[0] != '\0' && algo == ALGO_NONE) {
    PyErr_Format(PyExc_ValueError, "Unknown compression algorithm: %s",
                 algo_name);
    goto done;
  }

  if (parse_strategy(strategy_name, &strat) != 0 ||
      validate_compression_request(algo, strat, level, NULL) != 0 ||
      core_context_init(&ctx, self, progress, cancel, threads) != 0) {
    goto done;
  }

  char actual_path[FS_PATH_MAX];
  result =
      compress_file(PyBytes_AS_STRING(src_path_bytes),
                    PyBytes_AS_STRING(dst_path_bytes), algo, strat, level,
                    overwrite_existing, actual_path, sizeof(actual_path), &ctx);

done:
  Py_DECREF(src_path_bytes);
  Py_DECREF(dst_path_bytes);
  return finish_call(self, result, &ctx, actual_path);
}

static PyObject *py_decompress_file(PyObject *self, PyObject *args,
                                    PyObject *kwargs) {
  static char *kwlist[] = {"src_path", "dst_path", "algo",   "overwrite",
                           "threads",  "progress", "cancel", NULL};

  PyObject *src_path_bytes = NULL;
  PyObject *dst_path_bytes = NULL;
  const char *algo_name = NULL;
  int overwrite_existing = 0; // ERROR by default; frontends opt into RENAME
  int threads = 0;            // One per core
  PyObject *progress = NULL;
  PyObject *cancel = NULL;

  if (!PyArg_ParseTupleAndKeywords(
          args, kwargs, "O&O&|s$iiOO", kwlist, PyUnicode_FSConverter,
          &src_path_bytes, PyUnicode_FSConverter, &dst_path_bytes, &algo_name,
          &overwrite_existing, &threads, &progress, &cancel)) {
    return NULL; // Error already set
  }

  AlgoID algo = algo_from_string(algo_name);
  CoreContext ctx;
  int result = -1;
  if (validate_overwrite_arg(overwrite_existing) != 0) {
    goto done;
  }

  if (algo_name && algo_name[0] != '\0' && algo == ALGO_NONE) {
    PyErr_Format(PyExc_ValueError, "Unknown decompression algorithm: %s",
                 algo_name);
    goto done;
  }

  if (core_context_init(&ctx, self, progress, cancel, threads) != 0) {
    goto done;
  }

  char actual_path[FS_PATH_MAX];
  result = decompress_file(
      PyBytes_AS_STRING(src_path_bytes), PyBytes_AS_STRING(dst_path_bytes),
      algo, overwrite_existing, actual_path, sizeof(actual_path), &ctx);

done:
  Py_DECREF(src_path_bytes);
  Py_DECREF(dst_path_bytes);
  return finish_call(self, result, &ctx, actual_path);
}

// ---- Archive Operations ----

static PyObject *py_create_archive(PyObject *self, PyObject *args,
                                   PyObject *kwargs) {
  static char *kwlist[] = {"output_path",       "format",    "input_paths",
                           "compression_level", "overwrite", "threads",
                           "progress",          "cancel",    "include",
                           "exclude",           NULL};

  PyObject *output_path_bytes = NULL;
  const char *format_name = NULL;
  PathList inputs = {0};
  int compression_level = -1;
  int overwrite_existing = 0; // ERROR by default; frontends opt into RENAME
  int threads = 0;            // One per core
  PyObject *progress = NULL;
  PyObject *cancel = NULL;
  PathList include = {0};
  PathList exclude = {0};

  if (!PyArg_ParseTupleAndKeywords(
          args, kwargs, "O&sO&|i$iiOOO&O&", kwlist, PyUnicode_FSConverter,
          &output_path_bytes, &format_name, path_list_converter, &inputs,
          &compression_level, &overwrite_existing, &threads, &progress, &cancel,
          pattern_list_converter, &include, pattern_list_converter, &exclude)) {
    return NULL; // Error already set
  }
  PathFilter filter = {include.paths, include.count, exclude.paths,
                       exclude.count};

  CompressionPipeline pipe = pipeline_from_name(format_name, compression_level);
  CoreContext ctx;
  int result = -1;
  if (validate_overwrite_arg(overwrite_existing) != 0) {
    goto done;
  }

  if (pipe.archive == ARCHIVE_NONE && pipe.codec == FORMAT_UNKNOWN) {
    PyErr_Format(PyExc_ValueError, "Unknown archive format: %s", format_name);
    goto done;
  }

  if (validate_compression_request(ALGO_NONE, STRAT_BALANCED, compression_level,
                                   &pipe) != 0 ||
      core_context_init(&ctx, self, progress, cancel, threads) != 0) {
    goto done;
  }

  char actual_path[FS_PATH_MAX];
  result = create_archive(PyBytes_AS_STRING(output_path_bytes), &pipe,
                          inputs.paths, inputs.count, &filter,
                          overwrite_existing, actual_path, sizeof(actual_path),
                          &ctx);

done:
  Py_DECREF(output_path_bytes);
  path_list_converter(NULL, &inputs);
  pattern_list_converter(NULL, &include);
  pattern_list_converter(NULL, &exclude);
  return finish_call(self, result, &ctx, actual_path);
}

static PyObject *py_extract_archive(PyObject *self, PyObject *args,
                                    PyObject *kwargs) {
  static char *kwlist[] = {"archive_path",
                           "output_dir",
                           "files",
                           "overwrite",
                           "max_total_size",
                           "max_depth",
                           "preserve_permissions",
                           "exact_permissions",
                           "preserve_timestamps",
                           "allow_symlinks",
                           "overwrite_dir_metadata",
                           "threads",
                           "progress",
                           "cancel",
                           "include",
                           "exclude",
                           NULL};

  PyObject *archive_path_bytes = NULL;
  PyObject *output_dir_bytes = NULL;
  PyObject *files_obj = NULL;
  int threads = 0; // One per core
  PyObject *progress = NULL;
  PyObject *cancel = NULL;
  PathList include = {0};
  PathList exclude = {0};

  // Anything the caller does not pass keeps its default
  ExtractionPolicy policy = extraction_policy_default();

  // K and I write through exactly-sized pointers, so they cannot target the
  // struct's fixed-width fields directly
  unsigned long long max_total_size = (unsigned long long)policy.max_total_size;
  unsigned int max_depth = (unsigned int)policy.max_depth;

  if (!PyArg_ParseTupleAndKeywords(
          args, kwargs, "O&O&O|$iKIpppipiOOO&O&", kwlist,
          PyUnicode_FSConverter, &archive_path_bytes, PyUnicode_FSConverter,
          &output_dir_bytes, &files_obj, &policy.overwrite_existing,
          &max_total_size, &max_depth, &policy.preserve_permissions,
          &policy.exact_permissions, &policy.preserve_timestamps,
          &policy.allow_symlinks, &policy.overwrite_dir_metadata, &threads,
          &progress, &cancel, pattern_list_converter, &include,
          pattern_list_converter, &exclude)) {
    return NULL; // Error already set
  }
  PathFilter filter = {include.paths, include.count, exclude.paths,
                       exclude.count};

  policy.max_total_size = (uint64_t)max_total_size;
  policy.max_depth = (uint32_t)max_depth;

  PyObject *files_tuple = NULL;
  const char **files = NULL;
  size_t num_files = 0;
  CoreContext ctx;
  int result = -1;

  if (validate_overwrite_arg(policy.overwrite_existing) != 0) {
    goto done;
  }

  if (policy.allow_symlinks < -1 || policy.allow_symlinks > 2) {
    PyErr_Format(PyExc_ValueError,
                 "allow_symlinks must be 0 (deny), 1 (allow), 2 (rewrite) or "
                 "-1 (the format's default), not %d",
                 policy.allow_symlinks);
    goto done;
  }

  // Prevents a bare string from being treated as a sequence of characters
  if (PyUnicode_Check(files_obj) || PyBytes_Check(files_obj)) {
    PyErr_Format(PyExc_TypeError, "files must be a sequence of str, not %s",
                 Py_TYPE(files_obj)->tp_name);
    goto done;
  }

  // Private tuple, so a progress callback that mutates the caller's list
  // can't free the names out from under the extraction
  files_tuple = PySequence_Tuple(files_obj);
  if (!files_tuple) {
    goto done;
  }

  num_files = (size_t)PyTuple_GET_SIZE(files_tuple);
  if (num_files > 0) {
    files = safe_malloc(num_files * sizeof(char *));
    if (!files) {
      goto done;
    }
  }

  // `files` are archive-internal entry names matched against the stored (UTF-8)
  // paths, so they are decoded as UTF-8 rather than fs-encoded
  for (size_t i = 0; i < num_files; i++) {
    PyObject *item = PyTuple_GET_ITEM(files_tuple, i);
    if (!PyUnicode_Check(item)) {
      PyErr_Format(PyExc_TypeError, "files must contain only str, not %s",
                   Py_TYPE(item)->tp_name);
      goto done;
    }
    files[i] = PyUnicode_AsUTF8(item);
    if (!files[i]) {
      goto done;
    }
  }

  if (core_context_init(&ctx, self, progress, cancel, threads) != 0) {
    goto done;
  }

  result = extract_archive(PyBytes_AS_STRING(archive_path_bytes),
                           PyBytes_AS_STRING(output_dir_bytes), files,
                           num_files, &filter, &policy, &ctx);

done:
  Py_DECREF(archive_path_bytes);
  Py_DECREF(output_dir_bytes);
  pattern_list_converter(NULL, &include);
  pattern_list_converter(NULL, &exclude);
  free(files);
  Py_XDECREF(files_tuple);
  return finish_call(self, result, &ctx, NULL);
}

static PyObject *py_list_archive_contents(PyObject *self, PyObject *args) {
  PyObject *archive_path_bytes = NULL;

  if (!PyArg_ParseTuple(args, "O&", PyUnicode_FSConverter,
                        &archive_path_bytes)) {
    return NULL; // Error already set
  }

  // No progress to report, but the bridge still logs and checks for signals
  CoreContext ctx;
  core_context_init(&ctx, self, NULL, NULL, 0);

  PyObject *file_list =
      list_archive_contents(PyBytes_AS_STRING(archive_path_bytes), &ctx);
  Py_DECREF(archive_path_bytes);
  return file_list; // NULL propagates the already-set exception
}

// ---- Standalone Methods ----

static PyObject *py_compress_standalone(PyObject *self, PyObject *args,
                                        PyObject *kwargs) {
  static char *kwlist[] = {"input_path",        "output_path", "format",
                           "compression_level", "overwrite",   "threads",
                           "progress",          "cancel",      NULL};

  PyObject *input_path_bytes = NULL;
  PyObject *output_path_bytes = NULL;
  const char *format_name = NULL;
  int compression_level = -1;
  int overwrite_existing = 0; // ERROR by default; frontends opt into RENAME
  int threads = 0;            // One per core
  PyObject *progress = NULL;
  PyObject *cancel = NULL;

  if (!PyArg_ParseTupleAndKeywords(
          args, kwargs, "O&O&s|i$iiOO", kwlist, PyUnicode_FSConverter,
          &input_path_bytes, PyUnicode_FSConverter, &output_path_bytes,
          &format_name, &compression_level, &overwrite_existing, &threads,
          &progress, &cancel)) {
    return NULL; // Error already set
  }

  CoreContext ctx;
  char actual_path[FS_PATH_MAX];
  int rc = -1;
  if (validate_overwrite_arg(overwrite_existing) != 0) {
    goto done;
  }

  const FormatDesc *desc = format_by_name(format_name);
  if (!desc) {
    PyErr_Format(PyExc_ValueError, "Unknown standalone format: %s",
                 format_name);
    goto done;
  }

  const FormatDesc *fmt = find_standalone_format(desc->id);
  if (!fmt) {
    PyErr_Format(PyExc_ValueError, "Format cannot compress a single file: %s",
                 desc->name);
    goto done;
  }

  if (validate_level(fmt->name, fmt->levels, compression_level) != 0 ||
      core_context_init(&ctx, self, progress, cancel, threads) != 0) {
    goto done;
  }

  OutputTarget out = {.path = PyBytes_AS_STRING(output_path_bytes),
                      .overwrite = overwrite_existing,
                      .actual = actual_path,
                      .actual_size = sizeof(actual_path),
                      .keep_source_metadata = 1};
  rc = standalone_compress(fmt, PyBytes_AS_STRING(input_path_bytes), &out,
                           compression_level, &ctx);

done:
  Py_DECREF(input_path_bytes);
  Py_DECREF(output_path_bytes);
  return finish_call(self, rc, &ctx, actual_path);
}

static PyObject *py_decompress_standalone(PyObject *self, PyObject *args,
                                          PyObject *kwargs) {
  static char *kwlist[] = {"input_path", "output_path", "format", "overwrite",
                           "threads",    "progress",    "cancel", NULL};

  PyObject *input_path_bytes = NULL;
  PyObject *output_path_bytes = NULL;
  const char *format_name = NULL;
  int overwrite_existing = 0; // ERROR by default; frontends opt into RENAME
  int threads = 0;            // One per core
  PyObject *progress = NULL;
  PyObject *cancel = NULL;

  Format format = FORMAT_UNKNOWN;
  FILE *src = NULL;

  if (!PyArg_ParseTupleAndKeywords(
          args, kwargs, "O&O&|s$iiOO", kwlist, PyUnicode_FSConverter,
          &input_path_bytes, PyUnicode_FSConverter, &output_path_bytes,
          &format_name, &overwrite_existing, &threads, &progress, &cancel)) {
    return NULL; // Error already set
  }

  const char *input_path = PyBytes_AS_STRING(input_path_bytes);
  const FormatDesc *named = NULL;
  CoreContext ctx;
  char actual_path[FS_PATH_MAX];
  int rc = -1;
  if (validate_overwrite_arg(overwrite_existing) != 0) {
    goto done;
  }

  if (format_name) {
    named = format_by_name(format_name);
    if (!named) {
      PyErr_Format(PyExc_ValueError, "Unknown format: %s", format_name);
      goto done;
    }
  }

  if (core_context_init(&ctx, self, progress, cancel, threads) != 0) {
    goto done;
  }

  src = open_source(input_path, &format);
  if (!src) {
    goto done;
  }

  if (named) {
    format = named->id;
  } else if (format == FORMAT_UNKNOWN) {
    PyErr_Format(PyExc_ValueError, "Could not detect format for: %s",
                 input_path);
    goto done;
  }

  const FormatDesc *fmt = find_standalone_format(format);
  if (!fmt) {
    PyErr_Format(PyExc_ValueError, "Format cannot decompress a single file: %s",
                 format_by_id(format)->name);
    goto done;
  }

  OutputTarget out = {.path = PyBytes_AS_STRING(output_path_bytes),
                      .overwrite = overwrite_existing,
                      .actual = actual_path,
                      .actual_size = sizeof(actual_path),
                      .keep_source_metadata = 1};
  rc = standalone_decompress(fmt, input_path, src, &out, &ctx);
  src = NULL; // Closed by standalone_decompress

done:
  if (src) {
    fclose(src);
  }
  Py_DECREF(input_path_bytes);
  Py_DECREF(output_path_bytes);
  return finish_call(self, rc, &ctx, actual_path);
}

// ---- Format Detection Methods ----

static PyObject *py_detect_format(PyObject *Py_UNUSED(self), PyObject *args) {
  PyObject *file_path_bytes = NULL;

  if (!PyArg_ParseTuple(args, "O&", PyUnicode_FSConverter, &file_path_bytes)) {
    return NULL; // Error already set
  }

  const char *file_path = PyBytes_AS_STRING(file_path_bytes);
  Format format;
  FILE *f = open_source(file_path, &format);
  if (!f) {
    Py_DECREF(file_path_bytes);
    return NULL;
  }
  fclose(f);

  CompressionPipeline pipe = pipeline_from_format(format, file_path);
  char name[32];
  pipeline_display_name(&pipe, name, sizeof(name));

  Py_DECREF(file_path_bytes);
  return PyUnicode_FromString(name);
}

static PyObject *py_gzip_stored_name(PyObject *Py_UNUSED(self),
                                     PyObject *args) {
  PyObject *file_path_bytes = NULL;

  if (!PyArg_ParseTuple(args, "O&", PyUnicode_FSConverter, &file_path_bytes)) {
    return NULL; // Error already set
  }

  char *name = gzip_original_name(PyBytes_AS_STRING(file_path_bytes));
  Py_DECREF(file_path_bytes);
  if (!name) {
    Py_RETURN_NONE;
  }

  PyObject *result = PyBytes_FromString(name);
  free(name);
  return result;
}

static PyObject *py_format_is_archive(PyObject *Py_UNUSED(self),
                                      PyObject *args) {
  const char *format_name = NULL;

  if (!PyArg_ParseTuple(args, "s", &format_name)) {
    return NULL; // Error already set
  }

  CompressionPipeline pipe = pipeline_from_name(format_name, -1);

  return PyBool_FromLong(pipeline_is_valid(&pipe) &&
                         pipe.archive != ARCHIVE_NONE);
}

// The set entries of a FormatDesc name slot, which fill from the front
static PyObject *names_to_py(const char *const names[2]) {
  if (!names[0])
    return PyTuple_New(0);
  return names[1] ? Py_BuildValue("(ss)", names[0], names[1])
                  : Py_BuildValue("(s)", names[0]);
}

static PyObject *py_standalone_formats(PyObject *Py_UNUSED(self),
                                       PyObject *Py_UNUSED(ignored)) {
  PyObject *list = PyList_New(0);
  if (!list)
    return NULL;

  for (const FormatDesc *d = FORMATS; d->name; d++) {
    if (d->kind != KIND_CONTAINER)
      continue;
    PyObject *item =
        Py_BuildValue("{s:s,s:N,s:N}", "name", d->name, "aliases",
                      names_to_py(d->aliases), "exts", names_to_py(d->exts));
    if (!item || PyList_Append(list, item) < 0) {
      Py_XDECREF(item);
      Py_DECREF(list);
      return NULL;
    }
    Py_DECREF(item);
  }
  return list;
}

// ---- Capabilities Methods ----

static PyObject *py_get_capabilities(PyObject *Py_UNUSED(self),
                                     PyObject *Py_UNUSED(ignored)) {
  return get_capabilities();
}

static PyObject *py_get_archive_capabilities(PyObject *Py_UNUSED(self),
                                             PyObject *Py_UNUSED(ignored)) {
  return get_archive_capabilities();
}

static PyObject *py_get_default_backend_for_strategy(PyObject *Py_UNUSED(self),
                                                     PyObject *args) {
  const char *strategy_name = NULL;

  if (!PyArg_ParseTuple(args, "|s", &strategy_name)) {
    return NULL; // Error already set
  }

  Strategy strat;
  if (parse_strategy(strategy_name, &strat) != 0) {
    return NULL;
  }
  const CBackend *b = choose_backend(strat);
  if (!b) {
    Py_RETURN_NONE;
  }
  return PyUnicode_FromString(b->name);
}

// The same level check compress_file, compress_standalone and create_archive
// make, without doing any work, so a plan can refuse a level up front
static PyObject *py_check_level(PyObject *Py_UNUSED(self), PyObject *args,
                                PyObject *kwargs) {
  static char *kwlist[] = {"level", "algo", "strategy", "format", NULL};

  int level = -1;
  const char *algo_name = "";
  const char *strategy_name = "";
  const char *format_name = "";

  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "i|$sss", kwlist, &level,
                                   &algo_name, &strategy_name, &format_name)) {
    return NULL; // Error already set
  }

  if (format_name[0] != '\0') {
    CompressionPipeline pipe = pipeline_from_name(format_name, level);
    if (pipe.archive == ARCHIVE_NONE && pipe.codec == FORMAT_UNKNOWN) {
      PyErr_Format(PyExc_ValueError, "Unknown format: %s", format_name);
      return NULL;
    }
    if (validate_compression_request(ALGO_NONE, STRAT_BALANCED, level, &pipe) !=
        0) {
      return NULL;
    }
    Py_RETURN_NONE;
  }

  AlgoID algo = algo_from_string(algo_name);
  if (algo_name[0] != '\0' && algo == ALGO_NONE) {
    PyErr_Format(PyExc_ValueError, "Unknown compression algorithm: %s",
                 algo_name);
    return NULL;
  }

  Strategy strat;
  if (parse_strategy(strategy_name, &strat) != 0 ||
      validate_compression_request(algo, strat, level, NULL) != 0) {
    return NULL;
  }
  Py_RETURN_NONE;
}

// ---- Module Definition ----

static PyMethodDef CoreMethods[] = {
    {"compress_file", (PyCFunction)(void (*)(void))py_compress_file,
     METH_VARARGS | METH_KEYWORDS,
     "Compress a file using the specified algorithm and strategy."},
    {"decompress_file", (PyCFunction)(void (*)(void))py_decompress_file,
     METH_VARARGS | METH_KEYWORDS,
     "Decompress a file using the specified algorithm."},

    {"create_archive", (PyCFunction)(void (*)(void))py_create_archive,
     METH_VARARGS | METH_KEYWORDS,
     "Create a new archive from a list of files."},
    {"extract_archive", (PyCFunction)(void (*)(void))py_extract_archive,
     METH_VARARGS | METH_KEYWORDS,
     "Extract an archive file to a specified directory, subject to the "
     "keyword-only extraction policy."},
    {"list_archive_contents", (PyCFunction)py_list_archive_contents,
     METH_VARARGS,
     "Describe each entry in an archive file as a dict (path, size, type, "
     "mtime, mode, link_target, plus compressed_size, crc and method where "
     "the container records them)."},

    {"compress_standalone", (PyCFunction)(void (*)(void))py_compress_standalone,
     METH_VARARGS | METH_KEYWORDS,
     "Compress a file using a standalone compression format."},
    {"decompress_standalone",
     (PyCFunction)(void (*)(void))py_decompress_standalone,
     METH_VARARGS | METH_KEYWORDS, "Decompress a standalone format file."},

    {"detect_format", (PyCFunction)py_detect_format, METH_VARARGS,
     "Detect the format of a file."},
    {"gzip_stored_name", (PyCFunction)py_gzip_stored_name, METH_VARARGS,
     "Return the name a gzip header stores (FNAME), or None."},
    {"format_is_archive", (PyCFunction)py_format_is_archive, METH_VARARGS,
     "Check if a format is an archive format."},
    {"standalone_formats", (PyCFunction)py_standalone_formats, METH_NOARGS,
     "Describe each single-file container format as a dict (name, aliases, "
     "exts)."},

    {"get_capabilities", (PyCFunction)py_get_capabilities, METH_NOARGS,
     "Get the capabilities of available compression backends."},
    {"archive_capabilities", (PyCFunction)py_get_archive_capabilities,
     METH_NOARGS, "Get the capabilities of available archive backends."},
    {"check_level", (PyCFunction)(void (*)(void))py_check_level,
     METH_VARARGS | METH_KEYWORDS,
     "Raise ValueError if a compression level is out of range for the given "
     "algorithm, strategy or format."},
    {"get_default_backend_for_strategy",
     (PyCFunction)py_get_default_backend_for_strategy, METH_VARARGS,
     "Get the default backend for a given strategy, None if no backend is "
     "available."},

    {NULL, NULL, 0, NULL} // Sentinel
};

// ---- Module Initialisation ----

static int core_exec(PyObject *module) {
  crc32c_init();

  // Built at run time: on Windows, &PyExc_UserWarning isn't a constant
  // expression; each base must precede the exceptions derived from it;
  // failure leaves the rest to core_free
  CompErrors *errors = PyModule_GetState(module);
  const struct {
    PyObject **slot;
    const char *name;
    PyObject **base;
  } exceptions[] = {
      {&errors->error, "compresso.Error", NULL},
      {&errors->header, "compresso.HeaderError", &errors->error},
      {&errors->backend, "compresso.BackendError", &errors->error},
      {&errors->cancelled, "compresso.Cancelled", &errors->error},
      {&errors->corrupt_data, "compresso.CorruptDataError", &errors->error},
      {&errors->extraction_policy, "compresso.ExtractionPolicyError",
       &errors->error},
      {&errors->incomplete_extraction, "compresso.IncompleteExtractionError",
       &errors->error},
      {&errors->trailing_data_warning, "compresso.TrailingDataWarning",
       &PyExc_UserWarning},
  };

  for (size_t i = 0; i < sizeof exceptions / sizeof exceptions[0]; i++) {
    PyObject *base = exceptions[i].base ? *exceptions[i].base : NULL;
    *exceptions[i].slot = PyErr_NewException(exceptions[i].name, base, NULL);
    if (*exceptions[i].slot == NULL ||
        PyModule_AddObjectRef(module, strrchr(exceptions[i].name, '.') + 1,
                              *exceptions[i].slot) < 0) {
      return -1;
    }
  }

  return PyModule_AddType(module, &CancelTokenType);
}

static int core_traverse(PyObject *module, visitproc visit, void *arg) {
  CompErrors *errors = PyModule_GetState(module);
  Py_VISIT(errors->error);
  Py_VISIT(errors->header);
  Py_VISIT(errors->backend);
  Py_VISIT(errors->corrupt_data);
  Py_VISIT(errors->extraction_policy);
  Py_VISIT(errors->incomplete_extraction);
  Py_VISIT(errors->cancelled);
  Py_VISIT(errors->trailing_data_warning);
  return 0;
}

static int core_clear(PyObject *module) {
  CompErrors *errors = PyModule_GetState(module);
  Py_CLEAR(errors->error);
  Py_CLEAR(errors->header);
  Py_CLEAR(errors->backend);
  Py_CLEAR(errors->corrupt_data);
  Py_CLEAR(errors->extraction_policy);
  Py_CLEAR(errors->incomplete_extraction);
  Py_CLEAR(errors->cancelled);
  Py_CLEAR(errors->trailing_data_warning);
  return 0;
}

static void core_free(void *module) { core_clear((PyObject *)module); }

static PyModuleDef_Slot core_slots[] = {
    {Py_mod_exec, core_exec},
#if PY_VERSION_HEX >= 0x030C0000
    // Progress and log bridges take the GIL through PyGILState, which only
    // knows the main interpreter
    {Py_mod_multiple_interpreters, Py_MOD_MULTIPLE_INTERPRETERS_NOT_SUPPORTED},
#endif
    {0, NULL},
};

static struct PyModuleDef coremodule = {
    PyModuleDef_HEAD_INIT,
    .m_name = "_core",
    .m_doc = "Compresso core extension",
    .m_size = sizeof(CompErrors),
    .m_methods = CoreMethods,
    .m_slots = core_slots,
    .m_traverse = core_traverse,
    .m_clear = core_clear,
    .m_free = core_free,
};

PyMODINIT_FUNC PyInit__core(void) { return PyModuleDef_Init(&coremodule); }
