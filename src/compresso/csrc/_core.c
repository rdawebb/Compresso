#define PY_SSIZE_T_CLEAN
#include "common.h"
#include "standalone.h"
#include "validate.h"
#include <Python.h>

// Error Objects
PyObject *comp_Error;
PyObject *comp_HeaderError;
PyObject *comp_BackendError;
PyObject *comp_Cancelled;

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

// Populates `ctx` from the optional progress= and cancel= arguments; returns -1
// with an exception set if either is of the wrong type
//
// The bridge is installed even when neither is given, since it is also where
// PyErr_CheckSignals runs
static int core_context_init(CoreContext *ctx, PyObject *progress,
                             PyObject *cancel) {
  memset(ctx, 0, sizeof(*ctx));
  ctx->on_progress = progress_bridge;

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

// ---- Path Encoding Helpers ----

// Returns a new bytes reference (caller Py_DECREFs) with *out pointing into its
// buffer; PyUnicode_EncodeFSDefault honours the configured filesystem encoding
static PyObject *encode_fs_path(PyObject *obj, const char **out) {
  PyObject *bytes = PyUnicode_EncodeFSDefault(obj);
  if (!bytes)
    return NULL;
  *out = PyBytes_AsString(bytes);
  return bytes;
}

// Returns a list holding the backing bytes objects; *out_paths (caller frees)
// and *out_count are filled on success
static PyObject *encode_fs_path_list(PyObject *list, const char ***out_paths,
                                     size_t *out_count) {
  Py_ssize_t n = PyList_Size(list);
  const char **paths = NULL;
  if (n > 0) {
    paths = safe_malloc((size_t)n * sizeof(char *));
    if (!paths)
      return NULL;
  }

  PyObject *keepalive = PyList_New(n);
  if (!keepalive) {
    free(paths);
    return NULL;
  }

  for (Py_ssize_t i = 0; i < n; i++) {
    PyObject *item = PyList_GetItem(list, i); // borrowed
    if (!PyUnicode_Check(item)) {
      PyErr_SetString(PyExc_TypeError, "input_paths must be a list of strings");
      Py_DECREF(keepalive);
      free(paths);
      return NULL;
    }
    PyObject *bytes = PyUnicode_EncodeFSDefault(item);
    if (!bytes) {
      Py_DECREF(keepalive);
      free(paths);
      return NULL;
    }
    PyList_SET_ITEM(keepalive, i, bytes); // steals reference
    paths[i] = PyBytes_AsString(bytes);
  }

  *out_paths = paths;
  *out_count = (size_t)n;
  return keepalive;
}

// ---- Module Methods ----

static PyObject *py_compress_file(PyObject *self UNUSED, PyObject *args,
                                  PyObject *kwargs) {
  static char *kwlist[] = {"src_path", "dst_path", "algo",   "strategy",
                           "level",    "progress", "cancel", NULL};

  PyObject *src_path_obj;
  PyObject *dst_path_obj;
  const char *algo_name = NULL;
  const char *strategy_name = NULL;
  int level = -1;
  PyObject *progress = NULL;
  PyObject *cancel = NULL;

  if (!PyArg_ParseTupleAndKeywords(
          args, kwargs, "OO|ssi$OO", kwlist, &src_path_obj, &dst_path_obj,
          &algo_name, &strategy_name, &level, &progress, &cancel)) {
    return NULL; // Error already set
  }

  CoreContext ctx;
  if (core_context_init(&ctx, progress, cancel) != 0) {
    return NULL;
  }

  PyObject *src_path_bytes = PyUnicode_EncodeFSDefault(src_path_obj);
  PyObject *dst_path_bytes = PyUnicode_EncodeFSDefault(dst_path_obj);
  if (!src_path_bytes || !dst_path_bytes) {
    Py_XDECREF(src_path_bytes);
    Py_XDECREF(dst_path_bytes);
    return NULL; // Error already set
  }

  const char *src_path = PyBytes_AsString(src_path_bytes);
  const char *dst_path = PyBytes_AsString(dst_path_bytes);

  AlgoID algo = algo_from_string(algo_name);
  Strategy strat = strategy_from_string(strategy_name);

  if (algo_name && algo_name[0] != '\0' && algo == ALGO_NONE) {
    PyErr_Format(PyExc_ValueError, "Unknown compression algorithm: %s",
                 algo_name);
    Py_DECREF(src_path_bytes);
    Py_DECREF(dst_path_bytes);
    return NULL;
  }

  if (validate_compression_request(algo, strat, level, NULL) != 0) {
    Py_DECREF(src_path_bytes);
    Py_DECREF(dst_path_bytes);
    return NULL;
  }

  int return_code = compress_file(src_path, dst_path, algo, strat, level, &ctx);

  Py_DECREF(src_path_bytes);
  Py_DECREF(dst_path_bytes);

  if (return_code != 0) {
    set_cancelled_error(return_code);
    return NULL; // Error already set
  }

  if (ctx_finish(&ctx) != 0) {
    return NULL;
  }

  return PyLong_FromLong(0);
}

static PyObject *py_decompress_file(PyObject *self UNUSED, PyObject *args,
                                    PyObject *kwargs) {
  static char *kwlist[] = {"src_path", "dst_path", "algo",
                           "progress", "cancel",   NULL};

  PyObject *src_path_obj;
  PyObject *dst_path_obj;
  const char *algo_name = NULL;
  PyObject *progress = NULL;
  PyObject *cancel = NULL;

  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "OO|s$OO", kwlist,
                                   &src_path_obj, &dst_path_obj, &algo_name,
                                   &progress, &cancel)) {
    return NULL; // Error already set
  }

  CoreContext ctx;
  if (core_context_init(&ctx, progress, cancel) != 0) {
    return NULL;
  }

  PyObject *src_path_bytes = PyUnicode_EncodeFSDefault(src_path_obj);
  PyObject *dst_path_bytes = PyUnicode_EncodeFSDefault(dst_path_obj);
  if (!src_path_bytes || !dst_path_bytes) {
    Py_XDECREF(src_path_bytes);
    Py_XDECREF(dst_path_bytes);
    return NULL; // Error already set
  }

  const char *src_path = PyBytes_AsString(src_path_bytes);
  const char *dst_path = PyBytes_AsString(dst_path_bytes);

  AlgoID algo = algo_from_string(algo_name);

  if (algo_name && algo_name[0] != '\0' && algo == ALGO_NONE) {
    PyErr_Format(PyExc_ValueError, "Unknown decompression algorithm: %s",
                 algo_name);
    Py_DECREF(src_path_bytes);
    Py_DECREF(dst_path_bytes);
    return NULL;
  }

  int return_code = decompress_file(src_path, dst_path, algo, &ctx);

  Py_DECREF(src_path_bytes);
  Py_DECREF(dst_path_bytes);

  if (return_code != 0) {
    set_cancelled_error(return_code);
    return NULL; // Error already set
  }

  if (ctx_finish(&ctx) != 0) {
    return NULL;
  }

  return PyLong_FromLong(0);
}

