#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define PY_SSIZE_T_CLEAN
#include "archives.h"
#include "common.h"
#include "fsutil.h"
#include "standalone.h"
#include <Python.h>

// ---- Default Extraction Policies ----

static const ExtractionPolicy EXTRACTION_POLICY_DEFAULT = {
    .allow_symlinks = 0,
    .allow_absolute_paths = 0,
    .allow_special_files = 0,
    .overwrite_existing = 0,
    .preserve_permissions = 1,
    .preserve_timestamps = 1,
    .overwrite_dir_metadata = 0,
    .max_depth = 32,
    .max_total_size = 0,
};

ExtractionPolicy extraction_policy_default(void) {
  return EXTRACTION_POLICY_DEFAULT;
}

// ---- ArchiveEntry Helpers ----

static ArchiveEntry *entry_alloc(void) {
  // Zeroed, so unset fields are still defined; ENTRY_FILE is 0
  ArchiveEntry *e = calloc(1, sizeof(ArchiveEntry));
  if (!e)
    PyErr_NoMemory();
  return e;
}

static void entry_free(ArchiveEntry *e) {
  if (!e)
    return;
  free(e->path);
  free(e->link_target);
  free(e);
}

// Release an entry's owned strings and clear it for the next iteration
static void entry_reset(ArchiveEntry *e) {
  free(e->path);
  free(e->link_target);
  memset(e, 0, sizeof(*e));
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

// Whether any component of `path` is exactly ".."
static int path_has_parent_segment(const char *path) {
  const char *p = path;

  while (*p) {
    while (*p && FS_IS_SEP(*p))
      p++;

    const char *start = p;
    while (*p && !FS_IS_SEP(*p))
      p++;

    if ((size_t)(p - start) == 2 && start[0] == '.' && start[1] == '.')
      return 1;
  }

  return 0;
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

// Mirror extraction's checks, so an unsafe source fails at write time rather
// than producing an unextractable archive
static int check_storable_entry_name(const char *name) {
  if (name[0] == '\0' || fs_is_absolute(name) || fs_is_stream_path(name) ||
      path_has_parent_segment(name)) {
    PyErr_Format(PyExc_ValueError, "Refusing to store unsafe entry name: %s",
                 name);
    return -1;
  }

  return 0;
}

// ---- Helpers ----

// The archive being written and its temp file, so a walk over their own
// directory leaves them out: tar writes to one during the walk, and an output
// that is being overwritten still holds the previous archive
typedef struct {
  fs_stat files[2];
  size_t count;
} OwnFiles;

static void own_files_add(OwnFiles *own, const char *path) {
  fs_stat st;
  if (own->count < 2 && fs_stat_path(path, &st) == 0 && st.type == FS_TYPE_FILE)
    own->files[own->count++] = st;
}

// `st` receives the path's stat, for the caller's own checks
static ArchiveEntry *create_entry_from_path(const char *path, size_t prefix_len,
                                            fs_stat *out_st) {
  fs_stat st;
  if (fs_stat_path(path, &st) != 0) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, path);
    return NULL;
  }

  ArchiveEntry *entry = entry_alloc();
  if (!entry)
    return NULL;

  const char *rel_path = path + prefix_len;
  while (FS_IS_SEP(*rel_path))
    rel_path++;

  entry->path = strdup(rel_path);
  if (!entry->path) {
    entry_free(entry);
    PyErr_NoMemory();
    return NULL;
  }

  // Normalise to '/', collapsing runs and dropping a trailing one; writers add
  // the trailing separator a directory needs themselves
  char *w = entry->path;
  for (const char *r = entry->path; *r; r++) {
    if (FS_IS_SEP(*r)) {
      // Keep a leading separator so the absolute name is still rejected below
      if (w > entry->path && w[-1] == '/')
        continue;
      *w++ = '/';
    } else {
      *w++ = *r;
    }
  }
  while (w > entry->path + 1 && w[-1] == '/')
    w--;
  *w = '\0';

  if (check_storable_entry_name(entry->path) != 0) {
    entry_free(entry);
    return NULL;
  }

  entry->size = st.size;
  entry->mtime = st.mtime;
  entry->mode = st.mode;

  if (st.type == FS_TYPE_DIR) {
    entry->type = ENTRY_DIR;
  } else if (st.type == FS_TYPE_SYMLINK) {
    entry->type = ENTRY_SYMLINK;
    char target[FS_PATH_MAX];
    if (fs_readlink(path, target, sizeof(target)) != 0) {
      PyErr_SetFromErrnoWithFilename(PyExc_OSError, path);
      entry_free(entry);
      return NULL;
    }
    entry->link_target = strdup(target);
    if (!entry->link_target) {
      PyErr_NoMemory();
      entry_free(entry);
      return NULL;
    }
  } else if (st.type == FS_TYPE_FILE) {
    entry->type = ENTRY_FILE;
  } else {
    entry->type = ENTRY_SPECIAL;
  }

  *out_st = st;
  return entry;
}

// A FIFO or device is never opened, and the archive is never archived into
// itself; returns 1 if `entry` was skipped, and frees it
static int skip_entry(ArchiveEntry *entry, const fs_stat *st, const char *path,
                      const OwnFiles *own, CoreContext *ctx) {
  if (entry->type == ENTRY_SPECIAL) {
    ctx_log(ctx, CTX_LOG_WARNING,
            "Skipped %s: FIFOs, sockets and devices are not archived", path);
    entry_free(entry);
    return 1;
  }

  for (size_t i = 0; i < own->count; i++) {
    if (fs_same_file(st, &own->files[i])) {
      ctx_log(ctx, CTX_LOG_WARNING,
              "Skipped %s: it is the archive being created", path);
      entry_free(entry);
      return 1;
    }
  }

  return 0;
}

