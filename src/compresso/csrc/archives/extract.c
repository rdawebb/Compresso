#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "../common.h"
#include "../fsutil.h"
#include "../standalone.h"
#include "archive_ops.h"
#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// ---- Default Extraction Policies ----

static const ExtractionPolicy EXTRACTION_POLICY_DEFAULT = {
    .allow_symlinks = -1,
    .overwrite_existing = 0,
    .preserve_permissions = 1,
    .exact_permissions = 0,
    .preserve_timestamps = 1,
    .overwrite_dir_metadata = 0,
    .max_depth = 32,
    .max_total_size = 0,
};

ExtractionPolicy extraction_policy_default(void) {
  return EXTRACTION_POLICY_DEFAULT;
}

// ---- Path Helpers ----

// Number of real components in `path`, ignoring empty and "." segments
static uint32_t path_depth(const char *path) {
  uint32_t depth = 0;
  const char *p = path;

  while (*p) {
    while (*p && FS_IS_SEP(*p))
      p++;

    const char *start = p;
    while (*p && !FS_IS_SEP(*p))
      p++;

    size_t len = (size_t)(p - start);
    if (len > 0 && !(len == 1 && start[0] == '.'))
      depth++;
  }

  return depth;
}

typedef struct {
  char *path;
  char *backup; // What OVERWRITE moved aside; NULL if `path` was created
  int is_dir;
} Change;

typedef struct {
  Change *items;
  size_t count, cap;
} Changes;

