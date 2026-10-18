#ifndef WALK_H
#define WALK_H

#include "../context.h"
#include "../fsutil.h"
#include <stddef.h>
#include <stdint.h>

// The archive and its temp file, which the walk skips: tar fills the temp
// mid-walk, and an output being replaced still holds the old archive
typedef struct {
  fs_stat files[2];
  size_t count;
} OwnFiles;

void own_files_add(OwnFiles *own, const char *path);

typedef struct WalkItem {
  char *path;        // An input as given, or a path found below one
  size_t prefix_len; // Its input's parent prefix, which entry names drop
  fs_stat st;
  const struct WalkItem *link; // The file's first name, if this is a later one
} WalkItem;

typedef struct {
  WalkItem *items;
  size_t count, cap;
  uint64_t file_bytes; // The regular files' total, for progress
} WalkList;

// Every input and everything below it, depth-first, symlinks not followed.
// Warns and skips `own` and sockets, and FIFOs and devices unless
// `keep_specials`; with `link_hardlinks`, a file's later names get `link` and
// aren't counted in `file_bytes`; returns COMP_CANCELLED, or -1 with an
// exception set
int walk_sources(const char **input_paths, size_t num_paths,
                 const OwnFiles *own, int keep_specials, int link_hardlinks,
                 CoreContext *ctx, WalkList *out);

void walk_list_free(WalkList *list);

#endif // WALK_H