static int add_directory_recursive(void *writer, const CArchive *archive,
                                   const char *dir_path, size_t prefix_len,
                                   const OwnFiles *own, CoreContext *ctx) {
  fs_dir *dir = fs_opendir(dir_path);
  if (!dir) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, dir_path);
    return -1;
  }

  const char *name;
  while ((name = fs_readdir(dir)) != NULL) {
    char full_path[FS_PATH_MAX];
    if (fs_join(full_path, sizeof(full_path), dir_path, name) != 0) {
      fs_closedir(dir);
      PyErr_SetFromErrnoWithFilename(PyExc_OSError, dir_path);
      return -1;
    }

    fs_stat st;
    ArchiveEntry *ae = create_entry_from_path(full_path, prefix_len, &st);
    if (!ae) {
      fs_closedir(dir);
      return -1;
    }
    if (skip_entry(ae, &st, full_path, own, ctx))
      continue;

    if (ae->type == ENTRY_FILE) {
      FILE *f = fs_fopen(full_path, "rb");
      if (!f) {
        entry_free(ae);
        fs_closedir(dir);
        PyErr_SetFromErrnoWithFilename(PyExc_OSError, full_path);
        return -1;
      }
      int ret = archive->add_entry(writer, ae, f, full_path, ctx);
      fclose(f);
      entry_free(ae);
      if (ret != 0) {
        fs_closedir(dir);
        return ret;
      }
    } else if (ae->type == ENTRY_DIR) {
      int ret = archive->add_entry(writer, ae, NULL, full_path, ctx);
      entry_free(ae);
      if (ret == 0) {
        ret = add_directory_recursive(writer, archive, full_path, prefix_len,
                                      own, ctx);
      }
      if (ret != 0) {
        fs_closedir(dir);
        return ret;
      }
    } else {
      int ret = archive->add_entry(writer, ae, NULL, full_path, ctx);
      entry_free(ae);
      if (ret != 0) {
        fs_closedir(dir);
        return ret;
      }
    }
  }

  // A conversion failure ends iteration the same way exhaustion does, so
  // without this the archive would quietly be missing files
  if (fs_dir_error(dir)) {
    fs_closedir(dir);
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, dir_path);
    return -1;
  }

  fs_closedir(dir);
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

// Whether the deepest already-existing ancestor of `dir` is inside the root
// Only an existing component can redirect the path; anything still missing is
// about to be created rather than followed
static int existing_ancestor_is_contained(const char *resolved_root,
                                          const char *dir) {
  char probe[FS_PATH_MAX];
  size_t len = strlen(dir);
  if (len >= sizeof(probe))
    return 0;
  memcpy(probe, dir, len + 1);

  for (;;) {
    int contained = dir_is_contained(resolved_root, probe);
    if (contained >= 0)
      return contained;

    char *sep = fs_last_sep(probe);
    if (!sep || sep == probe)
      return 1; // Nothing left to trim; mkdir will report its own failure
    *sep = '\0';
  }
}

// Ensure `dir` exists and is inside the extraction root, creating it if needed
static int prepare_output_dir(const char *resolved_root, const char *dir,
                              uint32_t mode, const char *entry_path) {
  int contained = dir_is_contained(resolved_root, dir);

  if (contained < 0) {
    // Check before creating: mkdir first would already have made directories
    // through any symlink
    if (existing_ancestor_is_contained(resolved_root, dir) != 1) {
      PyErr_Format(comp_ExtractionPolicyError,
                   "Path traversal detected in entry: %s", entry_path);
      return -1;
    }

    if (fs_mkdir_p(dir, mode) != 0) {
      PyErr_SetFromErrnoWithFilename(PyExc_OSError, dir);
      return -1;
    }

    // Re-check now that it exists, closing the window in which a component
    // could have been swapped for a symlink since the check above
    contained = dir_is_contained(resolved_root, dir);
  }

  if (contained != 1) {
    PyErr_Format(comp_ExtractionPolicyError,
                 "Path traversal detected in entry: %s", entry_path);
    return -1;
  }

  return 0;
}