// Takes `backup` only on success
static int record_change(Changes *c, const char *path, char *backup,
                         int is_dir) {
  if (c->count == c->cap) {
    size_t cap = c->cap ? c->cap * 2 : 16;
    Change *items = realloc(c->items, cap * sizeof(*items));
    if (!items) {
      PyErr_NoMemory();
      return -1;
    }
    c->items = items;
    c->cap = cap;
  }

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

// Returns 1 if contained, 0 if it escapes, -1 if `dir` could not be resolved
// `resolved_root` must already have been through fs_realpath
static int dir_is_contained(const char *resolved_root, const char *dir) {
  char resolved_dir[FS_PATH_MAX];
  if (fs_realpath(dir, resolved_dir) != 0)
    return -1;

  size_t root_len = strlen(resolved_root);
  if (strncmp(resolved_dir, resolved_root, root_len) != 0)
    return 0;

  // A filesystem root ("/", "C:\") ends in a separator, so has no boundary byte
  if (root_len > 0 && FS_IS_SEP(resolved_root[root_len - 1]))
    return 1;

  // Otherwise require a component boundary, so "/out-evil" fails against "/out"
  return FS_IS_SEP(resolved_dir[root_len]) || resolved_dir[root_len] == '\0';
}

// Whether the deepest already-existing ancestor of `dir` is inside the root;
// its length goes in `existing_len`; only an existing component can redirect
// the path; anything still missing is about to be created rather than followed
static int existing_ancestor_is_contained(const char *resolved_root,
                                          const char *dir,
                                          size_t *existing_len) {
  char probe[FS_PATH_MAX];
  size_t len = strlen(dir);
  if (len >= sizeof(probe))
    return 0;
  memcpy(probe, dir, len + 1);

  for (;;) {
    int contained = dir_is_contained(resolved_root, probe);
    if (contained >= 0) {
      *existing_len = strlen(probe);
      return contained;
    }

    char *sep = fs_last_sep(probe);
    if (!sep || sep == probe) {
      *existing_len = 0;
      return 1; // Nothing left to trim; mkdir will report its own failure
    }
    *sep = '\0';
  }
}

// Ensure `dir` exists and is inside the extraction root, creating it if needed
static int prepare_output_dir(const CompErrors *errors,
                              const char *resolved_root, const char *dir,
                              uint32_t mode, const char *entry_path,
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

static int validate_entry_path(const CompErrors *errors, const char *output_dir,
                               const char *resolved_root,
                               const char *entry_path, uint32_t depth,
                               const ExtractionPolicy *policy) {
  if (fs_is_absolute(entry_path)) {
    PyErr_Format(errors->extraction_policy,
                 "Archive entry has an absolute path: %s", entry_path);
    return -1;
  }

  if (fs_is_stream_path(entry_path)) {
    PyErr_Format(errors->extraction_policy,
                 "Archive entry names an alternate data stream: %s",
                 entry_path);
    return -1;
  }

  if (policy->max_depth > 0 && depth > policy->max_depth) {
    PyErr_Format(errors->extraction_policy,
                 "Archive entry exceeds max depth (%u): %s", policy->max_depth,
                 entry_path);
    return -1;
  }

  char candidate[FS_PATH_MAX];
  if (snprintf(candidate, sizeof(candidate), "%s/%s", output_dir, entry_path) >=
      (int)sizeof(candidate)) {
    PyErr_SetString(PyExc_ValueError, "Archive entry path too long");
    return -1;
  }

  char *last_sep = fs_last_sep(candidate);
  char saved_sep = '\0';
  if (last_sep) {
    saved_sep = *last_sep;
    *last_sep = '\0';
  }

  // The parent usually does not exist yet, leaving only the textual check;
  // prepare_output_dir repeats it against the filesystem after creating it
  //
  // The fallback tests `entry_path` rather than the joined candidate, so an
  // output_dir that itself contains a ".." component is not read as an escape
  int contained = dir_is_contained(resolved_root, candidate);
  int traversal = (contained == 0) ||
                  (contained < 0 && path_has_parent_segment(entry_path));

  if (last_sep)
    *last_sep = saved_sep;

  if (traversal) {
    PyErr_Format(errors->extraction_policy,
                 "Path traversal detected in entry: %s", entry_path);
    return -1;
  }

  return 0;
}

// `base_depth` is the link's directory depth below the root; a ".." after a
// name is refused, as that name may be a symlink the textual check can't follow
static int check_symlink_target(const CompErrors *errors,
                                const ArchiveEntry *entry,
                                uint32_t base_depth) {
  const char *target = entry->link_target;
  if (!target || !*target) {
    PyErr_Format(errors->extraction_policy, "Symlink has no target: %s",
                 entry->path);
    return -1;
  }

  int contained = !fs_is_absolute(target) && !fs_is_stream_path(target);
  uint32_t depth = base_depth;
  int seen_name = 0;
  for (const char *p = target; contained && *p;) {
    while (*p && FS_IS_SEP(*p))
      p++;

    const char *start = p;
    while (*p && !FS_IS_SEP(*p))
      p++;

    size_t len = (size_t)(p - start);
    if (len == 2 && start[0] == '.' && start[1] == '.') {
      contained = !seen_name && depth > 0;
      if (contained)
        depth--;
    } else if (len > 0 && !(len == 1 && start[0] == '.')) {
      seen_name = 1;
    }
  }

  if (!contained) {
    PyErr_Format(errors->extraction_policy,
                 "Path traversal detected in symlink target: %s -> %s",
                 entry->path, target);
    return -1;
  }
  return 0;
}

// A hardlink's target names another entry, so gets an entry path's checks; a
// symlink's is checked from its written path; nothing else has a target
static int validate_link_target(const CompErrors *errors,
                                const char *output_dir,
                                const char *resolved_root,
                                const ArchiveEntry *entry, const char *target,
                                const ExtractionPolicy *policy) {
  if (entry->type == ENTRY_SYMLINK && policy->allow_symlinks == 1) {
    uint32_t depth = path_depth(entry->path);
    return check_symlink_target(errors, entry, depth > 0 ? depth - 1 : 0);
  }

  if (entry->type != ENTRY_HARDLINK)
    return 0;

  if (!target || !*target) {
    PyErr_Format(errors->extraction_policy, "Hardlink has no target: %s",
                 entry->path);
    return -1;
  }

  return validate_entry_path(errors, output_dir, resolved_root, target,
                             path_depth(target), policy);
}

static int check_entry_policy(const CompErrors *errors,
                              const ArchiveEntry *entry,
                              const ExtractionPolicy *policy) {
  if (entry->type == ENTRY_SYMLINK && !policy->allow_symlinks) {
    PyErr_Format(errors->extraction_policy,
                 "Archive contains symlink, but policy denies it: %s",
                 entry->path);
    return -1;
  }

  if (entry->type == ENTRY_SPECIAL) {
    PyErr_Format(errors->extraction_policy,
                 "Archive contains special file, but policy denies it: %s",
                 entry->path);
    return -1;
  }

  return 0;
}

// Whether `entry` is one of the requested `files`; an empty request means all
static int entry_is_selected(const ArchiveEntry *entry, const char **files,
                             size_t num_files) {
  if (num_files == 0)
    return 1;

  for (size_t i = 0; i < num_files; i++) {
    if (strcmp(entry->path, files[i]) == 0)
      return 1;
  }

  return 0;
}

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

// Walk every entry without writing anything, so an archive holding a refused
// entry leaves nothing on disk
//
// The size total here is advisory: a header can understate an entry, so
// extract_entries counts the bytes it actually writes as well
static int prevalidate_entries(const CArchive *archive, void *reader,
                               const char *output_dir,
                               const char *resolved_root, const char **files,
                               size_t num_files, const ExtractionPolicy *policy,
                               CoreContext *ctx, uint64_t *out_declared_total) {
  ArchiveEntry entry = {0};
  uint64_t declared_total = 0;
  int ret;

  // NULL: extract_entries reads the same headers again, and warns once there
  while ((ret = archive->get_next_entry(reader, &entry, NULL)) == 1) {
    // Nothing is written in this pass, so this is a cancellation check with no
    // progress to report
    int cancelled = ctx_advance(ctx, 0);
    if (cancelled != 0) {
      entry_reset(&entry);
      return cancelled;
    }

    if (!entry.path) {
      entry_reset(&entry);
      archive->skip_entry_data(reader);
      continue;
    }

    if (validate_entry_path(ctx->errors, output_dir, resolved_root, entry.path,
                            path_depth(entry.path), policy) != 0 ||
        validate_link_target(ctx->errors, output_dir, resolved_root, &entry,
                             entry.link_target, policy) != 0 ||
        check_entry_policy(ctx->errors, &entry, policy) != 0) {
      entry_reset(&entry);
      return -1;
    }

    // A link declares no data, but claims its path like a file
    if (entry_is_selected(&entry, files, num_files) &&
        (entry.type == ENTRY_FILE || entry.type == ENTRY_HARDLINK ||
         entry.type == ENTRY_SYMLINK)) {
      declared_total += entry.type == ENTRY_FILE ? entry.size : 0;
      if (policy->max_total_size > 0 &&
          declared_total > policy->max_total_size) {
        PyErr_Format(ctx->errors->extraction_policy,
                     "Archive exceeds the maximum extracted size (%llu bytes)",
                     (unsigned long long)policy->max_total_size);
        entry_reset(&entry);
        return -1;
      }

      if (policy->overwrite_existing == 0) {
        char out_path[FS_PATH_MAX];
        if (fs_join(out_path, sizeof(out_path), output_dir, entry.path) != 0) {
          PyErr_SetFromErrnoWithFilename(PyExc_OSError, entry.path);
          entry_reset(&entry);
          return -1;
        }

        fs_stat st;
        if (fs_stat_path(out_path, &st) == 0) {
          errno = EEXIST;
          PyErr_SetFromErrnoWithFilename(PyExc_OSError, out_path);
          entry_reset(&entry);
          return -1;
        }
      }
    }

    entry_reset(&entry);
    archive->skip_entry_data(reader);
  }

  if (out_declared_total)
    *out_declared_total = declared_total;

  return ret < 0 ? -1 : 0;
}

// Redirects entries under a renamed directory, since output paths come from
// the archive's literal strings rather than a live directory handle;
// old_prefix is the raw archive path so later raw entries match directly;
// new_prefix is the resolved on-disk path
typedef struct {
  char old_prefix[FS_PATH_MAX];
  char new_prefix[FS_PATH_MAX];
  size_t old_len;
} PathRename;

typedef struct {
  PathRename *items;
  size_t count, cap;
} Renames;

static int push_rename(Renames *renames, const char *raw_relative,
                       const char *actual_relative) {
  if (renames->count == renames->cap) {
    size_t cap = renames->cap ? renames->cap * 2 : 4;
    PathRename *items = realloc(renames->items, cap * sizeof(*items));
    if (!items) {
      PyErr_NoMemory();
      return -1;
    }
    renames->items = items;
    renames->cap = cap;
  }

  PathRename *r = &renames->items[renames->count];

  // Trim any trailing separator so this prefix matches at a component
  // boundary
  size_t raw_len = strlen(raw_relative);
  while (raw_len > 0 && FS_IS_SEP(raw_relative[raw_len - 1]))
    raw_len--;

  int n = snprintf(r->old_prefix, sizeof(r->old_prefix), "%.*s/", (int)raw_len,
                   raw_relative);
  if (n < 0 || (size_t)n >= sizeof(r->old_prefix)) {
    PyErr_SetString(PyExc_ValueError, "Archive entry path too long to rename");
    return -1;
  }
  r->old_len = (size_t)n;

  n = snprintf(r->new_prefix, sizeof(r->new_prefix), "%s/", actual_relative);
  if (n < 0 || (size_t)n >= sizeof(r->new_prefix)) {
    PyErr_SetString(PyExc_ValueError, "Archive entry path too long to rename");
    return -1;
  }

  renames->count++;
  return 0;
}

// Rewrites `path` through the longest renamed directory prefix it falls under,
// so a doubly-renamed ancestor composes in one step; returns `path` itself,
// `buf` holding the rewrite, or NULL with an exception set
static const char *redirect_path(const char *path, const Renames *renames,
                                 char *buf, size_t size) {
  const char *effective = path;
  size_t best_len = 0;
  for (size_t i = 0; i < renames->count; i++) {
    const PathRename *r = &renames->items[i];
    if (r->old_len > best_len &&
        strncmp(path, r->old_prefix, r->old_len) == 0) {
      if (fs_join(buf, size, r->new_prefix, path + r->old_len) != 0) {
        PyErr_SetFromErrnoWithFilename(PyExc_OSError, path);
        return NULL;
      }
      effective = buf;
      best_len = r->old_len;
    }
  }
  return effective;
}

// Directories are created writable, so their own entries can be written into
// them; each one's archived mode and mtime are applied once extraction ends
#define DIR_CREATE_MODE 0755

typedef struct {
  char *path;
  uint32_t mode;
  int64_t mtime;
} DeferredDir;

typedef struct {
  DeferredDir *items;
  size_t count, cap;
} DeferredDirs;

static int defer_dir(DeferredDirs *d, const char *path, uint32_t mode,
                     int64_t mtime) {
  if (d->count == d->cap) {
    size_t cap = d->cap ? d->cap * 2 : 16;
    DeferredDir *items = realloc(d->items, cap * sizeof(*items));
    if (!items) {
      PyErr_NoMemory();
      return -1;
    }
    d->items = items;
    d->cap = cap;
  }

  char *copy = strdup(path);
  if (!copy) {
    PyErr_NoMemory();
    return -1;
  }
  d->items[d->count++] = (DeferredDir){copy, mode, mtime};
  return 0;
}

static int deepest_first(const void *a, const void *b) {
  uint32_t da = path_depth(((const DeferredDir *)a)->path);
  uint32_t db = path_depth(((const DeferredDir *)b)->path);
  return (da < db) - (da > db);
}

// Deepest first, so a parent made read-only or unsearchable can't block its
// children; best-effort, like the metadata of files; a NULL `policy` only
// frees them
static void apply_deferred_dirs(DeferredDirs *d,
                                const ExtractionPolicy *policy) {
  qsort(d->items, d->count, sizeof(*d->items), deepest_first);

  for (size_t i = 0; i < d->count; i++) {
    if (policy && policy->preserve_permissions)
      fs_chmod(d->items[i].path, d->items[i].mode);
    if (policy && policy->preserve_timestamps && d->items[i].mtime > 0)
      fs_set_mtime(d->items[i].path, d->items[i].mtime);
    free(d->items[i].path);
  }

  free(d->items);
  *d = (DeferredDirs){0};
}

// Moves `path` to a temp beside it, so an undo can put it back; a missing
// `path` is no failure
static int move_aside(Changes *c, const char *path) {
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

// Undoes every change after the first `keep`, newest first; best-effort
static void undo_changes(Changes *c, size_t keep) {
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

// Keeps the changes, dropping the originals they replaced
static void keep_changes(Changes *c) {
  for (size_t i = 0; i < c->count; i++) {
    if (c->items[i].backup)
      remove_file(c->items[i].backup);
    free(c->items[i].path);
    free(c->items[i].backup);
  }
  free(c->items);
  *c = (Changes){0};
}

// Probes names until an exclusive mkdir succeeds, then records the rename so
// nested entries redirect to the directory actually created; `created` (at
// least FS_PATH_MAX bytes) receives the directory's real path
static int rename_conflicting_dir(const char *output_dir, const char *out_path,
                                  const char *entry_path, Renames *renames,
                                  char *created, Changes *changes) {
  char candidate[FS_PATH_MAX];

  for (int n = 2; n <= FS_MAX_CONFLICT_ATTEMPTS; n++) {
    if (fs_conflict_path(out_path, n, candidate, sizeof(candidate)) != 0) {
      PyErr_Format(PyExc_ValueError,
                   "Archive entry path too long to rename: %s", entry_path);
      return -1;
    }

    if (fs_mkdir_exclusive(candidate, DIR_CREATE_MODE) == 0) {
      memcpy(created, candidate, strlen(candidate) + 1);
      if (record_change(changes, candidate, NULL, 1) != 0)
        return -1;
      return push_rename(renames, entry_path,
                         candidate + strlen(output_dir) + 1);
    }
    if (errno != EEXIST) {
      PyErr_SetFromErrnoWithFilename(PyExc_OSError, candidate);
      return -1;
    }
  }

  PyErr_Format(PyExc_OSError, "Too many conflicting names for entry: %s",
               entry_path);
  return -1;
}

// Runs `make(source, out_path)` under the overwrite mode, as for a file;
// returns 0, or -1 with an exception set
static int place_link(int (*make)(const char *, const char *),
                      const char *source, const char *out_path,
                      const char *entry_path, int overwrite_existing,
                      Changes *changes) {
  if (make(source, out_path) == 0)
    return record_change(changes, out_path, NULL, 0);
  if (errno != EEXIST) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, out_path);
    return -1;
  }

  switch (overwrite_existing) {
  case 1: // SKIP
    return 0;
  case 2: // OVERWRITE
    if (move_aside(changes, out_path) != 0)
      return -1;
    if (make(source, out_path) != 0) {
      PyErr_SetFromErrnoWithFilename(PyExc_OSError, out_path);
      return -1;
    }
    return record_change(changes, out_path, NULL, 0);
  case 3: { // RENAME: each attempt is its own atomic link, as for a file
    char candidate[FS_PATH_MAX];
    for (int n = 2; n <= FS_MAX_CONFLICT_ATTEMPTS; n++) {
      if (fs_conflict_path(out_path, n, candidate, sizeof(candidate)) != 0) {
        PyErr_Format(PyExc_ValueError,
                     "Archive entry path too long to rename: %s", entry_path);
        return -1;
      }
      if (make(source, candidate) == 0)
        return record_change(changes, candidate, NULL, 0);
      if (errno != EEXIST) {
        PyErr_SetFromErrnoWithFilename(PyExc_OSError, candidate);
        return -1;
      }
    }
    PyErr_Format(PyExc_OSError, "Too many conflicting names for entry: %s",
                 entry_path);
    return -1;
  }
  default: // ERROR
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, out_path);
    return -1;
  }
}

// Links `out_path` to the already-extracted `target`; returns 0, or -1 with an
// exception set
static int extract_hardlink(const CompErrors *errors, const char *resolved_root,
                            const char *output_dir, const char *out_path,
                            const char *target, const char *entry_path,
                            int overwrite_existing, Changes *changes) {
  char target_path[FS_PATH_MAX];
  if (fs_join(target_path, sizeof(target_path), output_dir, target) != 0) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, target);
    return -1;
  }

  // lstat, so something planted in the target's place is refused, not followed
  fs_stat st;
  if (fs_stat_path(target_path, &st) != 0) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, target_path);
    return -1;
  }
  if (st.type != FS_TYPE_FILE) {
    PyErr_Format(errors->extraction_policy,
                 "Hardlink %s names %s, which is not a regular file",
                 entry_path, target);
    return -1;
  }

  // Catches a symlinked directory
  char *target_sep = fs_last_sep(target_path);
  if (target_sep) {
    char saved = *target_sep;
    *target_sep = '\0';
    int contained = dir_is_contained(resolved_root, target_path);
    *target_sep = saved;
    if (contained != 1) {
      PyErr_Format(errors->extraction_policy,
                   "Path traversal detected in hardlink target: %s", target);
      return -1;
    }
  }

  char link_parent[FS_PATH_MAX];
  memcpy(link_parent, out_path, strlen(out_path) + 1);
  char *link_sep = fs_last_sep(link_parent);
  if (link_sep) {
    *link_sep = '\0';
    if (prepare_output_dir(errors, resolved_root, link_parent, DIR_CREATE_MODE,
                           entry_path, changes) != 0)
      return -1;
  }

  return place_link(fs_link, target_path, out_path, entry_path,
                    overwrite_existing, changes);
}

