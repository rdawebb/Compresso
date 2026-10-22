#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "../common.h"
#include "../fsutil.h"
#include "../standalone.h"
#include "archive_ops.h"
#include "changes.h"
#include "paths.h"
#include "select.h"
#include <archive_entry.h>
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

// Walk every entry without writing anything, so an archive holding a refused
// entry leaves nothing on disk
//
// The size total here is advisory: a header can understate an entry, so
// extract_entries counts the bytes it actually writes as well
static int prevalidate_entries(const CArchive *archive, void *reader,
                               const char *output_dir,
                               const char *resolved_root, Selection *sel,
                               const ExtractionPolicy *policy, CoreContext *ctx,
                               uint64_t *out_declared_total) {
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

    // A link or node declares no data, but claims its path like a file
    if (entry_is_selected(&entry, sel) && entry.type != ENTRY_DIR) {
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
  }

  *out_declared_total = declared_total;

  return ret < 0 ? -1 : 0;
}

// Redirects entries under a directory created under a new name, since output
// paths are built from the archive's strings, not a live directory handle
typedef struct {
  char old_prefix[FS_PATH_MAX]; // Raw archive path, so raw entries match as-is
  char new_prefix[FS_PATH_MAX]; // Resolved on-disk path
  size_t old_len;
} PathRename;

typedef struct {
  PathRename *items;
  size_t count, cap;
} Renames;

static int push_rename(Renames *renames, const char *raw_relative,
                       const char *actual_relative) {
  PathRename *items =
      grow(renames->items, &renames->cap, renames->count, sizeof(*items));
  if (!items)
    return -1;
  renames->items = items;

  PathRename *r = &renames->items[renames->count];

  // Trim trailing separators so prefixes match at component boundary
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
  DeferredDir *items = grow(d->items, &d->cap, d->count, sizeof(*items));
  if (!items)
    return -1;
  d->items = items;

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
// children; best-effort, like file metadata; a NULL `policy` only frees them
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

// Ensures the directory `out_path` lands in exists inside the root, copying
// it into `parent` (at least FS_PATH_MAX bytes)
static int prepare_parent(const CompErrors *errors, const char *resolved_root,
                          const char *out_path, const char *entry_path,
                          Changes *changes, char *parent) {
  memcpy(parent, out_path, strlen(out_path) + 1);
  char *sep = fs_last_sep(parent);
  if (!sep)
    return 0;
  *sep = '\0';
  return prepare_output_dir(errors, resolved_root, parent, DIR_CREATE_MODE,
                            entry_path, changes);
}

// Tries "name (2)", "name (3)"... beside `out_path` until `make(arg, name)`
// succeeds, each attempt atomic so this stays race-free; the name made goes in
// `chosen` (at least FS_PATH_MAX bytes)
static int try_conflict_names(const char *out_path,
                              int (*make)(const void *, const char *),
                              const void *arg, char *chosen,
                              const char *entry_path) {
  for (int n = 2; n <= FS_MAX_CONFLICT_ATTEMPTS; n++) {
    if (fs_conflict_path(out_path, n, chosen, FS_PATH_MAX) != 0) {
      PyErr_Format(PyExc_ValueError,
                   "Archive entry path too long to rename: %s", entry_path);
      return -1;
    }
    if (make(arg, chosen) == 0)
      return 0;
    if (errno != EEXIST) {
      PyErr_SetFromErrnoWithFilename(PyExc_OSError, chosen);
      return -1;
    }
  }

  PyErr_Format(PyExc_OSError, "Too many conflicting names for entry: %s",
               entry_path);
  return -1;
}

// Sets a positive `mtime` on the link made; best-effort
static int link_made(Changes *changes, const char *path, int64_t mtime) {
  if (mtime > 0)
    fs_set_mtime(path, mtime);
  return record_change(changes, path, NULL, 0);
}

static int make_dir(const void *unused, const char *out_path) {
  (void)unused;
  return fs_mkdir_exclusive(out_path, DIR_CREATE_MODE);
}

// `file` is the FILE ** to open into
static int make_file(const void *file, const char *out_path) {
  return (*(FILE **)file = fs_fopen_exclusive(out_path)) ? 0 : -1;
}

static int make_hardlink(const void *source, const char *out_path) {
  return fs_link(source, out_path);
}

static int make_symlink(const void *target, const char *out_path) {
  return fs_symlink(target, out_path);
}

// Creates `entry` with its `mode` as given
static int make_node(const void *entry, const char *out_path) {
  const ArchiveEntry *e = entry;
  return fs_mknod(out_path, e->special, e->mode, e->rdev);
}

// Runs `make(source, out_path)` under the overwrite mode, as for a file;
// returns 0, or -1 with an exception set
static int place_link(int (*make)(const void *, const char *),
                      const void *source, const char *out_path,
                      const char *entry_path, int overwrite_existing,
                      int64_t mtime, Changes *changes) {
  if (make(source, out_path) == 0)
    return link_made(changes, out_path, mtime);
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
    return link_made(changes, out_path, mtime);
  case 3: { // RENAME
    char candidate[FS_PATH_MAX];
    if (try_conflict_names(out_path, make, source, candidate, entry_path) != 0)
      return -1;
    return link_made(changes, candidate, mtime);
  }
  default: // ERROR
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, out_path);
    return -1;
  }
}

