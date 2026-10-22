#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "select.h"
#include "../common.h"
#include <stdlib.h>
#include <string.h>

static int selected_name_cmp(const void *a, const void *b) {
  const SelectedName *x = a, *y = b;
  int c = memcmp(x->name, y->name, x->len < y->len ? x->len : y->len);
  return c ? c : (x->len > y->len) - (x->len < y->len);
}

int selection_init(Selection *sel, const char **files, size_t num_files) {
  *sel = (Selection){0};
  if (num_files == 0)
    return 0;

  sel->items = safe_malloc(num_files * sizeof(*sel->items));
  if (!sel->items)
    return -1;

  for (size_t i = 0; i < num_files; i++) {
    size_t len = strlen(files[i]);
    while (len > 0 && files[i][len - 1] == '/')
      len--;
    sel->items[sel->count++] = (SelectedName){files[i], len, 0};
  }
  qsort(sel->items, sel->count, sizeof(*sel->items), selected_name_cmp);

  // A repeated name would never be marked as matched
  size_t kept = 0;
  for (size_t i = 0; i < sel->count; i++)
    if (kept == 0 ||
        selected_name_cmp(&sel->items[kept - 1], &sel->items[i]) != 0)
      sel->items[kept++] = sel->items[i];
  sel->count = kept;
  return 0;
}

int entry_is_selected(const ArchiveEntry *entry, Selection *sel) {
  if (sel->count == 0)
    return 1;

  int selected = 0;
  for (const char *p = entry->path;; p++) {
    if ((*p == '/' || *p == '\0') && p > entry->path) {
      SelectedName key = {entry->path, (size_t)(p - entry->path), 0};
      SelectedName *hit = bsearch(&key, sel->items, sel->count,
                                  sizeof(*sel->items), selected_name_cmp);
      if (hit) {
        hit->matched = 1;
        selected = 1;
      }
    }
    if (*p == '\0')
      return selected;
  }
}

static PyObject *join_str(const char *sep, PyObject *items) {
  PyObject *s = PyUnicode_FromString(sep);
  PyObject *joined = s ? PyUnicode_Join(s, items) : NULL;
  Py_XDECREF(s);
  return joined;
}

// Appends "label: a, b" to `parts`, or nothing when `items` is empty
static int append_part(PyObject *parts, const char *label, PyObject *items) {
  if (PyList_GET_SIZE(items) == 0)
    return 0;
  PyObject *joined = join_str(", ", items);
  PyObject *part =
      joined ? PyUnicode_FromFormat("%s: %U", label, joined) : NULL;
  int rc = part ? PyList_Append(parts, part) : -1;
  Py_XDECREF(part);
  Py_XDECREF(joined);
  return rc;
}

int report_incomplete(const Selection *sel, PyObject *skipped_links,
                      CoreContext *ctx) {
  PyObject *names = PyList_New(0);
  PyObject *parts = PyList_New(0);
  int ret = -1;
  if (!names || !parts)
    goto done;
  for (size_t i = 0; i < sel->count; i++) {
    if (sel->items[i].matched)
      continue;
    PyObject *name = PyUnicode_FromStringAndSize(sel->items[i].name,
                                                 (Py_ssize_t)sel->items[i].len);
    int rc = name ? PyList_Append(names, name) : -1;
    Py_XDECREF(name);
    if (rc != 0)
      goto done;
  }
  if (append_part(parts, "Not found in archive", names) != 0 ||
      append_part(parts, "Hardlink target missing", skipped_links) != 0)
    goto done;

  if (PyList_GET_SIZE(parts) == 0) {
    ret = 0;
    goto done;
  }
  PyObject *message = join_str("; ", parts);
  if (message)
    PyErr_SetObject(ctx->errors->incomplete_extraction, message);
  Py_XDECREF(message);

done:
  Py_XDECREF(parts);
  Py_XDECREF(names);
  return ret;
}
