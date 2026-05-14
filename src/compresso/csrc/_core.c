#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "common.h"

// Error Objects
PyObject *comp_Error;
PyObject *comp_HeaderError;
PyObject *comp_BackendError;


// ---- Module Methods ----

static PyObject *
py_compress_file(PyObject *self __attribute__((unused)), PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"src_path", "dst_path", "algo", "strategy", "level", NULL};

    PyObject *src_path_obj;
    PyObject *dst_path_obj;
    const char *algo_name = NULL;
    const char *strategy_name = NULL;
    int level = -1;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs , "OO|ssi", kwlist,
                                     &src_path_obj, &dst_path_obj,
                                     &algo_name, &strategy_name, &level))
    {
        return NULL; // Error already set
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
        PyErr_Format(PyExc_ValueError, "Unknown compression algorithm: %s", algo_name);
        Py_DECREF(src_path_bytes);
        Py_DECREF(dst_path_bytes);
        return NULL;
    }

    if (compress_file(src_path, dst_path, algo, strat, level) != 0) {
        Py_DECREF(src_path_bytes);
        Py_DECREF(dst_path_bytes);
        return NULL; // Error already set
    }

    Py_DECREF(src_path_bytes);
    Py_DECREF(dst_path_bytes);
    return PyLong_FromLong(0);
}

static PyObject *
py_decompress_file(PyObject *self __attribute__((unused)), PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"src_path", "dst_path", "algo", NULL};

    PyObject *src_path_obj;
    PyObject *dst_path_obj;
    const char *algo_name = NULL;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs , "OO|s", kwlist,
                                     &src_path_obj, &dst_path_obj,
                                     &algo_name))
    {
        return NULL; // Error already set
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
        PyErr_Format(PyExc_ValueError, "Unknown decompression algorithm: %s", algo_name);
        Py_DECREF(src_path_bytes);
        Py_DECREF(dst_path_bytes);
        return NULL;
    }

    if (decompress_file(src_path, dst_path, algo) != 0) {
        Py_DECREF(src_path_bytes);
        Py_DECREF(dst_path_bytes);
        return NULL; // Error already set
    }

    Py_DECREF(src_path_bytes);
    Py_DECREF(dst_path_bytes);
    return PyLong_FromLong(0);
}


// ---- Archive Operations ----

static PyObject *
py_create_archive(PyObject *self __attribute__((unused)), PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"output_path", "format", "input_paths", "compression_level", NULL};

    const char *output_path = NULL;
    const char *format_name = NULL;
    PyObject *input_paths_obj = NULL;
    int compression_level = -1;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs , "ssO|i", kwlist,
                                     &output_path, &format_name, &input_paths_obj,
                                     &compression_level))
    {
        return NULL; // Error already set
    }

    Format format = format_from_name(format_name);
    if (format == FORMAT_UNKNOWN) {
        PyErr_Format(PyExc_ValueError, "Unknown archive format: %s", format_name);
        return NULL;
    }

    if (!PyList_Check(input_paths_obj)) {
        PyErr_SetString(PyExc_TypeError, "input_paths must be a list");
        return NULL;
    }

    Py_ssize_t num_paths = PyList_Size(input_paths_obj);
    const char **input_paths = safe_malloc(num_paths * sizeof(char *));
    if (!input_paths) {
        return NULL;
    }

    for (Py_ssize_t i = 0; i < num_paths; i++) {
        PyObject *item = PyList_GetItem(input_paths_obj, i);
        if (!PyUnicode_Check(item)) {
            free(input_paths);
            PyErr_SetString(PyExc_TypeError, "input_paths must be a list of strings");
            return NULL;
        }
        input_paths[i] = PyUnicode_AsUTF8(item);
    }

    int result = create_archive(output_path, format, input_paths, (size_t)num_paths, compression_level);
    free(input_paths);
    if (result != 0) {
        return NULL; // Error already set
    }

    Py_RETURN_NONE;
}

static PyObject *
py_extract_archive(PyObject *self __attribute__((unused)), PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"archive_path", "output_dir", "files", NULL};

    const char *archive_path = NULL;
    const char *output_dir = NULL;
    PyObject *files_obj = NULL;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "ssO", kwlist,
                                     &archive_path, &output_dir, &files_obj))
    {
        return NULL; // Error already set
    }

    const char **files = NULL;
    size_t num_files = 0;

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

    int result = extract_archive(archive_path, output_dir, files, num_files);
    if (files) free(files);
    if (result != 0) {
        return NULL; // Error already set
    }

    Py_RETURN_NONE;
}

static PyObject *
py_list_archive_contents(PyObject *self __attribute__((unused)), PyObject *args)
{
    const char *archive_path = NULL;

    if (!PyArg_ParseTuple(args, "s", &archive_path)) {
        return NULL; // Error already set
    }

    PyObject *file_list = list_archive_contents(archive_path);
    if (!file_list) {
        return NULL; // Error already set
    }

    return file_list;
}


// ---- Standalone Methods ----

static PyObject *
py_compress_standalone(PyObject *self __attribute__((unused)), PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"input_path", "output_path", "format", "compression_level", NULL};

    const char *input_path = NULL;
    const char *output_path = NULL;
    const char *format_name = NULL;
    int compression_level = -1;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "sss|i", kwlist,
                                     &input_path, &output_path, &format_name, &compression_level))
    {
        return NULL; // Error already set
    }

    Format format = format_from_name(format_name);
    if (format == FORMAT_UNKNOWN) {
        PyErr_Format(PyExc_ValueError, "Unknown standalone format: %s", format_name);
        return NULL;
    }

    const StandaloneFormat *fmt = find_standalone_format(format);
    if (!fmt) {
        PyErr_Format(PyExc_ValueError, "Format not supported: %s", format);
        return NULL;
    }

    if (fmt->compress_file(input_path, output_path, compression_level) != 0) {
        return NULL; // Error already set
    }

    Py_RETURN_NONE;
}

