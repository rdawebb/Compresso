#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "codec/crc32c.h"
#include "common.h"
#include "fsutil.h"
#include "standalone.h"
#include "validate.h"

// Error Objects
PyObject *comp_Error;
PyObject *comp_HeaderError;
PyObject *comp_BackendError;
PyObject *comp_CorruptDataError;
PyObject *comp_ExtractionPolicyError;
PyObject *comp_Cancelled;
PyObject *comp_TrailingDataWarning;

// ---- Cancel Token ----

// A cancellation flag the C loops can poll without holding the GIL
typedef struct {
  PyObject_HEAD
  // One-way and a single machine word
  volatile int flag;
} CancelTokenObject;

static PyObject *cancel_token_cancel(PyObject *self, PyObject *ignored UNUSED) {
  ((CancelTokenObject *)self)->flag = 1;
  Py_RETURN_NONE;
}

static PyObject *cancel_token_get_cancelled(PyObject *self,
                                            void *closure UNUSED) {
  return PyBool_FromLong(((CancelTokenObject *)self)->flag);
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

// Populates `ctx` from the optional progress=, cancel= and threads= arguments;
// returns -1 with an exception set if one is invalid
//
// The bridge is installed even without progress= or cancel=, since it is also
// where PyErr_CheckSignals runs
static int core_context_init(CoreContext *ctx, PyObject *progress,
                             PyObject *cancel, int threads) {
  memset(ctx, 0, sizeof(*ctx));

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

// Call only on a non-zero return; an exception already raised by the callback
// or a signal handler is more specific
static void set_cancelled_error(int return_code) {
  if (return_code == COMP_CANCELLED && !PyErr_Occurred()) {
    PyErr_SetString(comp_Cancelled, "Operation cancelled");
  }
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

static PyObject *py_compress_file(PyObject *self UNUSED, PyObject *args,
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

  const char *src_path = PyBytes_AS_STRING(src_path_bytes);
  const char *dst_path = PyBytes_AS_STRING(dst_path_bytes);

  if (validate_overwrite_arg(overwrite_existing) != 0) {
    Py_DECREF(src_path_bytes);
    Py_DECREF(dst_path_bytes);
    return NULL;
  }

  CoreContext ctx;
  if (core_context_init(&ctx, progress, cancel, threads) != 0) {
    Py_DECREF(src_path_bytes);
    Py_DECREF(dst_path_bytes);
    return NULL;
  }

  AlgoID algo = algo_from_string(algo_name);
  Strategy strat;

  if (algo_name && algo_name[0] != '\0' && algo == ALGO_NONE) {
    PyErr_Format(PyExc_ValueError, "Unknown compression algorithm: %s",
                 algo_name);
    Py_DECREF(src_path_bytes);
    Py_DECREF(dst_path_bytes);
    return NULL;
  }

  if (parse_strategy(strategy_name, &strat) != 0) {
    Py_DECREF(src_path_bytes);
    Py_DECREF(dst_path_bytes);
    return NULL;
  }

  if (validate_compression_request(algo, strat, level, NULL) != 0) {
    Py_DECREF(src_path_bytes);
    Py_DECREF(dst_path_bytes);
    return NULL;
  }

  char actual_path[FS_PATH_MAX];
  int return_code =
      compress_file(src_path, dst_path, algo, strat, level, overwrite_existing,
                    actual_path, sizeof(actual_path), &ctx);

  Py_DECREF(src_path_bytes);
  Py_DECREF(dst_path_bytes);

  if (return_code != 0) {
    set_cancelled_error(return_code);
    return NULL; // Error already set
  }

  if (ctx_finish(&ctx) != 0) {
    return NULL;
  }

  // The path actually written, which RENAME may have changed vs asked for
  return PyUnicode_DecodeFSDefault(actual_path);
}

static PyObject *py_decompress_file(PyObject *self UNUSED, PyObject *args,
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

  const char *src_path = PyBytes_AS_STRING(src_path_bytes);
  const char *dst_path = PyBytes_AS_STRING(dst_path_bytes);

  if (validate_overwrite_arg(overwrite_existing) != 0) {
    Py_DECREF(src_path_bytes);
    Py_DECREF(dst_path_bytes);
    return NULL;
  }

  CoreContext ctx;
  if (core_context_init(&ctx, progress, cancel, threads) != 0) {
    Py_DECREF(src_path_bytes);
    Py_DECREF(dst_path_bytes);
    return NULL;
  }

  AlgoID algo = algo_from_string(algo_name);

  if (algo_name && algo_name[0] != '\0' && algo == ALGO_NONE) {
    PyErr_Format(PyExc_ValueError, "Unknown decompression algorithm: %s",
                 algo_name);
    Py_DECREF(src_path_bytes);
    Py_DECREF(dst_path_bytes);
    return NULL;
  }

  char actual_path[FS_PATH_MAX];
  int return_code =
      decompress_file(src_path, dst_path, algo, overwrite_existing, actual_path,
                      sizeof(actual_path), &ctx);

  Py_DECREF(src_path_bytes);
  Py_DECREF(dst_path_bytes);

  if (return_code != 0) {
    set_cancelled_error(return_code);
    return NULL; // Error already set
  }

  if (ctx_finish(&ctx) != 0) {
    return NULL;
  }

  // The path actually written, which RENAME may have changed vs asked for
  return PyUnicode_DecodeFSDefault(actual_path);
}

// ---- Archive Operations ----

static PyObject *py_create_archive(PyObject *self UNUSED, PyObject *args,
                                   PyObject *kwargs) {
  static char *kwlist[] = {"output_path",       "format",    "input_paths",
                           "compression_level", "overwrite", "threads",
                           "progress",          "cancel",    NULL};

  PyObject *output_path_bytes = NULL;
  const char *format_name = NULL;
  PathList inputs = {0};
  int compression_level = -1;
  int overwrite_existing = 0; // ERROR by default; frontends opt into RENAME
  int threads = 0;            // One per core
  PyObject *progress = NULL;
  PyObject *cancel = NULL;

  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O&sO&|i$iiOO", kwlist,
                                   PyUnicode_FSConverter, &output_path_bytes,
                                   &format_name, path_list_converter, &inputs,
                                   &compression_level, &overwrite_existing,
                                   &threads, &progress, &cancel)) {
    return NULL; // Error already set
  }

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
      core_context_init(&ctx, progress, cancel, threads) != 0) {
    goto done;
  }

  char actual_path[FS_PATH_MAX];
  result = create_archive(PyBytes_AS_STRING(output_path_bytes), &pipe,
                          inputs.paths, inputs.count, overwrite_existing,
                          actual_path, sizeof(actual_path), &ctx);

done:
  Py_DECREF(output_path_bytes);
  path_list_converter(NULL, &inputs);
  if (result != 0) {
    set_cancelled_error(result);
    return NULL; // Error already set
  }

  if (ctx_finish(&ctx) != 0) {
    return NULL;
  }

  // The path actually written, which RENAME may have changed from what was
  // asked for
  return PyUnicode_DecodeFSDefault(actual_path);
}