// ---- Archive Operations ----

static PyObject *py_create_archive(PyObject *self UNUSED, PyObject *args,
                                   PyObject *kwargs) {
  static char *kwlist[] = {"output_path", "format", "input_paths",
                           "compression_level", NULL};

  PyObject *output_path_obj = NULL;
  const char *format_name = NULL;
  PyObject *input_paths_obj = NULL;
  int compression_level = -1;

  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "OsO|i", kwlist,
                                   &output_path_obj, &format_name,
                                   &input_paths_obj, &compression_level)) {
    return NULL; // Error already set
  }

  CompressionPipeline pipe = pipeline_from_name(format_name, compression_level);
  if (pipe.archive == ARCHIVE_NONE && pipe.codec == FORMAT_UNKNOWN) {
    PyErr_Format(PyExc_ValueError, "Unknown archive format: %s", format_name);
    return NULL;
  }

  if (validate_compression_request(ALGO_NONE, STRAT_BALANCED, compression_level,
                                   &pipe) != 0) {
    return NULL;
  }

  if (!PyList_Check(input_paths_obj)) {
    PyErr_SetString(PyExc_TypeError, "input_paths must be a list");
    return NULL;
  }

  const char **input_paths = NULL;
  size_t num_paths = 0;
  PyObject *paths_keepalive =
      encode_fs_path_list(input_paths_obj, &input_paths, &num_paths);
  if (!paths_keepalive) {
    return NULL; // Error already set
  }

  const char *output_path = NULL;
  PyObject *output_path_bytes = encode_fs_path(output_path_obj, &output_path);
  if (!output_path_bytes) {
    Py_DECREF(paths_keepalive);
    free(input_paths);
    return NULL; // Error already set
  }

  int result = create_archive(output_path, &pipe, input_paths, num_paths);

  Py_DECREF(output_path_bytes);
  Py_DECREF(paths_keepalive);
  free(input_paths);
  if (result != 0) {
    return NULL; // Error already set
  }

  Py_RETURN_NONE;
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
                           "preserve_timestamps",
                           "allow_symlinks",
                           NULL};

  PyObject *archive_path_obj = NULL;
  PyObject *output_dir_obj = NULL;
  PyObject *files_obj = NULL;

  // Anything the caller does not pass keeps its default
  ExtractionPolicy policy = extraction_policy_default();

  // K and I write through exactly-sized pointers, so they cannot target the
  // struct's fixed-width fields directly
  unsigned long long max_total_size = (unsigned long long)policy.max_total_size;
  unsigned int max_depth = (unsigned int)policy.max_depth;

  if (!PyArg_ParseTupleAndKeywords(
          args, kwargs, "OOO|$iKIppi", kwlist, &archive_path_obj,
          &output_dir_obj, &files_obj, &policy.overwrite_existing,
          &max_total_size, &max_depth, &policy.preserve_permissions,
          &policy.preserve_timestamps, &policy.allow_symlinks)) {
    return NULL; // Error already set
  }

  policy.max_total_size = (uint64_t)max_total_size;
  policy.max_depth = (uint32_t)max_depth;

  if (policy.overwrite_existing < 0 || policy.overwrite_existing > 2) {
    PyErr_Format(PyExc_ValueError,
                 "overwrite must be 0 (error), 1 (skip) or 2 (overwrite), "
                 "not %d",
                 policy.overwrite_existing);
    return NULL;
  }

  if (policy.allow_symlinks < 0 || policy.allow_symlinks > 2) {
    PyErr_Format(PyExc_ValueError,
                 "allow_symlinks must be 0 (deny), 1 (allow) or 2 (rewrite), "
                 "not %d",
                 policy.allow_symlinks);
    return NULL;
  }

  const char **files = NULL;
  size_t num_files = 0;

  // `files` are archive-internal entry names matched against the stored (UTF-8)
  // paths, so they are decoded as UTF-8 rather than fs-encoded
  if (files_obj && PyList_Check(files_obj)) {
    num_files = PyList_Size(files_obj);
    if (num_files > 0) {
      files = safe_malloc(num_files * sizeof(char *));
      if (!files) {
        return NULL;
      }

      for (size_t i = 0; i < num_files; i++) {
        PyObject *item = PyList_GetItem(files_obj, i);
        files[i] = PyUnicode_AsUTF8(item);
      }
    }
  }

  const char *archive_path = NULL;
  const char *output_dir = NULL;
  PyObject *archive_path_bytes =
      encode_fs_path(archive_path_obj, &archive_path);
  PyObject *output_dir_bytes = encode_fs_path(output_dir_obj, &output_dir);
  if (!archive_path_bytes || !output_dir_bytes) {
    Py_XDECREF(archive_path_bytes);
    Py_XDECREF(output_dir_bytes);
    free(files);
    return NULL; // Error already set
  }

  int result =
      extract_archive(archive_path, output_dir, files, num_files, &policy);

  Py_DECREF(archive_path_bytes);
  Py_DECREF(output_dir_bytes);
  free(files);
  if (result != 0) {
    return NULL; // Error already set
  }

  Py_RETURN_NONE;
}