static PyObject *
py_decompress_standalone(PyObject *self __attribute__((unused)), PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"input_path", "output_path", "format", NULL};

    const char *input_path = NULL;
    const char *output_path = NULL;
    const char *format_name = NULL;

    Format format = FORMAT_UNKNOWN;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "ss|s", kwlist,
                                     &input_path, &output_path, &format))
    {
        return NULL; // Error already set
    }

    if (format_name) {
        format = format_from_name(format_name);

        if (format == FORMAT_UNKNOWN) {
            PyErr_Format(PyExc_ValueError, "Unknown format: %s", format_name);
            return NULL;
        }
    } else {
        format = detect_format_from_path(input_path);

        if (format == FORMAT_UNKNOWN) {
            PyErr_Format(PyExc_ValueError, "Could not detect format for: %s", input_path);
            return NULL;
        }
    }

    const StandaloneFormat *fmt = find_standalone_format(format);

    if (!fmt) {
        PyErr_Format(PyExc_ValueError, "Unsupported format: %s", format);
        return NULL;
    }

    if (fmt->decompress_file(input_path, output_path) != 0) {
        return NULL; // Error already set
    }

    Py_RETURN_NONE;
}


// ---- Format Detection Methods ----

static PyObject *
py_detect_format(PyObject *self __attribute__((unused)), PyObject *args)
{
    const char *file_path = NULL;

    if (!PyArg_ParseTuple(args, "s", &file_path)) {
        return NULL; // Error already set
    }

    Format format = detect_format_from_path(file_path);
    const char *format_name = format_name_string(format);

    return PyUnicode_FromString(format_name);
}

static PyObject *
py_format_is_archive(PyObject *self __attribute__((unused)), PyObject *args)
{
    const char *format_name = NULL;

    if (!PyArg_ParseTuple(args, "s", &format_name)) {
        return NULL; // Error already set
    }

    Format format = format_from_name(format_name);

    if (format_is_archive(format)) {
        Py_RETURN_TRUE;
    }

    Py_RETURN_FALSE;
}


// ---- Capabilities Methods ----

static PyObject *
py_get_capabilities(PyObject *self __attribute__((unused)), PyObject *Py_UNUSED(ignored))
{
    return get_capabilities();
}

static PyObject *
py_archive_capabilities(PyObject *self __attribute__((unused)), PyObject *Py_UNUSED(ignored))
{
    return get_archive_capabilities();
}

static PyObject *
py_get_default_backend_for_strategy(PyObject *self __attribute__((unused)), PyObject *args)
{
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
    {"compress_file", (PyCFunction)py_compress_file, METH_VARARGS | METH_KEYWORDS,
     "Compress a file using the specified algorithm and strategy."},
    {"decompress_file", (PyCFunction)py_decompress_file, METH_VARARGS | METH_KEYWORDS,
     "Decompress a file using the specified algorithm."},

    {"create_archive", (PyCFunction)py_create_archive, METH_VARARGS | METH_KEYWORDS,
     "Create a new archive from a list of files."},
    {"extract_archive", (PyCFunction)py_extract_archive, METH_VARARGS | METH_KEYWORDS,
     "Extract an archive file to a specified directory."},
    {"list_archive_contents", (PyCFunction)py_list_archive_contents, METH_VARARGS | METH_KEYWORDS,
     "List the contents of an archive file."},

    {"compress_standalone", (PyCFunction)py_compress_standalone, METH_VARARGS | METH_KEYWORDS,
     "Compress a file using a standalone compression format."},
    {"decompress_standalone", (PyCFunction)py_decompress_standalone, METH_VARARGS | METH_KEYWORDS,
     "Decompress a standalone format file."},

    {"detect_format", (PyCFunction)py_detect_format, METH_VARARGS,
     "Detect the format of a file."},
    {"format_is_archive", (PyCFunction)py_format_is_archive, METH_VARARGS,
     "Check if a format is an archive format."},
     
    {"get_capabilities", (PyCFunction)py_get_capabilities, METH_NOARGS,
     "Get the capabilities of available compression backends."},
    {"archive_capabilities", (PyCFunction)py_archive_capabilities, METH_NOARGS,
     "Get the capabilities of available archive backends."},
    {"get_default_backend_for_strategy", (PyCFunction)py_get_default_backend_for_strategy, METH_VARARGS,
     "Get the default backend for a given strategy, None if no backend is available."},

    {NULL, NULL, 0, NULL}  // Sentinel
};

#pragma GCC diagnostic pop

static struct PyModuleDef coremodule = {
    PyModuleDef_HEAD_INIT,
    "_core",
    "Compresso core extension",
    -1,
    CoreMethods,
    NULL,  // m_slots
    NULL,  // m_traverse
    NULL,  // m_clear
    NULL   // m_free
};


// ---- Module Initialisation ----

PyMODINIT_FUNC
PyInit__core(void)
{
    PyObject *module = PyModule_Create(&coremodule);
    if (module == NULL) {
        return NULL;
    }

    comp_Error = PyErr_NewException("compresso.Error", NULL, NULL);
    if (!comp_Error) {
        Py_DECREF(module);
        return NULL;
    }

    comp_HeaderError = PyErr_NewException("compresso.HeaderError", comp_Error, NULL);
    if (!comp_HeaderError) {
        Py_DECREF(comp_Error);
        Py_DECREF(module);
        return NULL;
    }

    comp_BackendError = PyErr_NewException("compresso.BackendError", comp_Error, NULL);
    if (!comp_BackendError) {
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

    return module;
}