// Checks a symlink's target from `parent`, the directory it lands in, once
// resolved, as a symlinked ancestor can make it shallower
static int check_target_from(const CompErrors *errors,
                             const char *resolved_root, const char *parent,
                             const ArchiveEntry *entry) {
  char resolved_parent[FS_PATH_MAX];
  if (fs_realpath(parent, resolved_parent) != 0) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, parent);
    return -1;
  }
  uint32_t parent_depth = path_depth(resolved_parent);
  uint32_t root_depth = path_depth(resolved_root);
  return check_symlink_target(
      errors, entry, parent_depth > root_depth ? parent_depth - root_depth : 0);
}

// Links `out_path` to the already-extracted `target`; returns 0, 1 if `target`
// is missing, or -1 with an exception set
static int extract_hardlink(const CompErrors *errors, const char *resolved_root,
                            const char *output_dir, const char *out_path,
                            const char *target, const char *entry_path,
                            const ExtractionPolicy *policy, Changes *changes) {
  char target_path[FS_PATH_MAX];
  if (fs_join(target_path, sizeof(target_path), output_dir, target) != 0) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, target);
    return -1;
  }

  // lstat, so something planted in the target's place is refused, not followed
  fs_stat st;
  if (fs_stat_path(target_path, &st) != 0) {
    if (errno == ENOENT)
      return 1;
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, target_path);
    return -1;
  }
  // A symlink is linked itself
  int is_symlink = st.type == FS_TYPE_SYMLINK && policy->allow_symlinks == 1;
  if (st.type != FS_TYPE_FILE && !is_symlink && !st.special) {
    PyErr_Format(errors->extraction_policy,
                 "Hardlink %s names %s, which is not a file, FIFO or device",
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
  if (prepare_parent(errors, resolved_root, out_path, entry_path, changes,
                     link_parent) != 0)
    return -1;

  // Its relative target now resolves from the new link's directory, and it
  // may have been planted rather than extracted
  if (is_symlink) {
    char link_target[FS_PATH_MAX];
    if (fs_readlink(target_path, link_target, sizeof(link_target)) != 0) {
      PyErr_SetFromErrnoWithFilename(PyExc_OSError, target_path);
      return -1;
    }
    ArchiveEntry as_symlink = {.path = (char *)entry_path,
                               .link_target = link_target};
    if (check_target_from(errors, resolved_root, link_parent, &as_symlink) != 0)
      return -1;
  }

  // Shares the target's inode, and so its metadata too
  return place_link(make_hardlink, target_path, out_path, entry_path,
                    policy->overwrite_existing, 0, changes);
}

static int extract_symlink(const CompErrors *errors, const char *resolved_root,
                           const char *out_path, const ArchiveEntry *entry,
                           const ExtractionPolicy *policy, Changes *changes) {
  char parent[FS_PATH_MAX];
  if (prepare_parent(errors, resolved_root, out_path, entry->path, changes,
                     parent) != 0)
    return -1;

  if (check_target_from(errors, resolved_root, parent, entry) != 0)
    return -1;

  return place_link(make_symlink, entry->link_target, out_path, entry->path,
                    policy->overwrite_existing,
                    policy->preserve_timestamps ? (int64_t)entry->mtime : 0,
                    changes);
}

// A node this platform or user can't create is skipped with a warning; returns
// 0, or -1 with an exception set
static int extract_special(const CompErrors *errors, const char *resolved_root,
                           const char *out_path, const ArchiveEntry *entry,
                           const ExtractionPolicy *policy, uint32_t mode_mask,
                           CoreContext *ctx, Changes *changes) {
  if (!fs_can_mknod(entry->special)) {
    ctx_log(ctx, CTX_LOG_WARNING, "Skipped %s: %s", entry->path,
            !entry->special ? "sockets are not extracted"
            : entry->special == AE_IFIFO
                ? "FIFOs can't be created on this platform"
                : "devices are only created by root, and not on Windows");
    return 0;
  }

  char parent[FS_PATH_MAX];
  if (prepare_parent(errors, resolved_root, out_path, entry->path, changes,
                     parent) != 0)
    return -1;

  ArchiveEntry node = *entry;
  node.mode = policy->preserve_permissions ? entry->mode & mode_mask : 0666;
  return place_link(
      make_node, &node, out_path, entry->path, policy->overwrite_existing,
      policy->preserve_timestamps ? (int64_t)entry->mtime : 0, changes);
}

// Returns 0, or -1 with an exception set; strips trailing separators from
// `out_path`
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
    // RENAME: create the directory under a free name and redirect nested
    // entries there; no containment check needed, as the name chosen is a
    // sibling of out_path, whose parent is already contained
    char created[FS_PATH_MAX];
    if (try_conflict_names(out_path, make_dir, NULL, created, entry->path) !=
            0 ||
        record_change(changes, created, NULL, 1) != 0 ||
        push_rename(renames, entry->path, created + strlen(output_dir) + 1) !=
            0)
      return -1;
    return defer_dir(deferred, created, entry->mode & mode_mask, entry->mtime);
  }

  if (found_existing && !existing_is_dir) {
    if (policy->overwrite_existing == 1) {
      // SKIP: nested entries under this dir fail on their own with a clear
      // NotADirectoryError when they try to write through it
      return 0;
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

// Returns 0, or a negative code with an exception set; adds the bytes it writes
// to `*written_total`
static int extract_file(const CArchive *archive, void *reader,
                        const CompErrors *errors, const char *resolved_root,
                        char *out_path, const ArchiveEntry *entry,
                        const ExtractionPolicy *policy, uint32_t mode_mask,
                        CoreContext *data_ctx, uint64_t *written_total,
                        Changes *changes) {
  size_t entry_start = changes->count;
  char parent[FS_PATH_MAX];
  if (prepare_parent(errors, resolved_root, out_path, entry->path, changes,
                     parent) != 0)
    return -1;

  // OVERWRITE moves what is there aside instead of writing through it;
  // every mode then opens exclusively
  if (policy->overwrite_existing == 2 && move_aside(changes, out_path) != 0)
    return -1;
  FILE *f = fs_fopen_exclusive(out_path);

  if (!f && errno == EEXIST && policy->overwrite_existing == 3) {
    char candidate[FS_PATH_MAX];
    if (try_conflict_names(out_path, make_file, &f, candidate, entry->path) !=
        0)
      return -1;
    memcpy(out_path, candidate, strlen(candidate) + 1);
  }

  if (!f) {
    if (errno == EEXIST && policy->overwrite_existing == 1)
      return 0;
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

  // Flushed first, as later write would re-move mtime; best-effort: a metadata
  // failure must not fail extraction of otherwise-valid data
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

// Extract each entry from an open reader into `changes` and `deferred`; each
// hardlink whose target is missing is appended to `skipped_links` as
// "link -> target"; `data_ctx` counts bytes written, NULL callers count input
static int extract_entries(const CArchive *archive, void *reader,
                           const char *output_dir, const char *resolved_root,
                           Selection *sel, const ExtractionPolicy *policy,
                           CoreContext *ctx, CoreContext *data_ctx,
                           Changes *changes, DeferredDirs *deferred,
                           PyObject *skipped_links) {
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

    if (!entry_is_selected(&entry, sel)) {
      entry_reset(&entry);
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
      rc = extract_symlink(ctx->errors, resolved_root, out_path, &entry, policy,
                           changes);
    else if (entry.type == ENTRY_FILE || entry.type == ENTRY_SYMLINK)
      rc = extract_file(archive, reader, ctx->errors, resolved_root, out_path,
                        &entry, policy, mode_mask, data_ctx, &written_total,
                        changes);
    else if (entry.type == ENTRY_HARDLINK) {
      // A missing target is skipped, and reported once the rest is extracted
      rc = extract_hardlink(ctx->errors, resolved_root, output_dir, out_path,
                            effective_target, entry.path, policy, changes);
      if (rc == 1) {
        PyObject *link =
            PyUnicode_FromFormat("%s -> %s", entry.path, entry.link_target);
        rc = link && PyList_Append(skipped_links, link) == 0 ? 0 : -1;
        Py_XDECREF(link);
      }
    } else if (entry.type == ENTRY_SPECIAL)
      rc = extract_special(ctx->errors, resolved_root, out_path, &entry, policy,
                           mode_mask, ctx, changes);
    if (rc < 0) {
      result = rc;
      goto cleanup;
    }

    entry_reset(&entry);
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

  Selection sel;
  PyObject *skipped_links = PyList_New(0);
  if (!skipped_links || selection_init(&sel, files, num_files) != 0) {
    Py_XDECREF(skipped_links);
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
                                    &sel, policy, ctx, &declared_total)
              : extract_entries(archive, reader, output_dir, resolved_root,
                                &sel, policy, ctx, src ? NULL : ctx, &changes,
                                &deferred, skipped_links);

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

  // After everything else has been extracted
  if (ret == 0)
    ret = report_incomplete(&sel, skipped_links, ctx);
  Py_DECREF(skipped_links);
  free(sel.items);
  return ret;
}
