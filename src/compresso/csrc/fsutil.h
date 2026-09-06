#ifndef FSUTIL_H
#define FSUTIL_H

// Portable filesystem helpers

#include <stddef.h>
#include <stdint.h>

// Platform-independent path buffer size
#define FS_PATH_MAX 4096

typedef enum {
  FS_TYPE_FILE = 0,
  FS_TYPE_DIR = 1,
  FS_TYPE_SYMLINK = 2,
  FS_TYPE_OTHER = 3,
} fs_file_type;

typedef struct {
  uint64_t size;
  int64_t mtime; // Seconds since the Unix epoch
  uint32_t mode; // POSIX permission bits (0777), best-effort on Windows
  fs_file_type type;
} fs_stat;

// Stat `path`, following symlinks (like POSIX stat(2))
int fs_stat_path(const char *path, fs_stat *out);

// Read a symlink's target into `buf` (NUL-terminated)
int fs_readlink(const char *path, char *buf, size_t buf_size);

// Directory iteration: fs_opendir returns NULL with errno set on failure,
// fs_readdir returns the next entry name, skipping "." and "..", or NULL when
// the directory is exhausted
typedef struct fs_dir fs_dir;
fs_dir *fs_opendir(const char *path);
const char *fs_readdir(fs_dir *dir);
void fs_closedir(fs_dir *dir);

// Canonicalise `path` into `resolved` (must hold at least FS_PATH_MAX bytes)
int fs_realpath(const char *path, char *resolved);

// Create a unique temp file from a mkstemp-style template ending in "XXXXXX"
int fs_mkstemp(char *template_path);

// Create `path` and any missing parent directories, applying `mode` to the
// final component
int fs_mkdir_p(const char *path, uint32_t mode);

// Apply POSIX permission bits to an existing path, best-effort on Windows
// (only the read-only bit is honoured)
int fs_chmod(const char *path, uint32_t mode);

// Remove a file: returns 0 on success, or -1 with errno set
int fs_unlink(const char *path);

#endif // FSUTIL_H