static PyObject *py_list_archive_contents(PyObject *self UNUSED,
                                          PyObject *args) {
  PyObject *archive_path_obj = NULL;

  if (!PyArg_ParseTuple(args, "O", &archive_path_obj)) {
    return NULL; // Error already set
  }

  const char *archive_path = NULL;
  PyObject *archive_path_bytes =
      encode_fs_path(archive_path_obj, &archive_path);
  if (!archive_path_bytes) {
    return NULL; // Error already set
  }

  PyObject *file_list = list_archive_contents(archive_path);
  Py_DECREF(archive_path_bytes);
  return file_list; // NULL propagates the already-set exception
}

// ---- Standalone Methods ----

static PyObject *py_compress_standalone(PyObject *self UNUSED, PyObject *args,
                                        PyObject *kwargs) {
  static char *kwlist[] = {
      "input_path", "output_path", "format", "compression_level",
      "progress",   "cancel",      NULL};

  PyObject *input_path_obj = NULL;
  PyObject *output_path_obj = NULL;
  const char *format_name = NULL;
  int compression_level = -1;
  PyObject *progress = NULL;
  PyObject *cancel = NULL;

  if (!PyArg_ParseTupleAndKeywords(
          args, kwargs, "OOs|i$OO", kwlist, &input_path_obj, &output_path_obj,
          &format_name, &compression_level, &progress, &cancel)) {
    return NULL; // Error already set
  }

  CoreContext ctx;
  if (core_context_init(&ctx, progress, cancel) != 0) {
    return NULL;
  }

  Format format = format_from_name(format_name);
  if (format == FORMAT_UNKNOWN) {
    PyErr_Format(PyExc_ValueError, "Unknown standalone format: %s",
                 format_name);
    return NULL;
  }

  const StandaloneFormat *fmt = find_standalone_format(format);
  if (!fmt) {
    PyErr_Format(PyExc_ValueError, "Format not supported: %s", format);
    return NULL;
  }

  const char *input_path = NULL;
  const char *output_path = NULL;
  PyObject *input_path_bytes = encode_fs_path(input_path_obj, &input_path);
  PyObject *output_path_bytes = encode_fs_path(output_path_obj, &output_path);
  if (!input_path_bytes || !output_path_bytes) {
    Py_XDECREF(input_path_bytes);
    Py_XDECREF(output_path_bytes);
    return NULL; // Error already set
  }

  int rc = fmt->compress_file(input_path, output_path, compression_level, &ctx);
  Py_DECREF(input_path_bytes);
  Py_DECREF(output_path_bytes);
  if (rc != 0) {
    set_cancelled_error(rc);
    return NULL; // Error already set
  }

  if (ctx_finish(&ctx) != 0) {
    return NULL;
  }

  Py_RETURN_NONE;
}

