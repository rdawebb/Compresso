#include "fsutil.h"

#include <ctype.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// Returns the current read/write offset in an open stream as a 64-bit value
int64_t fs_ftell(FILE *stream) {
#if defined(_WIN32) || defined(_WIN64)
  return (int64_t)_ftelli64(stream);
#else
  return (int64_t)ftello(stream);
#endif
}

int64_t fs_stream_size(FILE *stream) {
  int64_t original = fs_ftell(stream);
  if (original < 0)
    return -1;

#if defined(_WIN32) || defined(_WIN64)
  if (_fseeki64(stream, 0, SEEK_END) != 0)
    return -1;
#else
  if (fseeko(stream, 0, SEEK_END) != 0)
    return -1;
#endif

  int64_t size = fs_ftell(stream);

#if defined(_WIN32) || defined(_WIN64)
  if (_fseeki64(stream, original, SEEK_SET) != 0)
    return -1;
#else
  if (fseeko(stream, (off_t)original, SEEK_SET) != 0)
    return -1;
#endif

  return size;
}

int fs_is_absolute(const char *path) {
  // Deliberately tests both separators on every platform - see fsutil.h
  if (path[0] == '/' || path[0] == '\\')
    return 1;

  int is_drive_letter =
      (path[0] >= 'A' && path[0] <= 'Z') || (path[0] >= 'a' && path[0] <= 'z');

  return is_drive_letter && path[1] == ':';
}

char *fs_last_sep(const char *path) {
  char *fwd = strrchr(path, '/');
#if defined(_WIN32) || defined(_WIN64)
  char *back = strrchr(path, '\\');
  if (!fwd)
    return back;
  if (!back)
    return fwd;
  return fwd > back ? fwd : back;
#else
  return fwd;
#endif
}

int fs_join(char *out, size_t out_size, const char *dir, const char *name) {
  size_t dir_len = strlen(dir);
  size_t name_len = strlen(name);
  size_t sep_len = dir_len > 0 && !FS_IS_SEP(dir[dir_len - 1]) ? 1 : 0;

  // Both strings are already in memory, so their lengths can't sum past
  // SIZE_MAX
  if (dir_len + sep_len + name_len >= out_size) {
    errno = ENAMETOOLONG;
    return -1;
  }

  memcpy(out, dir, dir_len);
  if (sep_len)
    out[dir_len] = '/';
  memcpy(out + dir_len + sep_len, name, name_len + 1);
  return 0;
}

int fs_same_file(const fs_stat *a, const fs_stat *b) {
  return a->ino != 0 && a->dev == b->dev && a->ino == b->ino;
}

int fs_is_stream_path(const char *path) {
#if defined(_WIN32) || defined(_WIN64)
  // Callers run this after fs_is_absolute, so a surviving colon is a stream
  // separator rather than a drive qualifier
  return strchr(path, ':') != NULL;
#else
  (void)path;
  return 0;
#endif
}

int fs_conflict_path(const char *path, int n, char *out, size_t out_size) {
  char *last_sep = fs_last_sep(path);
  const char *base = last_sep ? last_sep + 1 : path;

  // Skip leading dots so a hidden file's name is not mistaken for an extension
  const char *scan = base;
  while (*scan == '.')
    scan++;

  const char *dot = strrchr(scan, '.');

  // Handle tarball double-extension
  if (dot && (size_t)(dot - scan) >= 4 && dot[-4] == '.' &&
      tolower((unsigned char)dot[-3]) == 't' &&
      tolower((unsigned char)dot[-2]) == 'a' &&
      tolower((unsigned char)dot[-1]) == 'r')
    dot -= 4;

  size_t stem_len = dot ? (size_t)(dot - path) : strlen(path);
  const char *ext = dot ? dot : "";

#if defined(__APPLE__)
  int written =
      snprintf(out, out_size, "%.*s %d%s", (int)stem_len, path, n, ext);
#else
  int written =
      snprintf(out, out_size, "%.*s (%d)%s", (int)stem_len, path, n, ext);
#endif

  if (written < 0 || (size_t)written >= out_size)
    return -1;

  return 0;
}

