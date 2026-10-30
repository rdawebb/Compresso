#ifndef SELECT_H
#define SELECT_H

#include "../archives.h"
#include <stddef.h>

// The requested names, sorted for bsearch; with no includes, none means
// everything
typedef struct {
  const char *name;
  size_t len; // Without trailing slashes
  int matched;
} SelectedName;

typedef struct {
  SelectedName *items;
  size_t count;
  PathFilter filter;
  char *include_found; // One flag per include; NULL with none
} Selection;

// Borrows `files` and the filter's patterns, which must outlive `sel`; free
// `sel->items` and `sel->include_found` when done
int selection_init(Selection *sel, const char **files, size_t num_files,
                   const PathFilter *filter);

// Whether `entry` or a directory above it was requested, as tar matches names,
// or an include matches it, and no exclude does; marks every name and include
// that matches, so "d" and "d/x" both count as found
int entry_is_selected(const ArchiveEntry *entry, Selection *sel);

// Whether the glob `pat` matches any run of whole components in `path`: e.g.
// "*.log" matches "a/b.log", and "a" matches everything below "a/"; the syntax
// is fnmatch(3)'s with no flags, so `*` and `?` also match '/'
int path_matches(const char *path, const char *pat);

// Whether any of `count` patterns matches `path`
int path_matches_any(const char *path, const char **pats, size_t count);

// Raises an error listing unmatched names and includes, and skipped hardlinks
int report_incomplete(const Selection *sel, PyObject *skipped_links,
                      CoreContext *ctx);

#endif // SELECT_H
