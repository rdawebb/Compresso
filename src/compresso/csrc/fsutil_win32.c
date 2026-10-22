#include "fsutil.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <direct.h>
#include <fcntl.h>
#include <io.h>
#include <share.h>
#include <sys/stat.h>
#include <wchar.h>
#include <windows.h>
#include <winioctl.h>

// A reparse point's data buffer to decode a symlink/junction target
typedef struct _COMPRESSO_REPARSE_DATA_BUFFER {
  ULONG ReparseTag;
  USHORT ReparseDataLength;
  USHORT Reserved;
  union {
    struct {
      USHORT SubstituteNameOffset;
      USHORT SubstituteNameLength;
      USHORT PrintNameOffset;
      USHORT PrintNameLength;
      ULONG Flags;
      WCHAR PathBuffer[1];
    } SymbolicLinkReparseBuffer;
    struct {
      USHORT SubstituteNameOffset;
      USHORT SubstituteNameLength;
      USHORT PrintNameOffset;
      USHORT PrintNameLength;
      WCHAR PathBuffer[1];
    } MountPointReparseBuffer;
  };
} COMPRESSO_REPARSE_DATA_BUFFER;

// A UTF-8 byte never becomes more than one UTF-16 code unit, so FS_PATH_MAX
// wide characters always hold the conversion of an FS_PATH_MAX-byte path
int fs_widen(const char *utf8, wchar_t *out, int out_count) {
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
  out->special = 0;
  out->rdev = 0;
  out->dev = 0;
  out->ino = 0;
  out->nlink = 0;

  if (is_reparse)
    out->type = FS_TYPE_SYMLINK;
  else if (st.st_mode & _S_IFDIR)
    out->type = FS_TYPE_DIR;
  else if (st.st_mode & _S_IFREG)
    out->type = FS_TYPE_FILE;
  else
    out->type = FS_TYPE_OTHER;

  // _wstat64 leaves st_ino 0, so the identity needs a handle; only regular
  // files get one, as nothing compares directories
  if (out->type == FS_TYPE_FILE) {
    HANDLE h = fs_open_for_metadata(wpath);
    BY_HANDLE_FILE_INFORMATION info;
    if (h != INVALID_HANDLE_VALUE) {
      if (GetFileInformationByHandle(h, &info)) {
        out->dev = info.dwVolumeSerialNumber;
        out->ino = ((uint64_t)info.nFileIndexHigh << 32) | info.nFileIndexLow;
        out->nlink = info.nNumberOfLinks;
      }
      CloseHandle(h);
    }
  }

  return 0;
}