// Checks the target from the link's resolved directory, which a symlinked
// ancestor can make shallower than its path suggests
static int extract_symlink(const CompErrors *errors, const char *resolved_root,
                           const char *out_path, const ArchiveEntry *entry,
                           int overwrite_existing, Changes *changes) {
  char parent[FS_PATH_MAX], resolved_parent[FS_PATH_MAX];
  memcpy(parent, out_path, strlen(out_path) + 1);
  char *sep = fs_last_sep(parent);
  if (sep) {
    *sep = '\0';
    if (prepare_output_dir(errors, resolved_root, parent, DIR_CREATE_MODE,
                           entry->path, changes) != 0)
      return -1;
  }

  if (fs_realpath(parent, resolved_parent) != 0) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, parent);
    return -1;
  }
  uint32_t parent_depth = path_depth(resolved_parent);
  uint32_t root_depth = path_depth(resolved_root);
  if (check_symlink_target(errors, entry,
                           parent_depth > root_depth
                               ? parent_depth - root_depth
                               : 0) != 0)
    return -1;

  return place_link(fs_symlink, entry->link_target, out_path, entry->path,
                    overwrite_existing, changes);
}

// Returns 0, 1 if the entry's data should be skipped, or -1 with an exception
// set; strips any trailing separator from `out_path`
static int extract_dir(const CompErrors *errors, const char *output_dir,
                       const char *resolved_root, char *out_path,
                       const ArchiveEntry *entry,
                       const ExtractionPolicy *policy, uint32_t mode_mask,
                       Renames *renames, Changes *changes,
                       DeferredDirs *deferred) {
  // Strip the entry's trailing separator (e.g. "d/") so a stat of a
  // same-named file below resolves the file
  size_t out_len = strlen(out_path);
  while (out_len > 0 && FS_IS_SEP(out_path[out_len - 1]))
    out_path[--out_len] = '\0';

  fs_stat existing;
  int found_existing = fs_stat_path(out_path, &existing) == 0;
  int existing_is_dir = found_existing && existing.type == FS_TYPE_DIR;

  if (found_existing && policy->overwrite_existing == 3) {
    // RENAME: create the directory under a free name rather than merge into, or
    // replace, what is there; no extra containment check needed; the winning
    // candidate is a sibling of out_path, and that parent's containment was
    // already established to reach this fs_stat_path call
    char created[FS_PATH_MAX];
    if (rename_conflicting_dir(output_dir, out_path, entry->path, renames,
                               created, changes) != 0)
      return -1;
    return defer_dir(deferred, created, entry->mode & mode_mask, entry->mtime);
  }

  if (found_existing && !existing_is_dir) {
    if (policy->overwrite_existing == 1) {
      // SKIP: nested entries under this dir fail on their own with a clear
      // NotADirectoryError when they try to write through it
      return 1;
    }
    if (policy->overwrite_existing != 2) {
      PyErr_Format(PyExc_OSError,
                   "Cannot create directory, a file already exists: %s",
                   out_path);
      return -1;
    }
    if (move_aside(changes, out_path) != 0)
      return -1;
  }

  if (prepare_output_dir(errors, resolved_root, out_path, DIR_CREATE_MODE,
                         entry->path, changes) != 0)
    return -1;

  // An existing directory keeps its own metadata unless asked otherwise
  int restore = !existing_is_dir || (policy->overwrite_dir_metadata &&
                                     policy->overwrite_existing != 1);
  return restore ? defer_dir(deferred, out_path, entry->mode & mode_mask,
                             entry->mtime)
                 : 0;
}