static PyObject *py_decompress_standalone(PyObject *self UNUSED, PyObject *args,
                                          PyObject *kwargs) {
  static char *kwlist[] = {"input_path", "output_path", "format",
                           "progress",   "cancel",      NULL};

  PyObject *input_path_obj = NULL;
  PyObject *output_path_obj = NULL;
  const char *format_name = NULL;
  PyObject *progress = NULL;
  PyObject *cancel = NULL;

  Format format = FORMAT_UNKNOWN;

  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "OO|s$OO", kwlist,
                                   &input_path_obj, &output_path_obj,
                                   &format_name, &progress, &cancel)) {
    return NULL; // Error already set
  }

  CoreContext ctx;
  if (core_context_init(&ctx, progress, cancel) != 0) {
    return NULL;
  }

  const char *input_path = NULL;
  const char *output_path = NULL;
  PyObject *input_path_bytes = encode_fs_path(input_path_obj, &input_path);
  PyObject *output_path_bytes = encode_fs_path(output_path_obj, &output_path);
  if (!input_path_bytes || !output_path_bytes) {
    goto fail; // Error already set
  }

  if (format_name) {
    format = format_from_name(format_name);

    if (format == FORMAT_UNKNOWN) {
      PyErr_Format(PyExc_ValueError, "Unknown format: %s", format_name);
      goto fail;
    }
  } else {
    format = detect_format_from_path(input_path);

    if (format == FORMAT_UNKNOWN) {
      PyErr_Format(PyExc_ValueError, "Could not detect format for: %s",
                   input_path);
      goto fail;
    }
  }

  const StandaloneFormat *fmt = find_standalone_format(format);

  if (!fmt) {
    PyErr_Format(PyExc_ValueError, "Unsupported format: %s", format);
    goto fail;
  }

  int rc = fmt->decompress_file(input_path, output_path, &ctx);
  if (rc != 0) {
    set_cancelled_error(rc);
    goto fail; // Error already set
  }

  Py_DECREF(input_path_bytes);
  Py_DECREF(output_path_bytes);

  if (ctx_finish(&ctx) != 0) {
    return NULL;
  }

  Py_RETURN_NONE;