int fs_fstat(FILE *f, fs_stat *out) {
  struct __stat64 st;
  if (_fstat64(_fileno(f), &st) != 0)
    return -1;

  memset(out, 0, sizeof(*out));
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
  wchar_t wpath[FS_PATH_MAX];
  if (fs_widen(path, wpath, FS_PATH_MAX) != 0)
    return -1;

  // FILE_FLAG_OPEN_REPARSE_POINT keeps the handle on the link itself instead
  // of following it, so the reparse buffer below is the link's own data
  HANDLE handle = CreateFileW(
      wpath, 0, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL,
      OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
      NULL);
  if (handle == INVALID_HANDLE_VALUE) {
    errno = ENOENT;
    return -1;
  }

  char reparse_buf[MAXIMUM_REPARSE_DATA_BUFFER_SIZE];
  DWORD bytes_returned = 0;
  BOOL ok =
      DeviceIoControl(handle, FSCTL_GET_REPARSE_POINT, NULL, 0, reparse_buf,
                      sizeof(reparse_buf), &bytes_returned, NULL);
  CloseHandle(handle);

  if (!ok) {
    errno = EINVAL;
    return -1;
  }

  const COMPRESSO_REPARSE_DATA_BUFFER *rdb =
      (const COMPRESSO_REPARSE_DATA_BUFFER *)reparse_buf;
  const char *path_buffer;
  USHORT print_name_offset, print_name_length;

  if (rdb->ReparseTag == IO_REPARSE_TAG_SYMLINK) {
    path_buffer = (const char *)rdb->SymbolicLinkReparseBuffer.PathBuffer;
    print_name_offset = rdb->SymbolicLinkReparseBuffer.PrintNameOffset;
    print_name_length = rdb->SymbolicLinkReparseBuffer.PrintNameLength;
  } else if (rdb->ReparseTag == IO_REPARSE_TAG_MOUNT_POINT) {
    path_buffer = (const char *)rdb->MountPointReparseBuffer.PathBuffer;
    print_name_offset = rdb->MountPointReparseBuffer.PrintNameOffset;
    print_name_length = rdb->MountPointReparseBuffer.PrintNameLength;
  } else {
    // Not a symlink or junction; nothing this reads knows how to decode
    errno = EINVAL;
    return -1;
  }

  size_t name_wchars = print_name_length / sizeof(WCHAR);
  if (name_wchars == 0 || name_wchars >= FS_PATH_MAX) {
    errno = ENAMETOOLONG;
    return -1;
  }

  wchar_t wtarget[FS_PATH_MAX];
  memcpy(wtarget, path_buffer + print_name_offset, name_wchars * sizeof(WCHAR));
  wtarget[name_wchars] = L'\0';

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
  if (fs_widen(mode, wmode, (int)(sizeof(wmode) / sizeof(wmode[0]) - 1)) != 0)
    return NULL;
  if (mode[0] == 'r') {
    size_t n = wcslen(wmode);
    wmode[n] = L'S'; // FILE_FLAG_SEQUENTIAL_SCAN
    wmode[n + 1] = L'\0';
  }
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

static int fs_set_handle_mtime(HANDLE h, int64_t mtime) {
  // FILETIME counts 100ns intervals since 1601-01-01
  ULONGLONG ticks = (ULONGLONG)(mtime + 11644473600LL) * 10000000ULL;
  FILETIME ft = {(DWORD)ticks, (DWORD)(ticks >> 32)};
  if (h == INVALID_HANDLE_VALUE || !SetFileTime(h, NULL, &ft, &ft)) {
    errno = EACCES;
    return -1;
  }
  return 0;
}

int fs_set_mtime(const char *path, int64_t mtime) {
  wchar_t wpath[FS_PATH_MAX];
  if (fs_widen(path, wpath, FS_PATH_MAX) != 0)
    return -1;

  // _wutime64 opens the path as a file, which fails for a directory
  HANDLE h = CreateFileW(
      wpath, FILE_WRITE_ATTRIBUTES,
      FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL,
      OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
      NULL);
  if (h == INVALID_HANDLE_VALUE) {
    errno = EACCES;
    return -1;
  }

  int rc = fs_set_handle_mtime(h, mtime);
  CloseHandle(h);
  return rc;
}

int fs_futimens(FILE *f, int64_t mtime) {
  return fs_set_handle_mtime((HANDLE)_get_osfhandle(_fileno(f)), mtime);
}

int fs_fchmod(FILE *f, uint32_t mode) {
  // Zero times are left as they are; the attributes are written, not merged,
  // as a write-only handle cannot read them
  FILE_BASIC_INFO info = {0};
  info.FileAttributes =
      (mode & 0200) ? FILE_ATTRIBUTE_NORMAL : FILE_ATTRIBUTE_READONLY;
  if (!SetFileInformationByHandle((HANDLE)_get_osfhandle(_fileno(f)),
                                  FileBasicInfo, &info, sizeof(info))) {
    errno = EACCES;
    return -1;
  }
  return 0;
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

FILE *fs_mkstemp(char *template_path) {
  size_t narrow_size = strlen(template_path) + 1;

  wchar_t wtemplate[FS_PATH_MAX];
  if (fs_widen(template_path, wtemplate, FS_PATH_MAX) != 0)
    return NULL;

  if (_wmktemp_s(wtemplate, wcslen(wtemplate) + 1) != 0)
    return NULL;

  // The substituted characters keep the name exactly as long as the template,
  // so it still fits the caller's buffer
  if (fs_narrow(wtemplate, template_path, (int)narrow_size) != 0)
    return NULL;

  int fd;
  errno_t e =
      _wsopen_s(&fd, wtemplate, _O_CREAT | _O_EXCL | _O_WRONLY | _O_BINARY,
                _SH_DENYNO, _S_IREAD | _S_IWRITE);
  if (e != 0) {
    errno = e;
    return NULL;
  }

  FILE *f = _fdopen(fd, "wb");
  if (!f) {
    int saved = errno;
    _close(fd);
    _wremove(wtemplate);
    errno = saved;
  }
  return f;
}

// errno for GetLastError(), or `fallback` for a code with no closer match
static void set_errno_from_win32(int fallback) {
  switch (GetLastError()) {
  case ERROR_ALREADY_EXISTS:
  case ERROR_FILE_EXISTS:
    errno = EEXIST;
    break;
  case ERROR_FILE_NOT_FOUND:
  case ERROR_PATH_NOT_FOUND:
    errno = ENOENT;
    break;
  case ERROR_NOT_SAME_DEVICE:
    errno = EXDEV;
    break;
  case ERROR_ACCESS_DENIED:
    errno = EACCES;
    break;
  default:
    errno = fallback;
    break;
  }
}

int fs_rename(const char *from, const char *to, int replace) {
  wchar_t wfrom[FS_PATH_MAX], wto[FS_PATH_MAX];
  if (fs_widen(from, wfrom, FS_PATH_MAX) != 0 ||
      fs_widen(to, wto, FS_PATH_MAX) != 0)
    return -1;

  if (MoveFileExW(wfrom, wto, replace ? MOVEFILE_REPLACE_EXISTING : 0))
    return 0;

  // Includes a destination that is a directory or open elsewhere
  set_errno_from_win32(EACCES);
  return -1;
}

int fs_mkdir_exclusive(const char *path, uint32_t mode) {
  (void)mode; // Windows has no POSIX permission bits on directories

  wchar_t wpath[FS_PATH_MAX];
  if (fs_widen(path, wpath, FS_PATH_MAX) != 0)
    return -1;

  return _wmkdir(wpath); // EEXIST left on errno, unlike fs_mkdir_exist_ok
}

int fs_chmod(const char *path, uint32_t mode) {
  wchar_t wpath[FS_PATH_MAX];
  if (fs_widen(path, wpath, FS_PATH_MAX) != 0)
    return -1;

  // _wchmod only distinguishes read-only from read/write
  int win_mode = (mode & 0200) ? (_S_IREAD | _S_IWRITE) : _S_IREAD;
  return _wchmod(wpath, win_mode);
}

uint32_t fs_umask(void) { return 0; }

int fs_unlink(const char *path) {
  wchar_t wpath[FS_PATH_MAX];
  if (fs_widen(path, wpath, FS_PATH_MAX) != 0)
    return -1;

  if (_wremove(wpath) == 0)
    return 0;

  // A directory symlink is removed as a directory
  DWORD attrs = GetFileAttributesW(wpath);
  if (attrs != INVALID_FILE_ATTRIBUTES &&
      (attrs & FILE_ATTRIBUTE_REPARSE_POINT) &&
      (attrs & FILE_ATTRIBUTE_DIRECTORY))
    return _wrmdir(wpath);
  return -1;
}

int fs_rmdir(const char *path) {
  wchar_t wpath[FS_PATH_MAX];
  if (fs_widen(path, wpath, FS_PATH_MAX) != 0)
    return -1;

  return _wrmdir(wpath);
}

int fs_link(const char *existing, const char *new_path) {
  wchar_t wexisting[FS_PATH_MAX], wnew[FS_PATH_MAX];
  if (fs_widen(existing, wexisting, FS_PATH_MAX) != 0 ||
      fs_widen(new_path, wnew, FS_PATH_MAX) != 0)
    return -1;

  if (CreateHardLinkW(wnew, wexisting, NULL))
    return 0;

  set_errno_from_win32(EPERM); // e.g. a FAT volume, which has no hardlinks
  return -1;
}

#ifndef SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE
#define SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE 0x2
#endif

int fs_symlink(const char *target, const char *link_path) {
  wchar_t wtarget[FS_PATH_MAX], wlink[FS_PATH_MAX], wresolved[FS_PATH_MAX];
  if (fs_widen(target, wtarget, FS_PATH_MAX) != 0 ||
      fs_widen(link_path, wlink, FS_PATH_MAX) != 0)
    return -1;

  // A relative link resolves only through backslashes
  for (wchar_t *p = wtarget; *p; p++)
    if (*p == L'/')
      *p = L'\\';

  // Resolved from link's own directory; a target not there yet gets a file link
  DWORD flags = SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE;
  wchar_t *sep = wcsrchr(wlink, L'\\');
  wchar_t *fwd = wcsrchr(wlink, L'/');
  if (fwd && (!sep || fwd > sep))
    sep = fwd;
  size_t dir_len = sep ? (size_t)(sep - wlink) + 1 : 0;
  if (dir_len + wcslen(wtarget) < FS_PATH_MAX) {
    wmemcpy(wresolved, wlink, dir_len);
    wcscpy(wresolved + dir_len, wtarget);
    DWORD attrs = GetFileAttributesW(wresolved);
    if (attrs != INVALID_FILE_ATTRIBUTES && (attrs & FILE_ATTRIBUTE_DIRECTORY))
      flags |= SYMBOLIC_LINK_FLAG_DIRECTORY;
  }

  BOOLEAN ok = CreateSymbolicLinkW(wlink, wtarget, flags);
  // Windows before 10 1703 rejects the unprivileged flag
  if (!ok && GetLastError() == ERROR_INVALID_PARAMETER)
    ok = CreateSymbolicLinkW(
        wlink, wtarget, flags & ~SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE);
  if (ok)
    return 0;

  set_errno_from_win32(EPERM); // e.g. ERROR_PRIVILEGE_NOT_HELD
  return -1;
}

int fs_can_mknod(uint32_t type) {
  (void)type;
  return 0;
}

int fs_mknod(const char *path, uint32_t type, uint32_t mode, uint64_t rdev) {
  (void)path;
  (void)type;
  (void)mode;
  (void)rdev;
  errno = ENOTSUP;
  return -1;
}
