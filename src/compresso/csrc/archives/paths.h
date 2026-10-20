#ifndef PATHS_H
#define PATHS_H

#include "../archives.h"
#include <stddef.h>
#include <stdint.h>

// Skips `*p` past any separators and the component after them, returning that
// component; `*len` is 0 when only separators were left
const char *next_segment(const char **p, size_t *len);

// Whether any component of `path` is exactly ".."
int path_has_parent_segment(const char *path);

// Number of real components in `path`, ignoring empty and "." segments
uint32_t path_depth(const char *path);

// Returns 1 if contained, 0 if it escapes, -1 if `dir` could not be resolved
// `resolved_root` must already have been through fs_realpath
int dir_is_contained(const char *resolved_root, const char *dir);

// Whether the deepest already-existing ancestor of `dir` is inside the root;
// its length goes in `existing_len`; only an existing component can redirect
// the path; anything still missing is about to be created rather than followed
int existing_ancestor_is_contained(const char *resolved_root, const char *dir,
                                   size_t *existing_len);

int validate_entry_path(const CompErrors *errors, const char *output_dir,
                        const char *resolved_root, const char *entry_path,
                        uint32_t depth, const ExtractionPolicy *policy);

// `base_depth` is the link's directory depth below the root; a ".." after a
// name is refused, as that name may be a symlink the textual check can't follow
int check_symlink_target(const CompErrors *errors, const ArchiveEntry *entry,
                         uint32_t base_depth);

// A hardlink's target names another entry, so gets an entry path's checks; a
// symlink's is checked from its written path; nothing else has a target
int validate_link_target(const CompErrors *errors, const char *output_dir,
                         const char *resolved_root, const ArchiveEntry *entry,
                         const char *target, const ExtractionPolicy *policy);

int check_entry_policy(const CompErrors *errors, const ArchiveEntry *entry,
                       const ExtractionPolicy *policy);

#endif // PATHS_H