fail:
  Py_XDECREF(input_path_bytes);
  Py_XDECREF(output_path_bytes);
  return NULL;
}

// ---- Format Detection Methods ----

static PyObject *py_detect_format(PyObject *self UNUSED, PyObject *args) {
  PyObject *file_path_obj = NULL;

  if (!PyArg_ParseTuple(args, "O", &file_path_obj)) {
    return NULL; // Error already set
  }

  const char *file_path = NULL;
  PyObject *file_path_bytes = encode_fs_path(file_path_obj, &file_path);
  if (!file_path_bytes) {
    return NULL; // Error already set
  }

  CompressionPipeline pipe = detect_pipeline_from_path(file_path);
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

  Strategy strat = strategy_from_string(strategy_name);
  const char *name = get_default_backend_for_strategy(strat);

  if (!name) {
    Py_RETURN_NONE;
  }

  return PyUnicode_FromString(name);
}

// ---- Module Definition ----

#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wcast-function-type-mismatch"

static PyMethodDef CoreMethods[] = {
    {"compress_file", (PyCFunction)py_compress_file,
     METH_VARARGS | METH_KEYWORDS,
     "Compress a file using the specified algorithm and strategy."},
    {"decompress_file", (PyCFunction)py_decompress_file,
     METH_VARARGS | METH_KEYWORDS,
     "Decompress a file using the specified algorithm."},

    {"create_archive", (PyCFunction)py_create_archive,
     METH_VARARGS | METH_KEYWORDS,
     "Create a new archive from a list of files."},
    {"extract_archive", (PyCFunction)py_extract_archive,
     METH_VARARGS | METH_KEYWORDS,
     "Extract an archive file to a specified directory, subject to the "
     "keyword-only extraction policy."},
    {"list_archive_contents", (PyCFunction)py_list_archive_contents,
     METH_VARARGS | METH_KEYWORDS, "List the contents of an archive file."},

    {"compress_standalone", (PyCFunction)py_compress_standalone,
     METH_VARARGS | METH_KEYWORDS,
     "Compress a file using a standalone compression format."},
    {"decompress_standalone", (PyCFunction)py_decompress_standalone,
     METH_VARARGS | METH_KEYWORDS, "Decompress a standalone format file."},

    {"detect_format", (PyCFunction)py_detect_format, METH_VARARGS,
     "Detect the format of a file."},
    {"format_is_archive", (PyCFunction)py_format_is_archive, METH_VARARGS,
     "Check if a format is an archive format."},

    {"get_capabilities", (PyCFunction)py_get_capabilities, METH_NOARGS,
     "Get the capabilities of available compression backends."},
    {"archive_capabilities", (PyCFunction)py_get_archive_capabilities,
     METH_NOARGS, "Get the capabilities of available archive backends."},
    {"get_default_backend_for_strategy",
     (PyCFunction)py_get_default_backend_for_strategy, METH_VARARGS,
     "Get the default backend for a given strategy, None if no backend is "
     "available."},

    {NULL, NULL, 0, NULL} // Sentinel
};

