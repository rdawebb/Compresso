#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "paths.h"
#include "../common.h"
#include "../fsutil.h"
#include <stdio.h>
#include <string.h>

const char *next_segment(const char **p, size_t *len) {
  const char *start = *p;
  while (*start && FS_IS_SEP(*start))
    start++;

  const char *end = start;
  while (*end && !FS_IS_SEP(*end))
    end++;

  *len = (size_t)(end - start);
  *p = end;
  return start;
}

int path_has_parent_segment(const char *path) {
  for (const char *p = path; *p;) {
    size_t len;
    const char *start = next_segment(&p, &len);
    if (len == 2 && start[0] == '.' && start[1] == '.')
      return 1;
  }
  return 0;
}

uint32_t path_depth(const char *path) {
  uint32_t depth = 0;
  for (const char *p = path; *p;) {
    size_t len;
    const char *start = next_segment(&p, &len);
    if (len > 0 && !(len == 1 && start[0] == '.'))
      depth++;
  }
  return depth;
}

int dir_is_contained(const char *resolved_root, const char *dir) {
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

int existing_ancestor_is_contained(const char *resolved_root, const char *dir,
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

int validate_entry_path(const CompErrors *errors, const char *output_dir,
                        const char *resolved_root, const char *entry_path,
                        uint32_t depth, const ExtractionPolicy *policy) {
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

int check_symlink_target(const CompErrors *errors, const ArchiveEntry *entry,
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
    size_t len;
    const char *start = next_segment(&p, &len);
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

int validate_link_target(const CompErrors *errors, const char *output_dir,
                         const char *resolved_root, const ArchiveEntry *entry,
                         const char *target, const ExtractionPolicy *policy) {
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

int check_entry_policy(const CompErrors *errors, const ArchiveEntry *entry,
                       const ExtractionPolicy *policy) {
  if (entry->type == ENTRY_SYMLINK && !policy->allow_symlinks) {
    PyErr_Format(errors->extraction_policy,
                 "Archive contains symlink, but policy denies it: %s",
                 entry->path);
    return -1;
  }

  return 0;
}
