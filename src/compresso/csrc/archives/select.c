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

// The pattern after its first token if that token matches `c`, else NULL
static const char *match_token(const char *pat, unsigned char c) {
  if (*pat == '?')
    return pat + 1;
  if (*pat == '\\' && pat[1])
    return (unsigned char)pat[1] == c ? pat + 2 : NULL;

  if (*pat == '[') {
    const char *p = pat + 1;
    int negate = *p == '!' || *p == '^';
    if (negate)
      p++;
    // A `]` straight after the opening is literal
    int hit = 0;
    do {
      if (*p == '\0')
        return c == '[' ? pat + 1 : NULL; // Unterminated, so `[` is literal
      unsigned char lo = (unsigned char)*p, hi = lo;
      if (p[1] == '-' && p[2] && p[2] != ']') {
        hi = (unsigned char)p[2];
        p += 2;
      }
      hit |= lo <= c && c <= hi;
      p++;
    } while (*p != ']');
    return hit != negate ? p + 1 : NULL;
  }

  return (unsigned char)*pat == c ? pat + 1 : NULL;
}

// Whether `pat` matches `s` up to its end or a '/'; backtracks only to the
// last `*`, so the time stays linear: O(len * strlen(pat))
static int glob_match(const char *pat, const char *s, size_t len) {
  const char *star = NULL;
  size_t retry = 0, i = 0;
  while (i < len) {
    if (*pat == '\0' && i > 0 && s[i] == '/')
      return 1;
    if (*pat == '*') {
      star = ++pat;
      retry = i;
      continue;
    }
    const char *next = match_token(pat, (unsigned char)s[i]);
    if (next) {
      pat = next;
      i++;
    } else if (star) {
      pat = star;
      i = ++retry;
    } else {
      return 0;
    }
  }
  while (*pat == '*')
    pat++;
  return *pat == '\0';
}

int path_matches(const char *path, const char *pat) {
  size_t len = strlen(path);
  while (len > 0 && path[len - 1] == '/')
    len--;

  for (size_t start = 0; start < len; start++)
    if ((start == 0 || path[start - 1] == '/') &&
        glob_match(pat, path + start, len - start))
      return 1;
  return 0;
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
