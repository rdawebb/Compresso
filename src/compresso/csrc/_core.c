#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "common.h"

// Error Objects
PyObject *comp_Error;
PyObject *comp_HeaderError;
PyObject *comp_BackendError;


// ---- Module Methods ----

static PyObject *
py_compress_file(PyObject *self, PyObject *args, PyObject *kwargs)
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

    if (compress_file(src_path, dst_path, algo_name, strategy_name, level) != 0) {
        Py_DECREF(src_path_bytes);
        Py_DECREF(dst_path_bytes);
        return NULL; // Error already set
    }

    Py_DECREF(src_path_bytes);
    Py_DECREF(dst_path_bytes);
    Py_RETURN_NONE;
}

static PyObject *
py_decompress_file(PyObject *self, PyObject *args, PyObject *kwargs)
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

    if (decompress_file(src_path, dst_path, algo_name) != 0) {
        Py_DECREF(src_path_bytes);
        Py_DECREF(dst_path_bytes);
        return NULL; // Error already set
    }

    Py_DECREF(src_path_bytes);
    Py_DECREF(dst_path_bytes);
    Py_RETURN_NONE;
}

static PyObject *
py_get_capabilities(PyObject *self, PyObject *Py_UNUSED(ignored))
{
    return get_capabilities();
}


// ---- Module Definition ----

static PyMethodDef CoreMethods[] = {
    {"compress_file", (PyCFunction)py_compress_file, METH_VARARGS | METH_KEYWORDS,
     "Compress a file using the specified algorithm and strategy."},
    {"decompress_file", (PyCFunction)py_decompress_file, METH_VARARGS | METH_KEYWORDS,
     "Decompress a file using the specified algorithm."},
    {"get_capabilities", (PyCFunction)py_get_capabilities, METH_NOARGS,
     "Get the capabilities of available compression backends."},
    {NULL, NULL, 0, NULL}  // Sentinel
};

static struct PyModuleDef coremodule = {
    PyModuleDef_HEAD_INIT,
    "_core",
    "Compresso core extension",
    -1,
    CoreMethods
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