static int validate_entry_path(const char *output_dir,
                               const char *resolved_root,
                               const char *entry_path, uint32_t depth,
                               const ExtractionPolicy *policy) {
  if (fs_is_absolute(entry_path)) {
    PyErr_Format(comp_ExtractionPolicyError,
                 "Archive entry has an absolute path: %s", entry_path);
    return -1;
  }

  if (fs_is_stream_path(entry_path)) {
    PyErr_Format(comp_ExtractionPolicyError,
                 "Archive entry names an alternate data stream: %s",
                 entry_path);
    return -1;
  }

  if (policy->max_depth > 0 && depth > policy->max_depth) {
    PyErr_Format(comp_ExtractionPolicyError,
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
    PyErr_Format(comp_ExtractionPolicyError,
                 "Path traversal detected in entry: %s", entry_path);
    return -1;
  }

  return 0;
}

// A hardlink's target names another entry, so it gets the same checks as an
// entry's own path; anything else has no target to check
static int validate_link_target(const char *output_dir,
                                const char *resolved_root,
                                const ArchiveEntry *entry, const char *target,
                                const ExtractionPolicy *policy) {
  if (entry->type != ENTRY_HARDLINK)
    return 0;

  if (!target || !*target) {
    PyErr_Format(comp_ExtractionPolicyError, "Hardlink has no target: %s",
                 entry->path);
    return -1;
  }

  return validate_entry_path(output_dir, resolved_root, target,
                             path_depth(target), policy);
}

static int check_entry_policy(const ArchiveEntry *entry,
                              const ExtractionPolicy *policy) {
  if (entry->type == ENTRY_SYMLINK) {
    if (!policy->allow_symlinks) {
      PyErr_Format(comp_ExtractionPolicyError,
                   "Archive contains symlink, but policy denies it: %s",
                   entry->path);
      return -1;
    }

    // Refuse rather than drop the entry silently: neither allowed mode is
    // implemented yet (1 = create the link, 2 = rewrite to a regular file)
    PyErr_Format(PyExc_NotImplementedError,
                 "Symlink extraction is not implemented: %s", entry->path);
    return -1;
  }

  if (entry->type == ENTRY_SPECIAL && !policy->allow_special_files) {
    PyErr_Format(comp_ExtractionPolicyError,
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

// ---- Archive Registry ----

const CArchive *find_archive_by_id(uint8_t id) {
  switch (id) {
  case ARCHIVE_TAR:
    return get_tar_archive();
  case ARCHIVE_ZIP:
    return get_zip_archive();
  default:
    return NULL;
  }
}

// The backend for a pipeline's container, so a recognised format without a
// backend (e.g. 7z) is identified
static const CArchive *archive_for_pipeline(const CompressionPipeline *p) {
  const CArchive *archive = find_archive_by_id(p->archive);
  if (archive && archive->is_available())
    return archive;

  CompressionPipeline container = {.archive = p->archive,
                                   .codec = FORMAT_UNKNOWN};
  char name[32];
  pipeline_display_name(&container, name, sizeof(name));

  if (!archive)
    PyErr_Format(comp_BackendError,
                 "%s archives are recognised but not supported yet", name);
  else
    PyErr_Format(comp_BackendError, "%s archive backend not available", name);
  return NULL;
}

// ---- Pipeline Helpers ----

// Create a temp file in final_path's directory, which is known writable
static char *make_temp_path(const char *final_path) {
  static const char SUFFIX[] = ".compresso-XXXXXX";
  const char *slash = fs_last_sep(final_path);
  size_t dir_len = slash ? (size_t)(slash - final_path + 1) : 0;
  size_t len = dir_len + sizeof(SUFFIX); // sizeof includes the NUL

  char *tmpl = safe_malloc(len);
  if (!tmpl)
    return NULL;
  if (dir_len)
    memcpy(tmpl, final_path, dir_len);
  memcpy(tmpl + dir_len, SUFFIX, sizeof(SUFFIX));

  if (fs_mkstemp(tmpl) != 0) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, tmpl);
    free(tmpl);
    return NULL;
  }
  return tmpl;
}

// Total bytes of regular files under `dir_path`, for the progress denominator
static uint64_t sum_directory_size(const char *dir_path) {
  fs_dir *dir = fs_opendir(dir_path);
  if (!dir)
    return 0;

  uint64_t total = 0;
  const char *name;
  while ((name = fs_readdir(dir)) != NULL) {
    char full_path[FS_PATH_MAX];
    fs_stat st;
    if (fs_join(full_path, sizeof(full_path), dir_path, name) != 0 ||
        fs_stat_path(full_path, &st) != 0)
      continue;

    if (st.type == FS_TYPE_DIR)
      total += sum_directory_size(full_path);
    else if (st.type == FS_TYPE_FILE)
      total += st.size;
  }

  fs_closedir(dir);
  return total;
}

// Bytes the writer stage will read, so progress has a denominator up front
static uint64_t sum_input_size(const char **input_paths, size_t num_paths) {
  uint64_t total = 0;
  for (size_t i = 0; i < num_paths; i++) {
    fs_stat st;
    if (fs_stat_path(input_paths[i], &st) != 0)
      continue;

    if (st.type == FS_TYPE_DIR)
      total += sum_directory_size(input_paths[i]);
    else if (st.type == FS_TYPE_FILE)
      total += st.size;
  }
  return total;
}

// Write every input path into an already-open writer
static int add_paths_to_writer(const CArchive *archive, void *writer,
                               const char **input_paths, size_t num_paths,
                               const OwnFiles *own, CoreContext *ctx) {
  for (size_t i = 0; i < num_paths; i++) {
    fs_stat st;
    if (fs_stat_path(input_paths[i], &st) != 0) {
      PyErr_SetFromErrnoWithFilename(PyExc_OSError, input_paths[i]);
      return -1;
    }

    // Strip only the source's parent, so entries keep the source's own name
    size_t prefix_len = source_prefix_len(input_paths[i]);

    if (st.type == FS_TYPE_DIR) {
      // Record the directory itself so empty directories are preserved
      ArchiveEntry *dir_entry =
          create_entry_from_path(input_paths[i], prefix_len, &st);
      if (!dir_entry)
        return -1;
      int dir_ret =
          archive->add_entry(writer, dir_entry, NULL, input_paths[i], ctx);
      entry_free(dir_entry);
      if (dir_ret != 0)
        return dir_ret;

      int walk_ret = add_directory_recursive(writer, archive, input_paths[i],
                                             prefix_len, own, ctx);
      if (walk_ret != 0)
        return walk_ret;
    } else {
      ArchiveEntry *entry =
          create_entry_from_path(input_paths[i], prefix_len, &st);
      if (!entry)
        return -1;
      if (skip_entry(entry, &st, input_paths[i], own, ctx))
        continue;

      FILE *f = fs_fopen(input_paths[i], "rb");
      if (!f) {
        entry_free(entry);
        PyErr_SetFromErrnoWithFilename(PyExc_OSError, input_paths[i]);
        return -1;
      }

      int ret = archive->add_entry(writer, entry, f, input_paths[i], ctx);
      fclose(f);
      entry_free(entry);

      if (ret != 0)
        return ret;
    }
  }

  return 0;
}

// ---- Archive Operations ----

int create_archive(const char *output_path, const CompressionPipeline *pipeline,
                   const char **input_paths, size_t num_paths,
                   int overwrite_existing, char *out_actual_path,
                   size_t out_actual_path_size, CoreContext *ctx) {
  if (!pipeline_is_valid(pipeline) || pipeline->archive == ARCHIVE_NONE) {
    char name[32];
    pipeline_display_name(pipeline, name, sizeof(name));
    PyErr_Format(PyExc_ValueError, "Format does not support archives: %s",
                 name);
    return -1;
  }

  int level = pipeline->compression_level;

  const CArchive *archive = archive_for_pipeline(pipeline);
  if (!archive)
    return -1;

  char resolved_path[FS_PATH_MAX];
  int resolve_ret = fs_resolve_conflict(output_path, overwrite_existing,
                                        resolved_path, sizeof(resolved_path));
  if (resolve_ret != 0) {
    if (resolve_ret > 0 && out_actual_path) { // SKIP is a successful no-op
      snprintf(out_actual_path, out_actual_path_size, "%s", resolved_path);
      return 0;
    }
    if (errno == ENAMETOOLONG) {
      PyErr_Format(PyExc_ValueError, "Archive path too long: %s", output_path);
    } else {
      PyErr_SetFromErrnoWithFilename(PyExc_OSError, output_path);
    }
    return -1;
  }
  output_path = resolved_path;

  // Write archive to a temp file, then compress via the standalone codec
  char *tmp_path = NULL;
  const char *write_path = output_path;
  if (pipeline->codec != FORMAT_UNKNOWN) {
    tmp_path = make_temp_path(output_path);
    if (!tmp_path)
      return -1;
    write_path = tmp_path;
  }

  void *writer = archive->create_writer(write_path, level);
  if (!writer) {
    if (tmp_path) {
      fs_unlink(tmp_path);
      free(tmp_path);
    }
    return -1;
  }

  // With a codec, the temp archive is read a second time, so the two reads are
  // stages of one job
  uint64_t input_total = sum_input_size(input_paths, num_paths);
  if (pipeline->codec != FORMAT_UNKNOWN) {
    ctx_begin_job(ctx, input_total);
  }
  ctx_begin_stage(ctx, input_total);

  // Once the writer has opened its output, so a tar's identity is known
  OwnFiles own = {0};
  own_files_add(&own, write_path);
  if (write_path != output_path)
    own_files_add(&own, output_path);

  int ret =
      add_paths_to_writer(archive, writer, input_paths, num_paths, &own, ctx);

  // libzip does all its compression inside zip_close, so that is where its
  // progress comes from; a run that already failed is discarded instead
  int close_ret = archive->close_writer(writer, ctx, ret != 0);
  if (ret == 0 && close_ret != 0)
    ret = close_ret;

  if (ret == 0 && pipeline->codec != FORMAT_UNKNOWN) {
    const StandaloneFormat *codec = find_standalone_format(pipeline->codec);
    ret = codec->compress_file(tmp_path, output_path, level, ctx);
  }

  if (tmp_path) {
    fs_unlink(tmp_path);
    free(tmp_path);
  }

  // Never leave a half-written archive behind. The codec stage cleans up after
  // itself, so this covers the archive written straight to the destination.
  if (ret != 0) {
    fs_unlink(output_path);
  }

  if (ret == 0 && out_actual_path) {
    snprintf(out_actual_path, out_actual_path_size, "%s", output_path);
  }

  return ret;
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

    if (validate_entry_path(output_dir, resolved_root, entry.path,
                            path_depth(entry.path), policy) != 0 ||
        validate_link_target(output_dir, resolved_root, &entry,
                             entry.link_target, policy) != 0 ||
        check_entry_policy(&entry, policy) != 0) {
      entry_reset(&entry);
      return -1;
    }

    // A hardlink adds no data, but claims its path like a file
    if (entry_is_selected(&entry, files, num_files) &&
        (entry.type == ENTRY_FILE || entry.type == ENTRY_HARDLINK)) {
      declared_total += entry.type == ENTRY_FILE ? entry.size : 0;
      if (policy->max_total_size > 0 &&
          declared_total > policy->max_total_size) {
        PyErr_Format(comp_ExtractionPolicyError,
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

static int push_rename(PathRename **renames, size_t *num_renames,
                       size_t *cap_renames, const char *raw_relative,
                       const char *actual_relative) {
  if (*num_renames == *cap_renames) {
    size_t new_cap = *cap_renames ? *cap_renames * 2 : 4;
    PathRename *grown = realloc(*renames, new_cap * sizeof(**renames));
    if (!grown) {
      PyErr_NoMemory();
      return -1;
    }
    *renames = grown;
    *cap_renames = new_cap;
  }

  PathRename *r = &(*renames)[*num_renames];

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

  (*num_renames)++;
  return 0;
}

// Rewrites `path` through the longest renamed directory prefix it falls under,
// so a doubly-renamed ancestor composes in one step; returns `path` itself,
// `buf` holding the rewrite, or NULL with an exception set
static const char *redirect_path(const char *path, const PathRename *renames,
                                 size_t num_renames, char *buf, size_t size) {
  const char *effective = path;
  size_t best_len = 0;
  for (size_t i = 0; i < num_renames; i++) {
    if (renames[i].old_len > best_len &&
        strncmp(path, renames[i].old_prefix, renames[i].old_len) == 0) {
      if (fs_join(buf, size, renames[i].new_prefix,
                  path + renames[i].old_len) != 0) {
        PyErr_SetFromErrnoWithFilename(PyExc_OSError, path);
        return NULL;
      }
      effective = buf;
      best_len = renames[i].old_len;
    }
  }
  return effective;
}

// Probes names until an exclusive mkdir succeeds, then records the rename so
// nested entries redirect to the directory actually created
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
// children; best-effort, like the metadata of files
static void apply_deferred_dirs(DeferredDirs *d,
                                const ExtractionPolicy *policy) {
  qsort(d->items, d->count, sizeof(*d->items), deepest_first);

  for (size_t i = 0; i < d->count; i++) {
    if (policy->preserve_permissions)
      fs_chmod(d->items[i].path, d->items[i].mode);
    if (policy->preserve_timestamps && d->items[i].mtime > 0)
      fs_set_mtime(d->items[i].path, d->items[i].mtime);
    free(d->items[i].path);
  }

  free(d->items);
  *d = (DeferredDirs){0};
}

// `created` (at least FS_PATH_MAX bytes) receives the directory's real path
static int rename_conflicting_dir(const char *output_dir, const char *out_path,
                                  const char *entry_path, PathRename **renames,
                                  size_t *num_renames, size_t *cap_renames,
                                  char *created) {
  char candidate[FS_PATH_MAX];

  for (int n = 2; n <= FS_MAX_CONFLICT_ATTEMPTS; n++) {
    if (fs_conflict_path(out_path, n, candidate, sizeof(candidate)) != 0) {
      PyErr_Format(PyExc_ValueError,
                   "Archive entry path too long to rename: %s", entry_path);
      return -1;
    }

    if (fs_mkdir_exclusive(candidate, DIR_CREATE_MODE) == 0) {
      memcpy(created, candidate, strlen(candidate) + 1);
      return push_rename(renames, num_renames, cap_renames, entry_path,
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

// Links `out_path` to the already-extracted `target`, applying the overwrite
// mode as for a file; returns 0, or -1 with an exception set
static int extract_hardlink(const char *resolved_root, const char *output_dir,
                            const char *out_path, const char *target,
                            const char *entry_path, int overwrite_existing) {
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
    PyErr_Format(comp_ExtractionPolicyError,
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
      PyErr_Format(comp_ExtractionPolicyError,
                   "Path traversal detected in hardlink target: %s", target);
      return -1;
    }
  }

  char link_parent[FS_PATH_MAX];
  memcpy(link_parent, out_path, strlen(out_path) + 1);
  char *link_sep = fs_last_sep(link_parent);
  if (link_sep) {
    *link_sep = '\0';
    if (prepare_output_dir(resolved_root, link_parent, DIR_CREATE_MODE,
                           entry_path) != 0)
      return -1;
  }

  if (fs_link(target_path, out_path) == 0)
    return 0;
  if (errno != EEXIST) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, out_path);
    return -1;
  }

  switch (overwrite_existing) {
  case 1: // SKIP
    return 0;
  case 2: // OVERWRITE
    if (fs_unlink(out_path) != 0 || fs_link(target_path, out_path) != 0) {
      PyErr_SetFromErrnoWithFilename(PyExc_OSError, out_path);
      return -1;
    }
    return 0;
  case 3: { // RENAME: each attempt is its own atomic link, as for a file
    char candidate[FS_PATH_MAX];
    for (int n = 2; n <= FS_MAX_CONFLICT_ATTEMPTS; n++) {
      if (fs_conflict_path(out_path, n, candidate, sizeof(candidate)) != 0) {
        PyErr_Format(PyExc_ValueError,
                     "Archive entry path too long to rename: %s", entry_path);
        return -1;
      }
      if (fs_link(target_path, candidate) == 0)
        return 0;
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

// Read and write each entry from an already-open reader
static int extract_entries(const CArchive *archive, void *reader,
                           const char *output_dir, const char *resolved_root,
                           const char **files, size_t num_files,
                           const ExtractionPolicy *policy, CoreContext *ctx) {
  ArchiveEntry entry = {0};
  uint64_t written_total = 0;
  int ret;
  int result;

  PathRename *renames = NULL;
  size_t num_renames = 0, cap_renames = 0;
  DeferredDirs deferred = {0};

  while ((ret = archive->get_next_entry(reader, &entry, ctx)) == 1) {
    // Catches a cancel between entries; extract_entry_data catches one during
    // a single large entry
    int cancelled = ctx_advance(ctx, 0);
    if (cancelled != 0) {
      entry_reset(&entry);
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
    const char *effective_path = redirect_path(entry.path, renames, num_renames,
                                               rewritten, sizeof(rewritten));
    const char *effective_target =
        entry.type == ENTRY_HARDLINK && entry.link_target
            ? redirect_path(entry.link_target, renames, num_renames,
                            target_rewritten, sizeof(target_rewritten))
            : entry.link_target;
    if (!effective_path || (entry.type == ENTRY_HARDLINK && entry.link_target &&
                            !effective_target)) {
      entry_reset(&entry);
      result = -1;
      goto cleanup;
    }

    // Re-checked rather than trusted from the first pass
    if (validate_entry_path(output_dir, resolved_root, effective_path,
                            path_depth(effective_path), policy) != 0 ||
        validate_link_target(output_dir, resolved_root, &entry,
                             effective_target, policy) != 0 ||
        check_entry_policy(&entry, policy) != 0) {
      entry_reset(&entry);
      result = -1;
      goto cleanup;
    }

    if (!entry_is_selected(&entry, files, num_files)) {
      entry_reset(&entry);
      archive->skip_entry_data(reader);
      continue;
    }

    char out_path[FS_PATH_MAX];
    if (fs_join(out_path, sizeof(out_path), output_dir, effective_path) != 0) {
      PyErr_SetFromErrnoWithFilename(PyExc_OSError, entry.path);
      entry_reset(&entry);
      result = -1;
      goto cleanup;
    }

    if (entry.type == ENTRY_DIR) {
      char created[FS_PATH_MAX];

      // Strip the entry's trailing separator (e.g. "d/") so a stat of a
      // same-named file below resolves the file
      size_t out_len = strlen(out_path);
      while (out_len > 0 && FS_IS_SEP(out_path[out_len - 1]))
        out_path[--out_len] = '\0';

      fs_stat existing;
      int found_existing = fs_stat_path(out_path, &existing) == 0;
      int existing_is_dir = found_existing && existing.type == FS_TYPE_DIR;

      if (found_existing && existing_is_dir &&
          policy->overwrite_existing == 3) {
        // RENAME: rename the clashing directory rather than merge into it
        if (rename_conflicting_dir(output_dir, out_path, entry.path, &renames,
                                   &num_renames, &cap_renames, created) != 0 ||
            defer_dir(&deferred, created, entry.mode, entry.mtime) != 0) {
          entry_reset(&entry);
          result = -1;
          goto cleanup;
        }

        entry_reset(&entry);
        continue;
      }

      if (found_existing && !existing_is_dir) {
        if (policy->overwrite_existing == 2) {
          if (fs_unlink(out_path) != 0) {
            PyErr_SetFromErrnoWithFilename(PyExc_OSError, out_path);
            entry_reset(&entry);
            result = -1;
            goto cleanup;
          }
        } else if (policy->overwrite_existing == 1) {
          // SKIP: nested entries under this dir fail on their own with a
          // clear NotADirectoryError when they try to write through it
          entry_reset(&entry);
          archive->skip_entry_data(reader);
          continue;
        } else if (policy->overwrite_existing == 3) {
          // RENAME: probe names as above; no extra containment check needed;
          // the winning candidate is a sibling of out_path, and that parent's
          // containment was already established to reach this fs_stat_path call
          if (rename_conflicting_dir(output_dir, out_path, entry.path, &renames,
                                     &num_renames, &cap_renames,
                                     created) != 0 ||
              defer_dir(&deferred, created, entry.mode, entry.mtime) != 0) {
            entry_reset(&entry);
            result = -1;
            goto cleanup;
          }

          entry_reset(&entry);
          continue;
        } else {
          PyErr_Format(PyExc_OSError,
                       "Cannot create directory, a file already exists: %s",
                       out_path);
          entry_reset(&entry);
          result = -1;
          goto cleanup;
        }
      }

      // An existing directory keeps its own metadata unless asked otherwise
      int restore = !existing_is_dir || (policy->overwrite_dir_metadata &&
                                         policy->overwrite_existing != 1);
      if (prepare_output_dir(resolved_root, out_path, DIR_CREATE_MODE,
                             entry.path) != 0 ||
          (restore &&
           defer_dir(&deferred, out_path, entry.mode, entry.mtime) != 0)) {
        entry_reset(&entry);
        result = -1;
        goto cleanup;
      }
    } else if (entry.type == ENTRY_FILE) {
      char *last_slash = fs_last_sep(out_path);
      if (last_slash) {
        char saved = *last_slash;
        *last_slash = '\0';
        int rc = prepare_output_dir(resolved_root, out_path, DIR_CREATE_MODE,
                                    entry.path);
        *last_slash = saved;
        if (rc != 0) {
          entry_reset(&entry);
          result = -1;
          goto cleanup;
        }
      }

      // Modes 0 and 1 both need the exclusive open
      FILE *f = policy->overwrite_existing == 2 ? fs_fopen(out_path, "wb")
                                                : fs_fopen_exclusive(out_path);

      if (!f && errno == EEXIST && policy->overwrite_existing == 3) {
        // RENAME: try names until one is free; each attempt is its own
        // exclusive open, so this stays race-free
        char candidate[FS_PATH_MAX];
        for (int n = 2; n <= FS_MAX_CONFLICT_ATTEMPTS; n++) {
          if (fs_conflict_path(out_path, n, candidate, sizeof(candidate)) !=
              0) {
            PyErr_Format(PyExc_ValueError,
                         "Archive entry path too long to rename: %s",
                         entry.path);
            entry_reset(&entry);
            result = -1;
            goto cleanup;
          }

          f = fs_fopen_exclusive(candidate);
          if (f || errno != EEXIST)
            break;
        }

        if (f) {
          size_t len = strlen(candidate);
          if (len >= sizeof(out_path))
            len = sizeof(out_path) - 1;
          memcpy(out_path, candidate, len);
          out_path[len] = '\0';
        } else if (errno != EEXIST) {
          PyErr_SetFromErrnoWithFilename(PyExc_OSError, candidate);
          entry_reset(&entry);
          result = -1;
          goto cleanup;
        } else {
          PyErr_Format(PyExc_OSError,
                       "Too many conflicting names for entry: %s", entry.path);
          entry_reset(&entry);
          result = -1;
          goto cleanup;
        }
      }

      if (!f) {
        if (errno == EEXIST && policy->overwrite_existing == 1) {
          entry_reset(&entry);
          archive->skip_entry_data(reader);
          continue;
        }
        PyErr_SetFromErrnoWithFilename(PyExc_OSError, out_path);
        entry_reset(&entry);
        result = -1;
        goto cleanup;
      }

      uint64_t remaining = policy->max_total_size > 0
                               ? policy->max_total_size - written_total
                               : UINT64_MAX;
      uint64_t written = 0;

      int data_ret =
          archive->extract_entry_data(reader, f, remaining, &written, ctx);
      written_total += written;

      if (data_ret != 0) {
        fclose(f);
        entry_reset(&entry);
        // A cancel mid-entry leaves a truncated file; entries already
        // completed are kept, as documented
        if (data_ret == COMP_CANCELLED)
          fs_unlink(out_path);
        result = data_ret;
        goto cleanup;
      }

      fclose(f);

      // Best-effort: a metadata failure must not fail extraction of
      // otherwise-valid data
      if (policy->preserve_permissions)
        fs_chmod(out_path, entry.mode);
      if (policy->preserve_timestamps && entry.mtime > 0)
        fs_set_mtime(out_path, (int64_t)entry.mtime);
    } else if (entry.type == ENTRY_HARDLINK) {
      // Shares the target's inode, and so its metadata too
      if (extract_hardlink(resolved_root, output_dir, out_path,
                           effective_target, entry.path,
                           policy->overwrite_existing) != 0) {
        entry_reset(&entry);
        result = -1;
        goto cleanup;
      }
      archive->skip_entry_data(reader);
    }

    entry_reset(&entry);
  }

  result = ret < 0 ? -1 : 0;

cleanup:
  // Also after a failure, so what was extracted carries its archived metadata
  apply_deferred_dirs(&deferred, policy);
  free(renames);
  return result;
}

int extract_archive(const char *archive_path, const char *output_dir,
                    const char **files, size_t num_files,
                    const ExtractionPolicy *policy, CoreContext *ctx) {
  if (!policy)
    policy = &EXTRACTION_POLICY_DEFAULT;

  if (check_source_readable(archive_path) != 0)
    return -1;

  CompressionPipeline pipe = detect_pipeline_from_path(archive_path);
  if (!pipeline_is_valid(&pipe) || pipe.archive == ARCHIVE_NONE) {
    PyErr_SetString(PyExc_ValueError, "Not an archive format");
    return -1;
  }

  const CArchive *archive = archive_for_pipeline(&pipe);
  if (!archive)
    return -1;

  // Decode the codec stage to a temporary archive first, if present
  char *tmp_path = NULL;
  const char *read_path = archive_path;
  if (pipe.codec != FORMAT_UNKNOWN) {
    const StandaloneFormat *codec = find_standalone_format(pipe.codec);
    tmp_path = make_temp_path(archive_path);
    if (!tmp_path)
      return -1;

    // Seeded with the compressed size; the extraction stage extends it once
    // prevalidation knows how much the entries declare
    fs_stat st;
    ctx_begin_job(ctx, fs_stat_path(archive_path, &st) == 0 ? st.size : 0);

    int codec_ret = codec->decompress_file(archive_path, tmp_path, ctx);
    if (codec_ret != 0) {
      fs_unlink(tmp_path);
      free(tmp_path);
      return codec_ret;
    }
    read_path = tmp_path;
  }

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
    if (tmp_path) {
      fs_unlink(tmp_path);
      free(tmp_path);
    }
    return -1;
  }

  // Two passes over the same reader: validate everything, then extract
  uint64_t declared_total = 0;
  int ret = -1;
  for (int pass = 0; pass < 2; pass++) {
    void *reader = archive->create_reader(read_path);
    if (!reader) {
      ret = -1;
      break;
    }

    if (pass == 1)
      ctx_begin_stage(ctx, declared_total);

    ret = pass == 0
              ? prevalidate_entries(archive, reader, output_dir, resolved_root,
                                    files, num_files, policy, ctx,
                                    &declared_total)
              : extract_entries(archive, reader, output_dir, resolved_root,
                                files, num_files, policy, ctx);

    // A failed pass keeps its own error
    if (archive->close_reader(reader, ret != 0) != 0 && ret == 0)
      ret = -1;

    if (ret != 0)
      break;
  }

  if (tmp_path) {
    fs_unlink(tmp_path);
    free(tmp_path);
  }
  return ret;
}

// Name an EntryType as the Python layer sees it
static const char *entry_type_name(EntryType type) {
  switch (type) {
  case ENTRY_DIR:
    return "dir";
  case ENTRY_SYMLINK:
    return "symlink";
  case ENTRY_SPECIAL:
    return "special";
  case ENTRY_HARDLINK:
    return "hardlink";
  case ENTRY_FILE:
  default:
    return "file";
  }
}

// `compressed_size`, `crc` and `method` are present only for a container that
// compresses each entry separately; tar compresses the whole stream, so those
// keys are absent rather than None
//
// Names are UTF-8 by convention only; surrogateescape keeps one that isn't
// listable, as os.fsdecode does, and None stands for a missing name
static PyObject *decode_entry_name(const char *name) {
  if (!name)
    Py_RETURN_NONE;
  return PyUnicode_DecodeUTF8(name, (Py_ssize_t)strlen(name),
                              "surrogateescape");
}

static PyObject *entry_to_dict(const ArchiveEntry *entry) {
  PyObject *path = decode_entry_name(entry->path ? entry->path : "");
  int is_link = entry->type == ENTRY_SYMLINK || entry->type == ENTRY_HARDLINK;
  PyObject *target =
      path ? decode_entry_name(is_link ? entry->link_target : NULL) : NULL;
  if (!target) {
    Py_XDECREF(path);
    return NULL;
  }

  // "N" hands both references to the dict
  PyObject *item = Py_BuildValue(
      "{s:N, s:s, s:K, s:L, s:I, s:N}", "path", path, "type",
      entry_type_name(entry->type), "size", (unsigned long long)entry->size,
      "mtime", (long long)entry->mtime, "mode", (unsigned int)entry->mode,
      "link_target", target);

  if (!item || !entry->has_compression_detail)
    return item;

  PyObject *detail = Py_BuildValue("{s:K, s:I, s:I}", "compressed_size",
                                   (unsigned long long)entry->compressed_size,
                                   "crc", (unsigned int)entry->crc, "method",
                                   (unsigned int)entry->method);

  if (!detail) {
    Py_DECREF(item);
    return NULL;
  }

  int merged = PyDict_Update(item, detail);
  Py_DECREF(detail);

  if (merged < 0) {
    Py_DECREF(item);
    return NULL;
  }

  return item;
}

// Collect one dict per entry from an already-open reader into a new Python list
static PyObject *read_archive_entries(const CArchive *archive, void *reader,
                                      CoreContext *ctx) {
  PyObject *list = PyList_New(0);
  if (!list)
    return NULL;

  ArchiveEntry entry = {0};
  int ret;

  while ((ret = archive->get_next_entry(reader, &entry, ctx)) == 1) {
    PyObject *item = entry_to_dict(&entry);
    entry_reset(&entry);

    if (!item) {
      Py_DECREF(list);
      return NULL;
    }
    if (PyList_Append(list, item) < 0) {
      Py_DECREF(item);
      Py_DECREF(list);
      return NULL;
    }
    Py_DECREF(item);
    archive->skip_entry_data(reader);
  }

  if (ret < 0) {
    Py_DECREF(list);
    return NULL;
  }

  return list;
}

PyObject *list_archive_contents(const char *archive_path, CoreContext *ctx) {
  if (check_source_readable(archive_path) != 0)
    return NULL;

  CompressionPipeline pipe = detect_pipeline_from_path(archive_path);
  if (!pipeline_is_valid(&pipe) || pipe.archive == ARCHIVE_NONE) {
    PyErr_SetString(PyExc_ValueError, "Not an archive format");
    return NULL;
  }

  const CArchive *archive = archive_for_pipeline(&pipe);
  if (!archive)
    return NULL;

  // Decode the codec stage to a temporary archive first, if present
  char *tmp_path = NULL;
  const char *read_path = archive_path;
  if (pipe.codec != FORMAT_UNKNOWN) {
    const StandaloneFormat *codec = find_standalone_format(pipe.codec);
    tmp_path = make_temp_path(archive_path);
    if (!tmp_path)
      return NULL;
    if (codec->decompress_file(archive_path, tmp_path, ctx) != 0) {
      fs_unlink(tmp_path);
      free(tmp_path);
      return NULL;
    }
    read_path = tmp_path;
  }

  void *reader = archive->create_reader(read_path);
  if (!reader) {
    if (tmp_path) {
      fs_unlink(tmp_path);
      free(tmp_path);
    }
    return NULL;
  }

  PyObject *list = read_archive_entries(archive, reader, ctx);
  // A close failure after a full listing would otherwise return the list
  if (archive->close_reader(reader, list == NULL) != 0) {
    Py_XDECREF(list);
    list = NULL;
  }

  if (tmp_path) {
    fs_unlink(tmp_path);
    free(tmp_path);
  }
  return list;
}

// ---- Archive Capabilities ----

PyObject *get_archive_capabilities(void) {
  PyObject *list = PyList_New(0);
  if (!list)
    return NULL;

  const CArchive *backends[] = {get_tar_archive(), get_zip_archive()};
  size_t n = sizeof(backends) / sizeof(backends[0]);

  for (size_t i = 0; i < n; i++) {
    const CArchive *a = backends[i];
    if (!a || !a->is_available())
      continue;

    PyObject *dict = PyDict_New();
    if (!dict) {
      Py_DECREF(list);
      return NULL;
    }

    PyObject *name = PyUnicode_FromString(a->name ? a->name : "");
    PyObject *streaming = a->supports_streaming() ? Py_True : Py_False;
    PyObject *compression = a->supports_compression() ? Py_True : Py_False;

    if (!name || PyDict_SetItemString(dict, "name", name) < 0 ||
        PyDict_SetItemString(dict, "streaming", streaming) < 0 ||
        PyDict_SetItemString(dict, "compression", compression) < 0) {
      Py_XDECREF(name);
      Py_DECREF(dict);
      Py_DECREF(list);
      return NULL;
    }
    Py_DECREF(name);

    if (PyList_Append(list, dict) < 0) {
      Py_DECREF(dict);
      Py_DECREF(list);
      return NULL;
    }
    Py_DECREF(dict);
  }

  return list;
}
