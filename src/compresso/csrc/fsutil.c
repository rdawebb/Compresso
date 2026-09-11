#include "fsutil.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// Split-and-create helper shared by both platforms
static int fs_mkdir_one(const char *path, uint32_t mode);

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
#include <sys/utime.h>
#include <wchar.h>
#include <windows.h>

// A UTF-8 byte never becomes more than one UTF-16 code unit, so FS_PATH_MAX
// wide characters always hold the conversion of an FS_PATH_MAX-byte path
static int fs_widen(const char *utf8, wchar_t *out, int out_count) {
  // MB_ERR_INVALID_CHARS refuses malformed input instead of substituting U+FFFD
  if (MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, utf8, -1, out,
                          out_count) == 0) {
    errno =
        GetLastError() == ERROR_NO_UNICODE_TRANSLATION ? EILSEQ : ENAMETOOLONG;
    return -1;
  }
  return 0;
}

static int fs_narrow(const wchar_t *wide, char *out, int out_size) {
  if (WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, wide, -1, out,
                          out_size, NULL, NULL) == 0) {
    errno =
        GetLastError() == ERROR_NO_UNICODE_TRANSLATION ? EILSEQ : ENAMETOOLONG;
    return -1;
  }
  return 0;
}

// Access mask 0 needs no read permission, and FILE_FLAG_BACKUP_SEMANTICS is
// what allows a directory to be opened
//
// FILE_FLAG_OPEN_REPARSE_POINT is deliberately absent, so a junction or symlink
// resolves to whatever it really points at
static HANDLE fs_open_for_metadata(const wchar_t *wpath) {
  return CreateFileW(wpath, 0,
                     FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                     NULL, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, NULL);
}

int fs_stat_path(const char *path, fs_stat *out) {
  wchar_t wpath[FS_PATH_MAX];
  if (fs_widen(path, wpath, FS_PATH_MAX) != 0)
    return -1;

  // _wstat64 follows reparse points and has no S_ISLNK equivalent, so without
  // this archiver walks into any junction
  DWORD attrs = GetFileAttributesW(wpath);
  int is_reparse = attrs != INVALID_FILE_ATTRIBUTES &&
                   (attrs & FILE_ATTRIBUTE_REPARSE_POINT) != 0;

  struct __stat64 st;
  if (_wstat64(wpath, &st) != 0) {
    // A link is still a real entry when its target is missing
    if (!is_reparse)
      return -1;
    memset(out, 0, sizeof(*out));
    out->type = FS_TYPE_SYMLINK;
    return 0;
  }

  out->size = (uint64_t)st.st_size;
  out->mtime = (int64_t)st.st_mtime;
  out->mode = (uint32_t)(st.st_mode & 0777);

  if (is_reparse)
    out->type = FS_TYPE_SYMLINK;
  else if (st.st_mode & _S_IFDIR)
    out->type = FS_TYPE_DIR;
  else if (st.st_mode & _S_IFREG)
    out->type = FS_TYPE_FILE;
  else
    out->type = FS_TYPE_OTHER;

  return 0;
}

int fs_readlink(const char *path, char *buf, size_t buf_size) {
  wchar_t wpath[FS_PATH_MAX];
  if (fs_widen(path, wpath, FS_PATH_MAX) != 0)
    return -1;

  // Reading the literal target would mean decoding the raw reparse buffer;
  // resolving the link gives an absolute target
  HANDLE handle = fs_open_for_metadata(wpath);
  if (handle == INVALID_HANDLE_VALUE) {
    errno = ENOENT;
    return -1;
  }

  wchar_t wtarget[FS_PATH_MAX];
  DWORD n = GetFinalPathNameByHandleW(handle, wtarget, FS_PATH_MAX,
                                      FILE_NAME_NORMALIZED | VOLUME_NAME_DOS);
  CloseHandle(handle);

  if (n == 0 || n >= FS_PATH_MAX) {
    errno = EINVAL;
    return -1;
  }

  return fs_narrow(wtarget, buf, (int)buf_size);
}

struct fs_dir {
  HANDLE handle;
  WIN32_FIND_DATAW data;
  char name[FS_PATH_MAX]; // Current entry as UTF-8, stable across FindNextFileW
  int pending;            // 1 while `data` holds an unconsumed entry
  int failed;             // 1 if iteration stopped on a conversion failure
};

fs_dir *fs_opendir(const char *path) {
  char pattern[FS_PATH_MAX];
  int n = snprintf(pattern, sizeof(pattern), "%s\\*", path);
  if (n < 0 || (size_t)n >= sizeof(pattern)) {
    errno = ENAMETOOLONG;
    return NULL;
  }

  wchar_t wpattern[FS_PATH_MAX];
  if (fs_widen(pattern, wpattern, FS_PATH_MAX) != 0)
    return NULL;

  fs_dir *dir = calloc(1, sizeof(*dir));
  if (!dir) {
    errno = ENOMEM;
    return NULL;
  }

  dir->handle = FindFirstFileW(wpattern, &dir->data);
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
    // Convert the name out first, as advancing overwrites `data` in place
    int converted = fs_narrow(dir->data.cFileName, dir->name, FS_PATH_MAX) == 0;

    // Advance to the next entry for the following call
    if (!FindNextFileW(dir->handle, &dir->data))
      dir->pending = 0;

    if (!converted) {
      dir->failed = 1;
      return NULL;
    }

    if (strcmp(dir->name, ".") != 0 && strcmp(dir->name, "..") != 0)
      return dir->name;
  }
  return NULL;
}

int fs_dir_error(const fs_dir *dir) { return dir ? dir->failed : 0; }

