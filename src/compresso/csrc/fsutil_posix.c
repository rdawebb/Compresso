#if defined(__linux__) && !defined(_GNU_SOURCE)
#define _GNU_SOURCE // renameat2
#endif

#include "fsutil.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <dirent.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <unistd.h>

static void fs_fill_stat(const struct stat *st, fs_stat *out) {
  out->size = (uint64_t)st->st_size;
  out->mtime = (int64_t)st->st_mtime;
  out->mode = (uint32_t)(st->st_mode & 0777);
  out->dev = (uint64_t)st->st_dev;
  out->ino = (uint64_t)st->st_ino;

  if (S_ISDIR(st->st_mode))
    out->type = FS_TYPE_DIR;
  else if (S_ISLNK(st->st_mode))
    out->type = FS_TYPE_SYMLINK;
  else if (S_ISREG(st->st_mode))
    out->type = FS_TYPE_FILE;
  else
    out->type = FS_TYPE_OTHER;

  int storable = S_ISFIFO(st->st_mode) || S_ISCHR(st->st_mode) ||
                 S_ISBLK(st->st_mode);
  out->special = storable ? (uint32_t)(st->st_mode & S_IFMT) : 0;
  out->rdev = storable ? (uint64_t)st->st_rdev : 0;
}

int fs_stat_path(const char *path, fs_stat *out) {
  struct stat st;
  // lstat to prevent the archiver walking into a symlinked directory
  if (lstat(path, &st) != 0)
    return -1;
  fs_fill_stat(&st, out);
  return 0;
}

int fs_fstat(FILE *f, fs_stat *out) {
  struct stat st;
  if (fstat(fileno(f), &st) != 0)
    return -1;
  fs_fill_stat(&st, out);
  return 0;
}

int fs_readlink(const char *path, char *buf, size_t buf_size) {
  // readlink doesn't terminate and silently truncates, so a result that fills
  // the whole buffer may have been cut short
  ssize_t len = readlink(path, buf, buf_size);
  if (len < 0)
    return -1;
  if ((size_t)len >= buf_size) {
    errno = ENAMETOOLONG;
    return -1;
  }
  buf[len] = '\0';
  return 0;
}

struct fs_dir {
  DIR *handle;
};

FILE *fs_fopen(const char *path, const char *mode) {
  FILE *f = fopen(path, mode);
#ifdef POSIX_FADV_SEQUENTIAL
  // Only a hint, so a failure changes nothing
  if (f && mode[0] == 'r')
    (void)posix_fadvise(fileno(f), 0, 0, POSIX_FADV_SEQUENTIAL);
#endif
  return f;
}

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
  struct timespec times[2] = {{(time_t)mtime, 0}, {(time_t)mtime, 0}};
  return utimensat(AT_FDCWD, path, times, AT_SYMLINK_NOFOLLOW);
}

int fs_futimens(FILE *f, int64_t mtime) {
  struct timespec times[2] = {{(time_t)mtime, 0}, {(time_t)mtime, 0}};
  return futimens(fileno(f), times);
}

int fs_fchmod(FILE *f, uint32_t mode) {
  return fchmod(fileno(f), (mode_t)mode);
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

// Opening with 0666 instead lets the kernel apply the umask
FILE *fs_mkstemp(char *template_path) {
  static const char CHARS[] =
      "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";

  size_t len = strlen(template_path);
  if (len < 6 || strcmp(template_path + len - 6, "XXXXXX") != 0) {
    errno = EINVAL;
    return NULL;
  }
  char *x = template_path + len - 6;

  struct timeval tv;
  gettimeofday(&tv, NULL);
  uint64_t seed = ((uint64_t)tv.tv_sec << 20) ^ (uint64_t)tv.tv_usec ^
                  ((uint64_t)getpid() << 40) ^ (uint64_t)(uintptr_t)&tv;

  for (int attempt = 0; attempt < 100; attempt++) {
    seed = seed * 6364136223846793005ULL + 1442695040888963407ULL;
    uint64_t r = seed >> 16;
    for (int i = 0; i < 6; i++) {
      x[i] = CHARS[r % (sizeof(CHARS) - 1)];
      r /= sizeof(CHARS) - 1;
    }

    int fd = open(template_path, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0666);
    if (fd >= 0) {
      FILE *f = fdopen(fd, "wb");
      if (!f) {
        int saved = errno;
        close(fd);
        unlink(template_path);
        errno = saved;
      }
      return f;
    }
    if (errno != EEXIST)
      return NULL;
  }

  errno = EEXIST;
  return NULL;
}

int fs_rename(const char *from, const char *to, int replace) {
  if (replace)
    return rename(from, to);

#if defined(__linux__)
  if (renameat2(AT_FDCWD, from, AT_FDCWD, to, RENAME_NOREPLACE) == 0)
    return 0;
  if (errno != EINVAL && errno != ENOSYS)
    return -1;
#elif defined(__APPLE__)
  if (renamex_np(from, to, RENAME_EXCL) == 0)
    return 0;
  if (errno != ENOTSUP && errno != EINVAL)
    return -1;
#endif

  // For filesystems without a no-replace rename; link(2) never replaces
  if (link(from, to) == 0) {
    unlink(from);
    return 0;
  }
  if (errno != EPERM && errno != ENOTSUP && errno != EOPNOTSUPP)
    return -1;

  // Reserves name, then renames over it to avoid race window in rename(2)
  int fd = open(to, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
  if (fd < 0)
    return -1;
  close(fd);
  if (rename(from, to) != 0) {
    int saved = errno;
    unlink(to);
    errno = saved;
    return -1;
  }
  return 0;
}

int fs_mkdir_exclusive(const char *path, uint32_t mode) {
  return mkdir(path,
               (mode_t)mode); // EEXIST left on errno, unlike fs_mkdir_exist_ok
}

int fs_chmod(const char *path, uint32_t mode) {
  return chmod(path, (mode_t)mode);
}

uint32_t fs_umask(void) {
  mode_t old = umask(0);
  umask(old);
  return (uint32_t)old;
}

int fs_unlink(const char *path) { return unlink(path); }

int fs_rmdir(const char *path) { return rmdir(path); }

// linkat without AT_SYMLINK_FOLLOW, since link(2) follows a symlink on macOS
int fs_link(const char *existing, const char *new_path) {
  return linkat(AT_FDCWD, existing, AT_FDCWD, new_path, 0);
}

int fs_symlink(const char *target, const char *link_path) {
  return symlink(target, link_path);
}
