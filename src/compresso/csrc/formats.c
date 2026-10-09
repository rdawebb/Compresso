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
     .magic = magic_is_gzip,
     // zlib checks the whole header, including the optional fields, and the
     // trailer's CRC32 and ISIZE itself
     .engine = codec_zlib_ops,
     .enc = {.wrap = CODEC_WRAP_GZIP, .label = "gzip"},
     .dec = {.wrap = CODEC_WRAP_GZIP,
             .concatenated = 1,
             .ignore_trailing = 1,
             .label = "gzip"},
     .levels = LEVELS_ZLIB},
    {.id = FORMAT_BZIP2,
     .kind = KIND_CONTAINER,
     .name = "bzip2",
     .aliases = {"bz2"},
     .exts = {"bz2"},
     .tar_short = "tbz2",
     .magic = magic_is_bzip2,
     // libbz2 writes and verifies the per-block CRC32s
     .engine = codec_bzip2_ops,
     .dec = {.concatenated = 1, .ignore_trailing = 1},
     .levels = LEVELS_BZIP2},
    {.id = FORMAT_XZ,
     .kind = KIND_CONTAINER,
     .name = "xz",
     .aliases = {"lzma"},
     .exts = {"xz"},
     .tar_short = "txz",
     .magic = magic_is_xz,
     // lzma_easy_encoder writes the whole container, with a CRC64 check
     .engine = codec_lzma_ops,
     .enc = {.label = "xz"},
     .dec = {.concatenated = 1, .label = "xz"},
     .levels = LEVELS_LZMA},
    {.id = FORMAT_ZSTD,
     .kind = KIND_CONTAINER,
     .name = "zstd",
     .aliases = {"zst"},
     .exts = {"zst", "zstd"},
     .tar_short = "tzst",
     .magic = magic_is_zstd,
     .engine = codec_zstd_ops,
     .enc = {.checksum = 1}, // XXH64, verified as each frame ends
     .dec = {.concatenated = 1},
     .levels = LEVELS_ZSTD},
    {.id = FORMAT_LZ4,
     .kind = KIND_CONTAINER,
     .name = "lz4",
     .exts = {"lz4"},
     .tar_short = "tlz4",
     .magic = magic_is_lz4,
     .engine = codec_lz4_ops,
     .enc = {.checksum = 1}, // xxHash32, verified as each frame ends
     .dec = {.concatenated = 1},
     .levels = LEVELS_LZ4},
    {.id = FORMAT_SNAPPY,
     .kind = KIND_CONTAINER,
     .name = "snappy",
     .aliases = {"sz"},
     .exts = {"sz"},
     .magic = magic_is_snappy,
     .engine = codec_snappy_ops, // Masked CRC-32C per chunk
     .levels = LEVELS_NONE},
    {.id = FORMAT_ZIP,
     .kind = KIND_ARCHIVE,
     .name = "zip",
     .exts = {"zip"},
     .magic = magic_is_zip,
     .archive = ARCHIVE_ZIP,
     .backend = &ZIP_ARCHIVE},
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
     .archive = ARCHIVE_TAR,
     .backend = &TAR_ARCHIVE},
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
    char prev_lower[5];
    lowercase_into(prev_lower, sizeof(prev_lower), prev);
    *in_tar = shorthand || (prev > base && strcmp(prev_lower, ".tar") == 0 &&
                            prev[4] == '.');
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

const struct CArchive *find_archive_by_id(ArchiveID id) {
  for (const FormatDesc *d = FORMATS; d->name; d++)
    if (d->kind == KIND_ARCHIVE && d->archive == id)
      return d->backend;
  return NULL;
}