// Returns 0, 1 if the entry's data should be skipped, or a negative code with
// an exception set; adds the bytes it writes to `*written_total`
static int extract_file(const CArchive *archive, void *reader,
                        const CompErrors *errors, const char *resolved_root,
                        char *out_path, const ArchiveEntry *entry,
                        const ExtractionPolicy *policy, uint32_t mode_mask,
                        CoreContext *data_ctx, uint64_t *written_total,
                        Changes *changes) {
  size_t entry_start = changes->count;
  char *last_slash = fs_last_sep(out_path);
  if (last_slash) {
    char saved = *last_slash;
    *last_slash = '\0';
    int rc = prepare_output_dir(errors, resolved_root, out_path,
                                DIR_CREATE_MODE, entry->path, changes);
    *last_slash = saved;
    if (rc != 0)
      return -1;
  }

  // OVERWRITE moves what is there aside instead of writing through it;
  // every mode then opens exclusively
  if (policy->overwrite_existing == 2 && move_aside(changes, out_path) != 0)
    return -1;
  FILE *f = fs_fopen_exclusive(out_path);

  if (!f && errno == EEXIST && policy->overwrite_existing == 3) {
    // RENAME: try names until one is free; each attempt is its own
    // exclusive open, so this stays race-free
    char candidate[FS_PATH_MAX];
    for (int n = 2; n <= FS_MAX_CONFLICT_ATTEMPTS; n++) {
      if (fs_conflict_path(out_path, n, candidate, sizeof(candidate)) != 0) {
        PyErr_Format(PyExc_ValueError,
                     "Archive entry path too long to rename: %s", entry->path);
        return -1;
      }

      f = fs_fopen_exclusive(candidate);
      if (f || errno != EEXIST)
        break;
    }

    if (f) {
      size_t len = strlen(candidate);
      if (len >= FS_PATH_MAX)
        len = FS_PATH_MAX - 1;
      memcpy(out_path, candidate, len);
      out_path[len] = '\0';
    } else if (errno != EEXIST) {
      PyErr_SetFromErrnoWithFilename(PyExc_OSError, candidate);
      return -1;
    } else {
      PyErr_Format(PyExc_OSError, "Too many conflicting names for entry: %s",
                   entry->path);
      return -1;
    }
  }

  if (!f) {
    if (errno == EEXIST && policy->overwrite_existing == 1)
      return 1;
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, out_path);
    return -1;
  }
  if (record_change(changes, out_path, NULL, 0) != 0) {
    fclose(f);
    fs_unlink(out_path);
    return -1;
  }

  uint64_t remaining = policy->max_total_size > 0
                           ? policy->max_total_size - *written_total
                           : UINT64_MAX;
  uint64_t written = 0;

  int data_ret = 0;
  if (entry->type == ENTRY_SYMLINK) {
    // Mode 2: the link's target text becomes the file's content
    size_t len = entry->link_target ? strlen(entry->link_target) : 0;
    if (len > remaining) {
      PyErr_SetString(errors->extraction_policy,
                      "Archive exceeds the maximum extracted size");
      data_ret = -1;
    } else if (len > 0 && fwrite(entry->link_target, 1, len, f) != len) {
      PyErr_SetFromErrnoWithFilename(PyExc_OSError, out_path);
      data_ret = -1;
    } else {
      written = len;
    }
  } else {
    data_ret =
        archive->extract_entry_data(reader, f, remaining, &written, data_ctx);
  }
  *written_total += written;

  if (data_ret != 0) {
    fclose(f);
    // Never left truncated; a cancel keeps the entries already completed
    undo_changes(changes, entry_start);
    return data_ret;
  }

  // Flushed first, as a later write would move the mtime again
  // Best-effort: a metadata failure must not fail extraction of
  // otherwise-valid data
  int close_errno = fflush(f) != 0 ? errno : 0;
  if (!close_errno) {
    if (policy->preserve_timestamps && entry->mtime > 0)
      fs_futimens(f, (int64_t)entry->mtime);
    // A link's own mode (usually 0777) would make its text executable, so it
    // keeps the default file mode
    if (policy->preserve_permissions && entry->type == ENTRY_FILE)
      fs_fchmod(f, entry->mode & mode_mask);
  }
  if (fclose(f) != 0 && !close_errno)
    close_errno = errno;

  if (close_errno) {
    errno = close_errno;
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, out_path);
    return -1;
  }
  return 0;
}

