#ifndef WALK_H
#define WALK_H

#include "../context.h"
#include "../fsutil.h"
#include <stddef.h>
#include <stdint.h>

// The archive and its temp file, for the walk to skip: tar writes the temp
// during the walk, and an output being overwritten still holds the old archive
typedef struct {
  fs_stat files[2];
  size_t count;
} OwnFiles;

void own_files_add(OwnFiles *own, const char *path);

typedef struct {
  char *path;        // An input as given, or a path found below one
  size_t prefix_len; // Its input's parent prefix, which entry names drop
  fs_stat st;
} WalkItem;

typedef struct {
  WalkItem *items;
  size_t count, cap;
  uint64_t file_bytes; // The regular files' total, for progress
} WalkList;

// Every input and everything below it, depth-first, one lstat each; special
// files and `own` are skipped with a warning; -1 sets OSError, and `out` needs
// walk_list_free either way
int walk_sources(const char **input_paths, size_t num_paths,
                 const OwnFiles *own, CoreContext *ctx, WalkList *out);

void walk_list_free(WalkList *list);

#endif // WALK_H