static PyObject *py_extract_archive(PyObject *self UNUSED, PyObject *args,
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
                           NULL};

  PyObject *archive_path_bytes = NULL;
  PyObject *output_dir_bytes = NULL;
  PyObject *files_obj = NULL;
  int threads = 0; // One per core
  PyObject *progress = NULL;
  PyObject *cancel = NULL;

  // Anything the caller does not pass keeps its default
  ExtractionPolicy policy = extraction_policy_default();

  // K and I write through exactly-sized pointers, so they cannot target the
  // struct's fixed-width fields directly
  unsigned long long max_total_size = (unsigned long long)policy.max_total_size;
  unsigned int max_depth = (unsigned int)policy.max_depth;

  if (!PyArg_ParseTupleAndKeywords(
          args, kwargs, "O&O&O|$iKIpppipiOO", kwlist, PyUnicode_FSConverter,
          &archive_path_bytes, PyUnicode_FSConverter, &output_dir_bytes,
          &files_obj, &policy.overwrite_existing, &max_total_size, &max_depth,
          &policy.preserve_permissions, &policy.exact_permissions,
          &policy.preserve_timestamps, &policy.allow_symlinks,
          &policy.overwrite_dir_metadata, &threads, &progress, &cancel)) {
    return NULL; // Error already set
  }

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

  if (policy.allow_symlinks < 0 || policy.allow_symlinks > 2) {
    PyErr_Format(PyExc_ValueError,
                 "allow_symlinks must be 0 (deny), 1 (allow) or 2 (rewrite), "
                 "not %d",
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

  if (core_context_init(&ctx, progress, cancel, threads) != 0) {
    goto done;
  }

  result = extract_archive(PyBytes_AS_STRING(archive_path_bytes),
                           PyBytes_AS_STRING(output_dir_bytes), files,
                           num_files, &policy, &ctx);

done:
  Py_DECREF(archive_path_bytes);
  Py_DECREF(output_dir_bytes);
  free(files);
  Py_XDECREF(files_tuple);
  if (result != 0) {
    set_cancelled_error(result);
    return NULL; // Error already set
  }

  if (ctx_finish(&ctx) != 0) {
    return NULL;
  }

  Py_RETURN_NONE;
}

static PyObject *py_list_archive_contents(PyObject *self UNUSED,
                                          PyObject *args) {
  PyObject *archive_path_bytes = NULL;

  if (!PyArg_ParseTuple(args, "O&", PyUnicode_FSConverter,
                        &archive_path_bytes)) {
    return NULL; // Error already set
  }

  // No progress to report, but the bridge still logs and checks for signals
  CoreContext ctx;
  core_context_init(&ctx, NULL, NULL, 0);

  PyObject *file_list =
      list_archive_contents(PyBytes_AS_STRING(archive_path_bytes), &ctx);
  Py_DECREF(archive_path_bytes);
  return file_list; // NULL propagates the already-set exception
}

// ---- Standalone Methods ----

static PyObject *py_compress_standalone(PyObject *self UNUSED, PyObject *args,
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
  if (validate_overwrite_arg(overwrite_existing) != 0 ||
      core_context_init(&ctx, progress, cancel, threads) != 0) {
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

  if (validate_level(fmt->name, fmt->levels, compression_level) != 0) {
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
  if (rc != 0) {
    set_cancelled_error(rc);
    return NULL; // Error already set
  }

  if (ctx_finish(&ctx) != 0) {
    return NULL;
  }

  // The path actually written, which RENAME may have changed from what was
  // asked for
  return PyUnicode_DecodeFSDefault(actual_path);
}

static PyObject *py_decompress_standalone(PyObject *self UNUSED, PyObject *args,
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
  CoreContext ctx;
  if (validate_overwrite_arg(overwrite_existing) != 0 ||
      core_context_init(&ctx, progress, cancel, threads) != 0) {
    goto fail;
  }

  src = open_source(input_path, &format);
  if (!src) {
    goto fail;
  }

  if (format_name) {
    const FormatDesc *desc = format_by_name(format_name);
    if (!desc) {
      PyErr_Format(PyExc_ValueError, "Unknown format: %s", format_name);
      goto fail;
    }
    format = desc->id;
  } else if (format == FORMAT_UNKNOWN) {
    PyErr_Format(PyExc_ValueError, "Could not detect format for: %s",
                 input_path);
    goto fail;
  }

  const FormatDesc *fmt = find_standalone_format(format);

  if (!fmt) {
    PyErr_Format(PyExc_ValueError, "Format cannot decompress a single file: %s",
                 format_by_id(format)->name);
    goto fail;
  }

  char actual_path[FS_PATH_MAX];
  OutputTarget out = {.path = PyBytes_AS_STRING(output_path_bytes),
                      .overwrite = overwrite_existing,
                      .actual = actual_path,
                      .actual_size = sizeof(actual_path),
                      .keep_source_metadata = 1};
  int rc = standalone_decompress(fmt, input_path, src, &out, &ctx);
  src = NULL;
  if (rc != 0) {
    set_cancelled_error(rc);
    goto fail; // Error already set
  }

  Py_DECREF(input_path_bytes);
  Py_DECREF(output_path_bytes);

  if (ctx_finish(&ctx) != 0) {
    return NULL;
  }

  // The path actually written, which RENAME may have changed vs asked for
  return PyUnicode_DecodeFSDefault(actual_path);

fail:
  if (src) {
    fclose(src);
  }
  Py_DECREF(input_path_bytes);
  Py_DECREF(output_path_bytes);
  return NULL;
}

// ---- Format Detection Methods ----

static PyObject *py_detect_format(PyObject *self UNUSED, PyObject *args) {
  PyObject *file_path_bytes = NULL;

  if (!PyArg_ParseTuple(args, "O&", PyUnicode_FSConverter, &file_path_bytes)) {
    return NULL; // Error already set
  }

  const char *file_path = PyBytes_AS_STRING(file_path_bytes);
  CompressionPipeline pipe =
      pipeline_from_format(detect_format_from_path(file_path), file_path);
  char name[32];
  pipeline_display_name(&pipe, name, sizeof(name));

  Py_DECREF(file_path_bytes);
  return PyUnicode_FromString(name);
}

static PyObject *py_format_is_archive(PyObject *self UNUSED, PyObject *args) {
  const char *format_name = NULL;

  if (!PyArg_ParseTuple(args, "s", &format_name)) {
    return NULL; // Error already set
  }

  CompressionPipeline pipe = pipeline_from_name(format_name, -1);

  if (pipe.archive != ARCHIVE_NONE) {
    Py_RETURN_TRUE;
  }

  Py_RETURN_FALSE;
}

// ---- Capabilities Methods ----

static PyObject *py_get_capabilities(PyObject *self UNUSED,
                                     PyObject *Py_UNUSED(ignored)) {
  return get_capabilities();
}

static PyObject *py_get_archive_capabilities(PyObject *self UNUSED,
                                             PyObject *Py_UNUSED(ignored)) {
  return get_archive_capabilities();
}

static PyObject *py_get_default_backend_for_strategy(PyObject *self UNUSED,
                                                     PyObject *args) {
  const char *strategy_name = NULL;

  if (!PyArg_ParseTuple(args, "|s", &strategy_name)) {
    return NULL; // Error already set
  }

  Strategy strat;
  if (parse_strategy(strategy_name, &strat) != 0) {
    return NULL;
  }
  const char *name = get_default_backend_for_strategy(strat);

  if (!name) {
    Py_RETURN_NONE;
  }

  return PyUnicode_FromString(name);
}

// The same level check compress_file, compress_standalone and create_archive
// make, without doing any work, so a plan can refuse a level up front
static PyObject *py_check_level(PyObject *self UNUSED, PyObject *args,
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
    {"format_is_archive", (PyCFunction)py_format_is_archive, METH_VARARGS,
     "Check if a format is an archive format."},

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

static struct PyModuleDef coremodule = {
    PyModuleDef_HEAD_INIT,
    "_core",
    "Compresso core extension",
    -1,
    CoreMethods,
    NULL, // m_slots
    NULL, // m_traverse
    NULL, // m_clear
    NULL  // m_free
};

// ---- Module Initialisation ----

PyMODINIT_FUNC PyInit__core(void) {
  crc32c_init();

  PyObject *module = PyModule_Create(&coremodule);
  if (module == NULL) {
    return NULL;
  }

  // Built at run time: on Windows, &PyExc_UserWarning isn't a constant
  // expression; each base must precede the exceptions derived from it.
  const struct {
    PyObject **slot;
    const char *name;
    PyObject **base;
  } exceptions[] = {
      {&comp_Error, "compresso.Error", NULL},
      {&comp_HeaderError, "compresso.HeaderError", &comp_Error},
      {&comp_BackendError, "compresso.BackendError", &comp_Error},
      {&comp_Cancelled, "compresso.Cancelled", &comp_Error},
      {&comp_CorruptDataError, "compresso.CorruptDataError", &comp_Error},
      {&comp_ExtractionPolicyError, "compresso.ExtractionPolicyError",
       &comp_Error},
      {&comp_TrailingDataWarning, "compresso.TrailingDataWarning",
       &PyExc_UserWarning},
  };
  const size_t n_exceptions = sizeof exceptions / sizeof exceptions[0];

  for (size_t i = 0; i < n_exceptions; i++) {
    PyObject *base = exceptions[i].base ? *exceptions[i].base : NULL;
    *exceptions[i].slot = PyErr_NewException(exceptions[i].name, base, NULL);
    if (*exceptions[i].slot == NULL ||
        PyModule_AddObjectRef(module, strrchr(exceptions[i].name, '.') + 1,
                              *exceptions[i].slot) < 0) {
      goto fail;
    }
  }

  if (PyModule_AddType(module, &CancelTokenType) < 0) {
    goto fail;
  }

  return module;

fail:
  for (size_t i = 0; i < n_exceptions; i++) {
    Py_CLEAR(*exceptions[i].slot);
  }
  Py_DECREF(module);
  return NULL;
}