#pragma GCC diagnostic pop

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
  PyObject *module = PyModule_Create(&coremodule);
  if (module == NULL) {
    return NULL;
  }

  comp_Error = PyErr_NewException("compresso.Error", NULL, NULL);
  if (!comp_Error) {
    Py_DECREF(module);
    return NULL;
  }

  comp_HeaderError =
      PyErr_NewException("compresso.HeaderError", comp_Error, NULL);
  if (!comp_HeaderError) {
    Py_DECREF(comp_Error);
    Py_DECREF(module);
    return NULL;
  }

  comp_BackendError =
      PyErr_NewException("compresso.BackendError", comp_Error, NULL);
  if (!comp_BackendError) {
    Py_DECREF(comp_HeaderError);
    Py_DECREF(comp_Error);
    Py_DECREF(module);
    return NULL;
  }

  // Subclasses Error, not BaseException: the frontend jobs catch Exception to
  // honour their "never raises" contract, which a cancellation must not evade
  comp_Cancelled = PyErr_NewException("compresso.Cancelled", comp_Error, NULL);
  if (!comp_Cancelled) {
    Py_DECREF(comp_BackendError);
    Py_DECREF(comp_HeaderError);
    Py_DECREF(comp_Error);
    Py_DECREF(module);
    return NULL;
  }

  Py_INCREF(comp_Error);
  if (PyModule_AddObject(module, "Error", comp_Error) < 0) {
    Py_DECREF(comp_Error);
    Py_DECREF(comp_HeaderError);
    Py_DECREF(comp_BackendError);
    Py_DECREF(module);
    return NULL;
  }

  Py_INCREF(comp_HeaderError);
  if (PyModule_AddObject(module, "HeaderError", comp_HeaderError) < 0) {
    Py_DECREF(comp_Error);
    Py_DECREF(comp_HeaderError);
    Py_DECREF(comp_BackendError);
    Py_DECREF(module);
    return NULL;
  }

  Py_INCREF(comp_BackendError);
  if (PyModule_AddObject(module, "BackendError", comp_BackendError) < 0) {
    Py_DECREF(comp_Error);
    Py_DECREF(comp_HeaderError);
    Py_DECREF(comp_BackendError);
    Py_DECREF(module);
    return NULL;
  }

  Py_INCREF(comp_Cancelled);
  if (PyModule_AddObject(module, "Cancelled", comp_Cancelled) < 0) {
    Py_DECREF(comp_Error);
    Py_DECREF(comp_HeaderError);
    Py_DECREF(comp_BackendError);
    Py_DECREF(comp_Cancelled);
    Py_DECREF(module);
    return NULL;
  }

  if (PyType_Ready(&CancelTokenType) < 0) {
    Py_DECREF(module);
    return NULL;
  }

  Py_INCREF(&CancelTokenType);
  if (PyModule_AddObject(module, "CancelToken", (PyObject *)&CancelTokenType) <
      0) {
    Py_DECREF(&CancelTokenType);
    Py_DECREF(module);
    return NULL;
  }

  return module;
}
