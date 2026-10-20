#ifndef CHANGES_H
#define CHANGES_H

#include "../archives.h"
#include <stddef.h>
#include <stdint.h>

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
int record_change(Changes *c, const char *path, char *backup, int is_dir);

// Ensure `dir` exists and is inside the extraction root, creating it if needed
int prepare_output_dir(const CompErrors *errors, const char *resolved_root,
                       const char *dir, uint32_t mode, const char *entry_path,
                       Changes *changes);

// Moves `path` to a temp beside it, so an undo can put it back
int move_aside(Changes *c, const char *path);

// Undoes every change after the first `keep`, newest first; best-effort
void undo_changes(Changes *c, size_t keep);

// Keeps the changes, dropping the originals they replaced
void keep_changes(Changes *c);

#endif // CHANGES_H
