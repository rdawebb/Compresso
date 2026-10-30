#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "walk.h"
#include "../common.h"
#include "select.h"
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
  WalkItem *items = grow(list->items, &list->cap, list->count, sizeof(*items));
  if (!items) {
    free(path);
    return -1;
  }
  list->items = items;
  list->items[list->count++] =
      (WalkItem){.path = path, .prefix_len = prefix_len};
  return 0;
}

static int is_skipped(const WalkItem *item, const OwnFiles *own,
                      int keep_specials, CoreContext *ctx) {
  if (item->st.type == FS_TYPE_OTHER && !(keep_specials && item->st.special)) {
    ctx_log(ctx, CTX_LOG_WARNING, "Skipped %s: %s are not archived", item->path,
            keep_specials ? "sockets" : "FIFOs, sockets and devices");
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

// Groups names of the same file together, each group in walk order, so the
// first name walked keeps the data and the rest link to it
static int by_identity(const void *a, const void *b) {
  const WalkItem *x = *(const WalkItem *const *)a;
  const WalkItem *y = *(const WalkItem *const *)b;
  if (x->st.dev != y->st.dev)
    return x->st.dev < y->st.dev ? -1 : 1;
  if (x->st.ino != y->st.ino)
    return x->st.ino < y->st.ino ? -1 : 1;
  return (x > y) - (x < y);
}

static int find_hardlinks(WalkList *list) {
  size_t n = 0;
  for (size_t i = 0; i < list->count; i++)
    n += list->items[i].st.type != FS_TYPE_DIR && list->items[i].st.nlink > 1;
  if (n < 2)
    return 0;

  WalkItem **linked = malloc(n * sizeof(*linked));
  if (!linked) {
    PyErr_NoMemory();
    return -1;
  }
  n = 0;
  for (size_t i = 0; i < list->count; i++)
    if (list->items[i].st.type != FS_TYPE_DIR && list->items[i].st.nlink > 1)
      linked[n++] = &list->items[i];
  qsort(linked, n, sizeof(*linked), by_identity);

  for (size_t i = 1; i < n; i++) {
    if (!fs_same_file(&linked[i]->st, &linked[i - 1]->st))
      continue;
    linked[i]->link = linked[i - 1]->link ? linked[i - 1]->link : linked[i - 1];
    if (linked[i]->st.type == FS_TYPE_FILE)
      list->file_bytes -= linked[i]->st.size;
  }
  free(linked);
  return 0;
}

char *walk_entry_name(const char *path, size_t prefix_len) {
  const char *rel_path = path + prefix_len;
  while (FS_IS_SEP(*rel_path))
    rel_path++;

  char *name = strdup(rel_path);
  if (!name) {
    PyErr_NoMemory();
    return NULL;
  }

  // Normalise to '/', collapsing runs and dropping a trailing one; writers add
  // the trailing separator a directory needs
  char *w = name;
  for (const char *r = name; *r; r++) {
    if (FS_IS_SEP(*r)) {
      // Keep a leading separator so the absolute name is still rejected
      if (w > name && w[-1] == '/')
        continue;
      *w++ = '/';
    } else {
      *w++ = *r;
    }
  }
  while (w > name + 1 && w[-1] == '/')
    w--;
  *w = '\0';
  return name;
}

// Whether `path` is below the directory `dir`, as fs_join builds child paths
static int is_below(const char *path, const char *dir) {
  size_t n = strlen(dir);
  return strncmp(path, dir, n) == 0 &&
         (FS_IS_SEP(path[n]) || (n > 0 && FS_IS_SEP(dir[n - 1])));
}

// Drops each provisional directory with nothing kept below it; depth-first, so
// the nearest kept item after a directory is below it if anything is
static void prune_provisional(WalkList *list) {
  const char *next_kept = NULL;
  for (size_t i = list->count; i-- > 0;) {
    WalkItem *item = &list->items[i];
    if (item->provisional && !(next_kept && is_below(next_kept, item->path))) {
      free(item->path);
      item->path = NULL;
    } else {
      next_kept = item->path;
    }
  }

  size_t kept = 0;
  for (size_t i = 0; i < list->count; i++)
    if (list->items[i].path)
      list->items[kept++] = list->items[i];
  list->count = kept;
}

int walk_sources(const char **input_paths, size_t num_paths,
                 const PathFilter *filter, const OwnFiles *own,
                 int keep_specials, int link_hardlinks, CoreContext *ctx,
                 WalkList *out) {
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

    // Matched before the stat, so an excluded tree costs nothing to skip
    int included = 1;
    if (filter->num_include > 0 || filter->num_exclude > 0) {
      char *name = walk_entry_name(item.path, item.prefix_len);
      if (!name) {
        free(item.path);
        ret = -1;
        break;
      }
      int excluded =
          path_matches_any(name, filter->exclude, filter->num_exclude);
      included = filter->num_include == 0 ||
                 path_matches_any(name, filter->include, filter->num_include);
      free(name);
      if (excluded) {
        free(item.path);
        continue;
      }
    }

    if (fs_stat_path(item.path, &item.st) != 0) {
      PyErr_SetFromErrnoWithFilename(PyExc_OSError, item.path);
      free(item.path);
      ret = -1;
      break;
    }
    if ((!included && item.st.type != FS_TYPE_DIR) ||
        is_skipped(&item, own, keep_specials, ctx)) {
      free(item.path);
      continue;
    }

    ret = push(out, item.path, item.prefix_len);
    if (ret != 0)
      break;
    out->items[out->count - 1].st = item.st;
    out->items[out->count - 1].provisional = !included;

    if (item.st.type == FS_TYPE_DIR)
      ret = push_children(&pending, &item);
    else if (item.st.type == FS_TYPE_FILE)
      out->file_bytes += item.st.size;
  }

  walk_list_free(&pending);
  if (ret == 0)
    prune_provisional(out);
  if (ret == 0 && link_hardlinks)
    ret = find_hardlinks(out);
  return ret;
}

void walk_list_free(WalkList *list) {
  for (size_t i = 0; i < list->count; i++)
    free(list->items[i].path);
  free(list->items);
  *list = (WalkList){0};
}
