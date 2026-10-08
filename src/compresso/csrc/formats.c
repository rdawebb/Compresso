#include "formats.h"
#include "fsutil.h"
#include "magics.h"
#include <ctype.h>
#include <string.h>

#define COUNT(array) (sizeof(array) / sizeof((array)[0]))

// Each leading magic differs from the rest in its first byte; tar's sits at an
// offset, behind which another format's magic may start, so it comes last
const FormatDesc FORMATS[] = {
    {.id = FORMAT_COMPRESSO,
     .kind = KIND_NATIVE,
     .name = "compresso",
     .exts = {"comp"},
     .magic = magic_is_compresso},
    {.id = FORMAT_GZIP,
     .kind = KIND_CONTAINER,
     .name = "gzip",
     .aliases = {"gz"},
     .exts = {"gz"},
     .tar_short = "tgz",
     .magic = magic_is_gzip},
    {.id = FORMAT_BZIP2,
     .kind = KIND_CONTAINER,
     .name = "bzip2",
     .aliases = {"bz2"},
     .exts = {"bz2"},
     .tar_short = "tbz2",
     .magic = magic_is_bzip2},
    {.id = FORMAT_XZ,
     .kind = KIND_CONTAINER,
     .name = "xz",
     .aliases = {"lzma"},
     .exts = {"xz"},
     .tar_short = "txz",
     .magic = magic_is_xz},
    {.id = FORMAT_ZSTD,
     .kind = KIND_CONTAINER,
     .name = "zstd",
     .aliases = {"zst"},
     .exts = {"zst", "zstd"},
     .tar_short = "tzst",
     .magic = magic_is_zstd},
    {.id = FORMAT_LZ4,
     .kind = KIND_CONTAINER,
     .name = "lz4",
     .exts = {"lz4"},
     .tar_short = "tlz4",
     .magic = magic_is_lz4},
    {.id = FORMAT_ZIP,
     .kind = KIND_ARCHIVE,
     .name = "zip",
     .exts = {"zip"},
     .magic = magic_is_zip,
     .archive = ARCHIVE_ZIP},
    {.id = FORMAT_7Z,
     .kind = KIND_ARCHIVE,
     .name = "7z",
     .exts = {"7z"},
     .magic = magic_is_7z,
     .archive = ARCHIVE_7Z},
    {.id = FORMAT_TAR,
     .kind = KIND_ARCHIVE,
     .name = "tar",
     .exts = {"tar"},
     .magic = magic_is_tar,
     .archive = ARCHIVE_TAR},
    {.name = NULL},
};

static int listed(const char *const *list, size_t n, const char *s) {
  for (size_t i = 0; i < n && list[i]; i++)
    if (strcmp(list[i], s) == 0)
      return 1;
  return 0;
}

const FormatDesc *format_by_id(Format id) {
  for (const FormatDesc *d = FORMATS; d->name; d++)
    if (d->id == id)
      return d;
  return NULL;
}

const FormatDesc *format_by_name(const char *name) {
  if (!name)
    return NULL;
  for (const FormatDesc *d = FORMATS; d->name; d++)
    if (strcmp(d->name, name) == 0 ||
        listed(d->aliases, COUNT(d->aliases), name))
      return d;
  return NULL;
}

// The dot starting the final extension of `path`'s last component, or NULL;
// a leading dot names a hidden file rather than an extension
static const char *final_extension(const char *path, const char **base) {
  const char *sep = fs_last_sep(path);
  *base = sep ? sep + 1 : path;

  const char *dot = strrchr(*base, '.');
  return (dot && dot > *base) ? dot : NULL;
}

// Lowercases `ext` into `out`, truncating to fit; the cast keeps a UTF-8 byte,
// negative as a char, within what tolower accepts
static void lowercase_into(char *out, size_t size, const char *ext) {
  size_t i;
  for (i = 0; i < size - 1 && ext[i]; i++)
    out[i] = (char)tolower((unsigned char)ext[i]);
  out[i] = '\0';
}

const FormatDesc *format_by_ext(const char *path, int *in_tar) {
  if (in_tar)
    *in_tar = 0;
  if (!path)
    return NULL;

  const char *base;
  const char *ext = final_extension(path, &base);
  if (!ext)
    return NULL;

  char lower[16];
  lowercase_into(lower, sizeof(lower), ext + 1);

  const FormatDesc *match = NULL;
  int shorthand = 0;
  for (const FormatDesc *d = FORMATS; d->name && !match; d++) {
    if (listed(d->exts, COUNT(d->exts), lower))
      match = d;
    else if (d->tar_short && strcmp(d->tar_short, lower) == 0) {
      match = d;
      shorthand = 1;
    }
  }

  if (in_tar) {
    // Checks if first part of extension is '.tar'
    const char *prev = ext - 1;
    while (prev > base && *prev != '.')
      prev--;
    *in_tar = shorthand ||
              (prev > base && strncmp(prev, ".tar", 4) == 0 && prev[4] == '.');
  }
  return match;
}

const FormatDesc *format_by_magic(const unsigned char *m, size_t n) {
  if (!m)
    return NULL;
  for (const FormatDesc *d = FORMATS; d->name; d++)
    if (d->magic(m, n))
      return d;
  return NULL;
}