int fs_resolve_conflict(const char *path, int overwrite_existing,
                        char *resolved, size_t resolved_size) {
  size_t len = strlen(path);
  if (len >= resolved_size) {
    errno = ENAMETOOLONG;
    return -1;
  }
  memcpy(resolved, path, len + 1);

  if (overwrite_existing == 2) // OVERWRITE: unconditional
    return 0;

  fs_stat st;
  if (fs_stat_path(resolved, &st) != 0)
    return 0; // Nothing there yet

  if (overwrite_existing == 0) { // ERROR
    errno = EEXIST;
    return -1;
  }

  if (overwrite_existing == 1) // SKIP
    return 1;

  // RENAME: probe "name 2", "name 3", ... until one is free
  for (int n = 2; n <= FS_MAX_CONFLICT_ATTEMPTS; n++) {
    char candidate[FS_PATH_MAX];
    if (fs_conflict_path(path, n, candidate, sizeof(candidate)) != 0) {
      errno = ENAMETOOLONG;
      return -1;
    }

    if (fs_stat_path(candidate, &st) != 0) {
      size_t clen = strlen(candidate);
      if (clen >= resolved_size) {
        errno = ENAMETOOLONG;
        return -1;
      }
      memcpy(resolved, candidate, clen + 1);
      return 0;
    }
  }

  errno = EEXIST;
  return -1;
}

int fs_commit_temp(const char *temp, const char *dst, int overwrite_existing,
                   char *actual, size_t actual_size) {
  for (int n = 1; n <= FS_MAX_CONFLICT_ATTEMPTS; n++) {
    char candidate[FS_PATH_MAX];
    const char *target = dst;
    if (n > 1) {
      if (fs_conflict_path(dst, n, candidate, sizeof(candidate)) != 0) {
        errno = ENAMETOOLONG;
        return -1;
      }
      target = candidate;
    }

    // Checked before the move, so a committed file's name is always reported
    size_t len = strlen(target);
    if (len >= actual_size) {
      errno = ENAMETOOLONG;
      return -1;
    }

    if (fs_rename(temp, target, overwrite_existing == 2) == 0) {
      memcpy(actual, target, len + 1);
      return 0;
    }
    if (errno == EEXIST && overwrite_existing == 1)
      return 1;
    if (errno != EEXIST || overwrite_existing != 3)
      return -1;
  }

  errno = EEXIST;
  return -1;
}

// Length of `path`'s root, which exists and can't itself be created: leading
// separators, and on Windows a drive (C:) or a UNC share (\\host\share, which
// also covers the \\?\C: form)
static size_t fs_root_len(const char *path) {
  size_t i = 0;
#if defined(_WIN32) || defined(_WIN64)
  if (FS_IS_SEP(path[0]) && FS_IS_SEP(path[1])) {
    i = 2;
    for (int part = 0; part < 2; part++) {
      while (path[i] && !FS_IS_SEP(path[i]))
        i++;
      while (FS_IS_SEP(path[i]))
        i++;
    }
    return i;
  }
  char drive = (char)(path[0] | 0x20);
  if (drive >= 'a' && drive <= 'z' && path[1] == ':')
    i = 2;
#endif
  while (FS_IS_SEP(path[i]))
    i++;
  return i;
}

static int fs_mkdir_exist_ok(const char *path, uint32_t mode) {
  if (fs_mkdir_exclusive(path, mode) != 0 && errno != EEXIST)
    return -1;
  return 0;
}

int fs_mkdir_p(const char *path, uint32_t mode) {
  char tmp[FS_PATH_MAX];
  size_t len = strlen(path);
  if (len == 0 || len >= sizeof(tmp)) {
    errno = ENAMETOOLONG;
    return -1;
  }

  memcpy(tmp, path, len + 1);

  for (char *p = tmp + fs_root_len(tmp); *p; p++) {
    if (FS_IS_SEP(*p)) {
      char sep = *p;
      *p = '\0';
      if (fs_mkdir_exist_ok(tmp, 0755) != 0)
        return -1;
      *p = sep;
    }
  }

  return fs_mkdir_exist_ok(tmp, mode);
}
