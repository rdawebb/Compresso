#ifndef FSUTIL_H
#define FSUTIL_H

// Portable filesystem helpers; except where noted, functions returning int
// return 0 on success, or -1 with errno set

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

#define FS_PATH_MAX 4096

// Windows accepts either separator and fs_realpath there returns backslashes,
// so prefix comparisons must test both
#if defined(_WIN32) || defined(_WIN64)
#define FS_IS_SEP(c) ((c) == '/' || (c) == '\\')
#else
#define FS_IS_SEP(c) ((c) == '/')
#endif

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

// True if `path` is anything other than a plain relative path: a leading
// separator, or a drive qualifier; Windows forms are rejected on POSIX too,
// since archives are portable
int fs_is_absolute(const char *path);

// True if `path` names an NTFS alternate data stream, which attaches hidden
// content to the named file instead of creating it
// Windows-only: on POSIX ':' is an ordinary filename character
int fs_is_stream_path(const char *path);

// Locate the last path separator in `path`, or NULL if it has none
char *fs_last_sep(const char *path);

// Stat `path` itself, without following symlinks (lstat(2)), so a symlink or
// Windows reparse point reports FS_TYPE_SYMLINK rather than its target's type
int fs_stat_path(const char *path, fs_stat *out);

// Read a symlink's target into `buf` (NUL-terminated)
// On Windows this is the resolved absolute target, not the literal link text
int fs_readlink(const char *path, char *buf, size_t buf_size);

// Directory iteration; fs_readdir skips "." and ".."
// A NULL return means the directory is exhausted or a name could not be
// represented, which would otherwise silently shorten the listing
typedef struct fs_dir fs_dir;
fs_dir *fs_opendir(const char *path); // NULL with errno set on failure
const char *fs_readdir(fs_dir *dir);
int fs_dir_error(const fs_dir *dir); // Non-zero if iteration stopped on error
void fs_closedir(fs_dir *dir);

// Open `path` with fopen(3) semantics, returning NULL with errno set
FILE *fs_fopen(const char *path, const char *mode);

// Open `path` for binary writing, failing with errno EEXIST if it already
// exists; the existence test and the creation are one atomic operation, so an
// existing file is never truncated
FILE *fs_fopen_exclusive(const char *path);

// Set `path`'s modification time to `mtime` seconds since the Unix epoch
// The access time is set to the same value, since archives do not carry one
int fs_set_mtime(const char *path, int64_t mtime);

// Canonicalise `path` into `resolved` (at least FS_PATH_MAX bytes), resolving
// symlinks and Windows reparse points; fails if `path` does not exist
int fs_realpath(const char *path, char *resolved);

// Create a unique temp file from a mkstemp-style template ending in "XXXXXX"
int fs_mkstemp(char *template_path);

// Create `path` and any missing parents, applying `mode` to the final
// component only
int fs_mkdir_p(const char *path, uint32_t mode);

// Apply POSIX permission bits to an existing path; on Windows only the
// read-only bit is honoured
int fs_chmod(const char *path, uint32_t mode);

int fs_unlink(const char *path);

// Current read/write offset in an open stream, as a 64-bit value on every
// platform (plain ftell() is 32-bit on Windows); returns -1 on failure
int64_t fs_ftell(FILE *stream);

// Size of an open stream in bytes, leaving the offset where it found it;
// returns -1 on failure
int64_t fs_stream_size(FILE *stream);

#endif // FSUTIL_H
