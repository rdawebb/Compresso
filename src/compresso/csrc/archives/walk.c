#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "walk.h"
#include <stdlib.h>
#include <string.h>

void own_files_add(OwnFiles *own, const char *path) {
  fs_stat st;
  if (own->count < 2 && fs_stat_path(path, &st) == 0 && st.type == FS_TYPE_FILE)
    own->files[own->count++] = st;
}

// Length of the parent prefix of a source path; trailing separators are
// ignored, so "dir/" names "dir" rather than ""
static size_t source_prefix_len(const char *path) {
  size_t end = strlen(path);
  while (end > 1 && FS_IS_SEP(path[end - 1]))
    end--;

  size_t start = end;
  while (start > 0 && !FS_IS_SEP(path[start - 1]))
    start--;

#if defined(_WIN32) || defined(_WIN64)
  // "C:file" is drive-relative: the qualifier is not part of the name
  if (start == 0 && path[0] != '\0' && path[1] == ':')
    start = 2;
#endif

  return start;
}

// Takes `path`, freeing it on failure
static int push(WalkList *list, char *path, size_t prefix_len) {
  if (!path) {
    PyErr_NoMemory();
    return -1;
  }
  if (list->count == list->cap) {
    size_t cap = list->cap ? list->cap * 2 : 64;
    WalkItem *items = realloc(list->items, cap * sizeof(*items));
    if (!items) {
      free(path);
      PyErr_NoMemory();
      return -1;
    }
    list->items = items;
    list->cap = cap;
  }
  list->items[list->count++] =
      (WalkItem){.path = path, .prefix_len = prefix_len};
  return 0;
}

static int is_skipped(const WalkItem *item, const OwnFiles *own,
                      CoreContext *ctx) {
  if (item->st.type == FS_TYPE_OTHER) {
    ctx_log(ctx, CTX_LOG_WARNING,
            "Skipped %s: FIFOs, sockets and devices are not archived",
            item->path);
    return 1;
  }

  for (size_t i = 0; i < own->count; i++) {
    if (fs_same_file(&item->st, &own->files[i])) {
      ctx_log(ctx, CTX_LOG_WARNING,
              "Skipped %s: it is the archive being created", item->path);
      return 1;
    }
  }
  return 0;
}

// Pushes the children of `dir` so they pop in the order readdir gave them
static int push_children(WalkList *pending, const WalkItem *dir) {
  fs_dir *d = fs_opendir(dir->path);
  if (!d) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, dir->path);
    return -1;
  }

  size_t first = pending->count;
  int ret = 0;
  const char *name;
  while ((name = fs_readdir(d)) != NULL) {
    char path[FS_PATH_MAX];
    if (fs_join(path, sizeof(path), dir->path, name) != 0) {
      PyErr_SetFromErrnoWithFilename(PyExc_OSError, dir->path);
      ret = -1;
      break;
    }
    if (push(pending, strdup(path), dir->prefix_len) != 0) {
      ret = -1;
      break;
    }
  }

  // A conversion failure also ends fs_readdir; unchecked, files would silently
  // go missing
  if (ret == 0 && fs_dir_error(d)) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, dir->path);
    ret = -1;
  }
  fs_closedir(d);

  for (size_t i = first, j = pending->count; i + 1 < j; i++, j--) {
    WalkItem t = pending->items[i];
    pending->items[i] = pending->items[j - 1];
    pending->items[j - 1] = t;
  }
  return ret;
}

int walk_sources(const char **input_paths, size_t num_paths,
                 const OwnFiles *own, CoreContext *ctx, WalkList *out) {
  // Depth-first like the archive's order, on a heap stack rather than the C one
  WalkList pending = {0};
  int ret = 0;
  for (size_t i = num_paths; i-- > 0 && ret == 0;)
    ret = push(&pending, strdup(input_paths[i]),
               source_prefix_len(input_paths[i]));

  while (ret == 0 && pending.count > 0) {
    // The walk reports no progress, so polls for itself; holds the GIL
    if (ctx_cancelled(ctx)) {
      ret = COMP_CANCELLED;
      break;
    }
    if (PyErr_CheckSignals() != 0) {
      ret = -1;
      break;
    }

    WalkItem item = pending.items[--pending.count];
    if (fs_stat_path(item.path, &item.st) != 0) {
      PyErr_SetFromErrnoWithFilename(PyExc_OSError, item.path);
      free(item.path);
      ret = -1;
      break;
    }
    if (is_skipped(&item, own, ctx)) {
      free(item.path);
      continue;
    }

    ret = push(out, item.path, item.prefix_len);
    if (ret != 0)
      break;
    out->items[out->count - 1].st = item.st;

    if (item.st.type == FS_TYPE_DIR)
      ret = push_children(&pending, &item);
    else if (item.st.type == FS_TYPE_FILE)
      out->file_bytes += item.st.size;
  }

  walk_list_free(&pending);
  return ret;
}

void walk_list_free(WalkList *list) {
  for (size_t i = 0; i < list->count; i++)
    free(list->items[i].path);
  free(list->items);
  *list = (WalkList){0};
}