void fs_closedir(fs_dir *dir) {
  if (!dir)
    return;
  if (dir->handle != INVALID_HANDLE_VALUE)
    FindClose(dir->handle);
  free(dir);
}

FILE *fs_fopen(const char *path, const char *mode) {
  wchar_t wpath[FS_PATH_MAX];
  wchar_t wmode[16];
  if (fs_widen(path, wpath, FS_PATH_MAX) != 0)
    return NULL;
  if (fs_widen(mode, wmode, (int)(sizeof(wmode) / sizeof(wmode[0]))) != 0)
    return NULL;
  return _wfopen(wpath, wmode);
}

FILE *fs_fopen_exclusive(const char *path) {
  wchar_t wpath[FS_PATH_MAX];
  if (fs_widen(path, wpath, FS_PATH_MAX) != 0)
    return NULL;

  int fd;
  errno_t e = _wsopen_s(&fd, wpath, _O_CREAT | _O_EXCL | _O_WRONLY | _O_BINARY,
                        _SH_DENYNO, _S_IREAD | _S_IWRITE);
  if (e != 0) {
    errno = e;
    return NULL;
  }

  FILE *f = _fdopen(fd, "wb");
  if (!f) {
    int saved = errno;
    _close(fd);
    errno = saved;
  }
  return f;
}

int fs_set_mtime(const char *path, int64_t mtime) {
  wchar_t wpath[FS_PATH_MAX];
  if (fs_widen(path, wpath, FS_PATH_MAX) != 0)
    return -1;

  struct __utimbuf64 times;
  times.actime = (__time64_t)mtime;
  times.modtime = (__time64_t)mtime;
  return _wutime64(wpath, &times);
}

// Resolves `path` to an absolute path, following reparse points if necessary
int fs_realpath(const char *path, char *resolved) {
  wchar_t wpath[FS_PATH_MAX];
  if (fs_widen(path, wpath, FS_PATH_MAX) != 0)
    return -1;

  HANDLE handle = fs_open_for_metadata(wpath);
  if (handle == INVALID_HANDLE_VALUE) {
    errno = ENOENT;
    return -1;
  }

  wchar_t wresolved[FS_PATH_MAX];
  DWORD n = GetFinalPathNameByHandleW(handle, wresolved, FS_PATH_MAX,
                                      FILE_NAME_NORMALIZED | VOLUME_NAME_DOS);
  CloseHandle(handle);

  if (n == 0 || n >= FS_PATH_MAX) {
    errno = EINVAL;
    return -1;
  }

  return fs_narrow(wresolved, resolved, FS_PATH_MAX);
}

int fs_mkstemp(char *template_path) {
  size_t narrow_size = strlen(template_path) + 1;

  wchar_t wtemplate[FS_PATH_MAX];
  if (fs_widen(template_path, wtemplate, FS_PATH_MAX) != 0)
    return -1;

  if (_wmktemp_s(wtemplate, wcslen(wtemplate) + 1) != 0)
    return -1;

  int fd;
  errno_t e =
      _wsopen_s(&fd, wtemplate, _O_CREAT | _O_EXCL | _O_WRONLY | _O_BINARY,
                _SH_DENYNO, _S_IREAD | _S_IWRITE);
  if (e != 0) {
    errno = e;
    return -1;
  }
  _close(fd);

  // The substituted characters keep the name exactly as long as the template,
  // so it still fits the caller's buffer
  return fs_narrow(wtemplate, template_path, (int)narrow_size);
}

static int fs_mkdir_one(const char *path, uint32_t mode) {
  (void)mode; // Windows has no POSIX permission bits on directories

  wchar_t wpath[FS_PATH_MAX];
  if (fs_widen(path, wpath, FS_PATH_MAX) != 0)
    return -1;

  if (_wmkdir(wpath) != 0 && errno != EEXIST)
    return -1;
  return 0;
}

int fs_chmod(const char *path, uint32_t mode) {
  wchar_t wpath[FS_PATH_MAX];
  if (fs_widen(path, wpath, FS_PATH_MAX) != 0)
    return -1;

  // _wchmod only distinguishes read-only from read/write
  int win_mode = (mode & 0200) ? (_S_IREAD | _S_IWRITE) : _S_IREAD;
  return _wchmod(wpath, win_mode);
}

int fs_unlink(const char *path) {
  wchar_t wpath[FS_PATH_MAX];
  if (fs_widen(path, wpath, FS_PATH_MAX) != 0)
    return -1;

  return _wremove(wpath);
}

#else

// ---- POSIX implementation ----

#include <dirent.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <unistd.h>

int fs_stat_path(const char *path, fs_stat *out) {
  struct stat st;
  // lstat to prevent the archiver walking into a symlinked directory
  if (lstat(path, &st) != 0)
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

FILE *fs_fopen(const char *path, const char *mode) { return fopen(path, mode); }

FILE *fs_fopen_exclusive(const char *path) {
  // O_NOFOLLOW so an existing symlink is refused rather than followed; O_EXCL
  // already rejects one, but only where the kernel implements it
  int fd = open(path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0666);
  if (fd < 0)
    return NULL;

  FILE *f = fdopen(fd, "wb");
  if (!f) {
    int saved = errno;
    close(fd);
    errno = saved;
  }
  return f;
}

int fs_set_mtime(const char *path, int64_t mtime) {
  struct timeval times[2];
  times[0].tv_sec = (time_t)mtime; // Access time
  times[0].tv_usec = 0;
  times[1].tv_sec = (time_t)mtime; // Modification time
  times[1].tv_usec = 0;
  return utimes(path, times);
}

int fs_dir_error(const fs_dir *dir) {
  (void)dir;
  // Only the Windows branch can fail mid-iteration, on a name it cannot convert
  return 0;
}

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
