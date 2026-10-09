#ifndef FORMATS_H
#define FORMATS_H

#include "codec/codec.h"
#include "levels.h"
#include <stddef.h>

struct CArchive; // archives.h

typedef enum {
  ARCHIVE_NONE = 0,
  ARCHIVE_TAR = 1,
  ARCHIVE_ZIP = 2,
  ARCHIVE_7Z = 3
} ArchiveID;

typedef enum {
  FORMAT_UNKNOWN = 0,

  // Single-file formats
  FORMAT_COMPRESSO = 1,
  FORMAT_GZIP = 2,
  FORMAT_BZIP2 = 3,
  FORMAT_XZ = 4,
  FORMAT_ZSTD = 5,
  FORMAT_LZ4 = 6,

  // Multi-file formats with built-in compression
  FORMAT_ZIP = 10,
  FORMAT_7Z = 11,

  // Multi-file formats without built-in compression
  FORMAT_TAR = 20
} Format;

// Engines (codec/*.c) have no file format of their own, so "lzma" is both a
// `.comp` AlgoID and an alias of the `.xz` container
typedef enum {
  KIND_NATIVE,    // `.comp`, wrapping any engine
  KIND_CONTAINER, // One file, one engine: `.gz`, `.zst`, ...
  KIND_ARCHIVE,   // Many entries: tar, which a container may wrap, and zip/7z
} FormatKind;

typedef struct {
  Format id;
  FormatKind kind;
  const char *name;       // Canonical, and the display name
  const char *aliases[2]; // Further names format_by_name accepts
  const char *exts[2];    // Lowercase, without the dot; the first is primary
  const char *tar_short;  // Extension and name for tar wrapped in this, or NULL
  int (*magic)(const unsigned char *m, size_t n);
  // KIND_ARCHIVE only; `backend` is NULL for one recognised but unsupported
  ArchiveID archive;
  const struct CArchive *backend;

  // KIND_CONTAINER only: the engine, and the params it runs with each way;
  // compression takes its level from the caller
  const CodecOps *(*engine)(void);
  CodecParams enc, dec;
  LevelRange levels;
} FormatDesc;

// Ends with a row whose name is NULL; magic detection takes the rows in order
extern const FormatDesc FORMATS[];

// Each returns NULL when nothing matches
const FormatDesc *format_by_id(Format id);
const FormatDesc *format_by_name(const char *name);

// By `path`'s final extension, ignoring case; tar shorthands count. `*in_tar`,
// if given, says if it's a shorthand or follows ".tar", match or not
const FormatDesc *format_by_ext(const char *path, int *in_tar);
const FormatDesc *format_by_magic(const unsigned char *m, size_t n);

// archives/{tar,zip}.c
extern const struct CArchive TAR_ARCHIVE, ZIP_ARCHIVE;

// The backend of `id`'s KIND_ARCHIVE row, or NULL
const struct CArchive *find_archive_by_id(ArchiveID id);

#endif // FORMATS_H
