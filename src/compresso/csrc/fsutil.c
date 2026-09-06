#include "fsutil.h"

#include <errno.h>
#include <stdlib.h>
#include <string.h>

// Split-and-create helper shared by both platforms
static int fs_mkdir_one(const char *path, uint32_t mode);

int fs_mkdir_p(const char *path, uint32_t mode) {
  char tmp[FS_PATH_MAX];
  size_t len = strlen(path);
  if (len == 0 || len >= sizeof(tmp)) {
    errno = ENAMETOOLONG;
    return -1;
  }

  memcpy(tmp, path, len + 1);

  for (char *p = tmp + 1; *p; p++) {
    if (*p == '/') {
      *p = '\0';
      if (fs_mkdir_one(tmp, 0755) != 0)
        return -1;
      *p = '/';
    }
  }

  return fs_mkdir_one(tmp, mode);
}

#if defined(_WIN32) || defined(_WIN64)

// ---- Windows implementation ----

#include <direct.h>
#include <fcntl.h>
#include <io.h>
#include <share.h>
#include <sys/stat.h>
#include <windows.h>

int fs_stat_path(const char *path, fs_stat *out) {
  struct __stat64 st;
  if (_stat64(path, &st) != 0)
    return -1;

  out->size = (uint64_t)st.st_size;
  out->mtime = (int64_t)st.st_mtime;
  out->mode = (uint32_t)(st.st_mode & 0777);

  if (st.st_mode & _S_IFDIR)
    out->type = FS_TYPE_DIR;
  else if (st.st_mode & _S_IFREG)
    out->type = FS_TYPE_FILE;
  else
    out->type = FS_TYPE_OTHER;

  return 0;
}

int fs_readlink(const char *path, char *buf, size_t buf_size) {
  (void)path;
  (void)buf;
  (void)buf_size;
  // Windows symlinks are reparse points requiring privileged handling; the
  // default extraction policy denies symlinks, so this stays unimplemented
  errno = ENOSYS;
  return -1;
}

struct fs_dir {
  HANDLE handle;
  WIN32_FIND_DATAA data;
  int pending; // 1 while `data` holds an unconsumed entry
};

fs_dir *fs_opendir(const char *path) {
  char pattern[FS_PATH_MAX];
  int n = snprintf(pattern, sizeof(pattern), "%s\\*", path);
  if (n < 0 || (size_t)n >= sizeof(pattern)) {
    errno = ENAMETOOLONG;
    return NULL;
  }

  fs_dir *dir = calloc(1, sizeof(*dir));
  if (!dir) {
    errno = ENOMEM;
    return NULL;
  }

  dir->handle = FindFirstFileA(pattern, &dir->data);
  if (dir->handle == INVALID_HANDLE_VALUE) {
    free(dir);
    errno = ENOENT;
    return NULL;
  }
  dir->pending = 1;
  return dir;
}

const char *fs_readdir(fs_dir *dir) {
  while (dir->pending) {
    const char *name = dir->data.cFileName;
    int skip = (strcmp(name, ".") == 0 || strcmp(name, "..") == 0);

    // Advance to the next entry for the following call
    if (!FindNextFileA(dir->handle, &dir->data))
      dir->pending = 0;

    if (!skip)
      return name;
  }
  return NULL;
}

void fs_closedir(fs_dir *dir) {
  if (!dir)
    return;
  if (dir->handle != INVALID_HANDLE_VALUE)
    FindClose(dir->handle);
  free(dir);
}

int fs_realpath(const char *path, char *resolved) {
  // GetFullPathName normalises "." and ".." without touching the filesystem
  DWORD n = GetFullPathNameA(path, FS_PATH_MAX, resolved, NULL);
  if (n == 0 || n >= FS_PATH_MAX) {
    errno = EINVAL;
    return -1;
  }
  return 0;
}

int fs_mkstemp(char *template_path) {
  if (_mktemp_s(template_path, strlen(template_path) + 1) != 0)
    return -1;

  int fd;
  errno_t e =
      _sopen_s(&fd, template_path, _O_CREAT | _O_EXCL | _O_WRONLY | _O_BINARY,
               _SH_DENYNO, _S_IREAD | _S_IWRITE);
  if (e != 0) {
    errno = e;
    return -1;
  }
  _close(fd);
  return 0;
}

static int fs_mkdir_one(const char *path, uint32_t mode) {
  (void)mode; // Windows has no POSIX permission bits on directories
  if (_mkdir(path) != 0 && errno != EEXIST)
    return -1;
  return 0;
}

int fs_chmod(const char *path, uint32_t mode) {
  // _chmod only distinguishes read-only from read/write
  int win_mode = (mode & 0200) ? (_S_IREAD | _S_IWRITE) : _S_IREAD;
  return _chmod(path, win_mode);
}

int fs_unlink(const char *path) { return remove(path); }

#else

// ---- POSIX implementation ----

#include <dirent.h>
#include <sys/stat.h>
#include <unistd.h>

int fs_stat_path(const char *path, fs_stat *out) {
  struct stat st;
  // Follows symlinks
  if (stat(path, &st) != 0)
    return -1;

  out->size = (uint64_t)st.st_size;
  out->mtime = (int64_t)st.st_mtime;
  out->mode = (uint32_t)(st.st_mode & 0777);

  if (S_ISDIR(st.st_mode))
    out->type = FS_TYPE_DIR;
  else if (S_ISLNK(st.st_mode))
    out->type = FS_TYPE_SYMLINK;
  else if (S_ISREG(st.st_mode))
    out->type = FS_TYPE_FILE;
  else
    out->type = FS_TYPE_OTHER;

  return 0;
}

int fs_readlink(const char *path, char *buf, size_t buf_size) {
  ssize_t len = readlink(path, buf, buf_size - 1);
  if (len < 0)
    return -1;
  buf[len] = '\0';
  return 0;
}

struct fs_dir {
  DIR *handle;
};

fs_dir *fs_opendir(const char *path) {
  DIR *handle = opendir(path);
  if (!handle)
    return NULL;

  fs_dir *dir = calloc(1, sizeof(*dir));
  if (!dir) {
    closedir(handle);
    errno = ENOMEM;
    return NULL;
  }
  dir->handle = handle;
  return dir;
}

const char *fs_readdir(fs_dir *dir) {
  struct dirent *dent;
  while ((dent = readdir(dir->handle)) != NULL) {
    if (strcmp(dent->d_name, ".") == 0 || strcmp(dent->d_name, "..") == 0)
      continue;
    return dent->d_name;
  }
  return NULL;
}

void fs_closedir(fs_dir *dir) {
  if (!dir)
    return;
  if (dir->handle)
    closedir(dir->handle);
  free(dir);
}

int fs_realpath(const char *path, char *resolved) {
  return realpath(path, resolved) ? 0 : -1;
}

int fs_mkstemp(char *template_path) {
  int fd = mkstemp(template_path);
  if (fd < 0)
    return -1;
  close(fd);
  return 0;
}

static int fs_mkdir_one(const char *path, uint32_t mode) {
  if (mkdir(path, (mode_t)mode) != 0 && errno != EEXIST)
    return -1;
  return 0;
}

int fs_chmod(const char *path, uint32_t mode) {
  return chmod(path, (mode_t)mode);
}

int fs_unlink(const char *path) { return unlink(path); }

#endif