// Read and write each entry from an already-open reader into `changes` and
// `deferred`; `data_ctx` counts bytes written, NULL if the caller counts input
static int extract_entries(const CArchive *archive, void *reader,
                           const char *output_dir, const char *resolved_root,
                           const char **files, size_t num_files,
                           const ExtractionPolicy *policy, CoreContext *ctx,
                           CoreContext *data_ctx, Changes *changes,
                           DeferredDirs *deferred) {
  ArchiveEntry entry = {0};
  uint64_t written_total = 0;
  Renames renames = {0};
  int ret;
  int result = -1;

  // Read once, as fs_umask briefly changes it
  uint32_t mode_mask = policy->exact_permissions ? 07777 : 0777 & ~fs_umask();

  while ((ret = archive->get_next_entry(reader, &entry, ctx)) == 1) {
    // Catches a cancel between entries; extract_entry_data catches one during
    // a single large entry
    int cancelled = ctx_advance(ctx, 0);
    if (cancelled != 0) {
      result = cancelled;
      goto cleanup;
    }

    if (!entry.path) {
      entry_reset(&entry);
      archive->skip_entry_data(reader);
      continue;
    }

    // Redirect through any directory renamed earlier in this pass; a hardlink's
    // target names an entry, so it follows the same renames
    char rewritten[FS_PATH_MAX], target_rewritten[FS_PATH_MAX];
    const char *effective_path =
        redirect_path(entry.path, &renames, rewritten, sizeof(rewritten));
    const char *effective_target =
        entry.type == ENTRY_HARDLINK && entry.link_target
            ? redirect_path(entry.link_target, &renames, target_rewritten,
                            sizeof(target_rewritten))
            : entry.link_target;
    if (!effective_path || (entry.type == ENTRY_HARDLINK && entry.link_target &&
                            !effective_target))
      goto cleanup;

    // Re-checked rather than trusted from the first pass
    if (validate_entry_path(ctx->errors, output_dir, resolved_root,
                            effective_path, path_depth(effective_path),
                            policy) != 0 ||
        validate_link_target(ctx->errors, output_dir, resolved_root, &entry,
                             effective_target, policy) != 0 ||
        check_entry_policy(ctx->errors, &entry, policy) != 0)
      goto cleanup;

    if (!entry_is_selected(&entry, files, num_files)) {
      entry_reset(&entry);
      archive->skip_entry_data(reader);
      continue;
    }

    char out_path[FS_PATH_MAX];
    if (fs_join(out_path, sizeof(out_path), output_dir, effective_path) != 0) {
      PyErr_SetFromErrnoWithFilename(PyExc_OSError, entry.path);
      goto cleanup;
    }

    int rc = 0;
    if (entry.type == ENTRY_DIR)
      rc = extract_dir(ctx->errors, output_dir, resolved_root, out_path, &entry,
                       policy, mode_mask, &renames, changes, deferred);
    else if (entry.type == ENTRY_SYMLINK && policy->allow_symlinks == 1)
      // Link mtimes aren't restored, as lutimes isn't portable
      rc = extract_symlink(ctx->errors, resolved_root, out_path, &entry,
                           policy->overwrite_existing, changes) != 0
               ? -1
               : 1;
    else if (entry.type == ENTRY_FILE || entry.type == ENTRY_SYMLINK)
      rc = extract_file(archive, reader, ctx->errors, resolved_root, out_path,
                        &entry, policy, mode_mask, data_ctx, &written_total,
                        changes);
    else if (entry.type == ENTRY_HARDLINK)
      // Shares the target's inode, and so its metadata too
      rc = extract_hardlink(ctx->errors, resolved_root, output_dir, out_path,
                            effective_target, entry.path,
                            policy->overwrite_existing, changes) != 0
               ? -1
               : 1;
    if (rc < 0) {
      result = rc;
      goto cleanup;
    }

    entry_reset(&entry);
    if (rc == 1)
      archive->skip_entry_data(reader);
  }

  result = ret; // May be the stream's COMP_CANCELLED

cleanup:
  entry_reset(&entry);
  free(renames.items);
  return result;
}

