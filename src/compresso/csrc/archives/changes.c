#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "changes.h"
#include "../common.h"
#include "../fsutil.h"
#include "../standalone.h"
#include "paths.h"
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// Create a temp file in final_path's directory, which is known writable
static char *make_temp_path(const char *final_path) {
  char *tmpl = safe_malloc(FS_PATH_MAX);
  if (!tmpl)
    return NULL;

  OutputTarget out = {.path = final_path, .owner_only = 1};
  FILE *f = output_open(&out, tmpl);
  if (!f) {
    free(tmpl);
    return NULL;
  }
  fclose(f);
  return tmpl;
}

int record_change(Changes *c, const char *path, char *backup, int is_dir) {
  Change *items = grow(c->items, &c->cap, c->count, sizeof(*items));
  if (!items)
    return -1;
  c->items = items;

  char *copy = strdup(path);
  if (!copy) {
    PyErr_NoMemory();
    return -1;
  }
  c->items[c->count++] = (Change){copy, backup, is_dir};
  return 0;
}

// Records each component of `dir` past first `existing` bytes, shallowest first
static int record_new_dirs(Changes *c, const char *dir, size_t existing) {
  char prefix[FS_PATH_MAX];
  size_t len = strlen(dir);
  if (len >= sizeof(prefix)) {
    PyErr_SetString(PyExc_ValueError, "Archive entry path too long");
    return -1;
  }
  memcpy(prefix, dir, len + 1);

  for (size_t i = existing + 1; i <= len; i++) {
    if ((i == len || FS_IS_SEP(dir[i])) && !FS_IS_SEP(dir[i - 1])) {
      prefix[i] = '\0';
      if (record_change(c, prefix, NULL, 1) != 0)
        return -1;
      prefix[i] = dir[i];
    }
  }
  return 0;
}

int prepare_output_dir(const CompErrors *errors, const char *resolved_root,
                       const char *dir, uint32_t mode, const char *entry_path,
                       Changes *changes) {
  int contained = dir_is_contained(resolved_root, dir);

  if (contained < 0) {
    // Check before creating: mkdir first would already have made directories
    // through any symlink
    size_t existing_len;
    if (existing_ancestor_is_contained(resolved_root, dir, &existing_len) !=
        1) {
      PyErr_Format(errors->extraction_policy,
                   "Path traversal detected in entry: %s", entry_path);
      return -1;
    }

    // Before mkdir, so a partial failure is undone too
    if (record_new_dirs(changes, dir, existing_len) != 0)
      return -1;

    if (fs_mkdir_p(dir, mode) != 0) {
      PyErr_SetFromErrnoWithFilename(PyExc_OSError, dir);
      return -1;
    }

    // Re-check now that it exists, closing the window in which a component
    // could have been swapped for a symlink since the check above
    contained = dir_is_contained(resolved_root, dir);
  }

  if (contained != 1) {
    PyErr_Format(errors->extraction_policy,
                 "Path traversal detected in entry: %s", entry_path);
    return -1;
  }

  return 0;
}

int move_aside(Changes *c, const char *path) {
  fs_stat st;
  if (fs_stat_path(path, &st) != 0)
    return 0;
  if (st.type == FS_TYPE_DIR) {
    errno = EISDIR;
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, path);
    return -1;
  }

  char *backup = make_temp_path(path);
  if (!backup)
    return -1;
  if (fs_rename(path, backup, 1) != 0) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, path);
    fs_unlink(backup);
    free(backup);
    return -1;
  }
  if (record_change(c, path, backup, 0) != 0) {
    fs_rename(backup, path, 1);
    free(backup);
    return -1;
  }
  return 0;
}

// Windows won't remove a read-only file
static void remove_file(const char *path) {
  if (fs_unlink(path) != 0 && fs_chmod(path, 0600) == 0)
    fs_unlink(path);
}

void undo_changes(Changes *c, size_t keep) {
  while (c->count > keep) {
    Change *ch = &c->items[--c->count];
    if (ch->backup)
      fs_rename(ch->backup, ch->path, 1);
    else if (ch->is_dir)
      fs_rmdir(ch->path);
    else
      remove_file(ch->path);
    free(ch->path);
    free(ch->backup);
  }
}

void keep_changes(Changes *c) {
  for (size_t i = 0; i < c->count; i++) {
    if (c->items[i].backup)
      remove_file(c->items[i].backup);
    free(c->items[i].path);
    free(c->items[i].backup);
  }
  free(c->items);
  *c = (Changes){0};
}
