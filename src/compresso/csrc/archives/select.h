#ifndef SELECT_H
#define SELECT_H

#include "../archives.h"
#include <stddef.h>

// The requested names, sorted for bsearch; none means everything
typedef struct {
  const char *name;
  size_t len; // Without trailing slashes
  int matched;
} SelectedName;

typedef struct {
  SelectedName *items;
  size_t count;
} Selection;

// Borrows `files`, which must outlive `sel`; free `sel->items` when done
int selection_init(Selection *sel, const char **files, size_t num_files);

// Whether `entry` or a directory above it was requested, as tar matches names;
// marks every name that matches, so "d" and "d/x" both count as found
int entry_is_selected(const ArchiveEntry *entry, Selection *sel);

// Whether the glob `pat` matches any run of whole components in `path`: e.g.
// "*.log" matches "a/b.log", and "a" matches everything below "a/"; the syntax
// is fnmatch(3)'s with no flags, so `*` and `?` also match '/'
int path_matches(const char *path, const char *pat);

// Raises an error listing unmatched requested names and skipped hardlinks
int report_incomplete(const Selection *sel, PyObject *skipped_links,
                      CoreContext *ctx);

#endif // SELECT_H