int extract_archive(const char *archive_path, const char *output_dir,
                    const char **files, size_t num_files,
                    const ExtractionPolicy *policy, CoreContext *ctx) {
  if (!policy)
    policy = &EXTRACTION_POLICY_DEFAULT;

  const CArchive *archive;
  CompressionPipeline pipe;
  FILE *src = open_archive_source(archive_path, &archive, &pipe, ctx);
  if (!src)
    return -1;
  if (pipe.codec == FORMAT_UNKNOWN) {
    fclose(src);
    src = NULL;
  }

  ExtractionPolicy resolved_policy = *policy;
  if (resolved_policy.allow_symlinks < 0)
    resolved_policy.allow_symlinks = archive->default_symlinks;
  policy = &resolved_policy;

  // Canonicalised once rather than per entry, and after the directory exists;
  // fs_mkdir_p accepts an existing file, so the resolved root is checked too
  char resolved_root[FS_PATH_MAX];
  fs_stat root_st;
  int root_ok = fs_mkdir_p(output_dir, 0755) == 0 &&
                fs_realpath(output_dir, resolved_root) == 0 &&
                fs_stat_path(resolved_root, &root_st) == 0;
  if (root_ok && root_st.type != FS_TYPE_DIR) {
    errno = ENOTDIR;
    root_ok = 0;
  }
  if (!root_ok) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, output_dir);
    if (src)
      fclose(src);
    return -1;
  }

  // A plain file gets a validation pass first; a codec stream is decoded once,
  // counting its compressed bytes, and relies on the undo below
  uint64_t declared_total = 0;
  CodecStream *stream = NULL;
  Changes changes = {0};
  DeferredDirs deferred = {0};
  int ret = 0;
  for (int pass = src ? 1 : 0; pass < 2 && ret == 0; pass++) {
    void *reader;
    if (src) {
      const FormatDesc *fmt = find_standalone_format(pipe.codec);
      ctx_begin_stage_stream(ctx, src);
      stream = open_codec_source(fmt, src, ctx);
      reader = stream ? archive->create_codec_reader(stream, archive_path, ctx)
                      : NULL;
    } else {
      if (pass == 1)
        ctx_begin_stage(ctx, declared_total);
      reader = archive->create_reader(archive_path, ctx);
    }
    if (!reader) {
      ret = -1;
      break;
    }

    ret = pass == 0
              ? prevalidate_entries(archive, reader, output_dir, resolved_root,
                                    files, num_files, policy, ctx,
                                    &declared_total)
              : extract_entries(archive, reader, output_dir, resolved_root,
                                files, num_files, policy, ctx, src ? NULL : ctx,
                                &changes, &deferred);

    // A failed pass keeps its own error
    if (archive->close_reader(reader, ret != 0) != 0 && ret == 0)
      ret = -1;
  }

  if (stream) {
    int rc = close_codec_source(stream, src, ret == 0);
    if (ret == 0)
      ret = rc;
  } else if (src) {
    fclose(src);
  }

  // Before directories get archived modes, which may not be writable; after an
  // undo, existing directories keep their own
  int undo = ret != 0 && ret != COMP_CANCELLED;
  if (undo)
    undo_changes(&changes, 0);
  keep_changes(&changes);
  apply_deferred_dirs(&deferred, undo ? NULL : policy);
  return ret;
}
