#define PY_SSIZE_T_CLEAN
#include "archives.h"
#include "fsutil.h"
#include <Python.h>
#include <stdio.h>
#include <string.h>

// ---- Format Detection Functions ----

static Format detect_format_from_stream(FILE *f, const char *path) {
  unsigned char magic[512];
  size_t bytes_read = fread(magic, 1, sizeof(magic), f);
  if (bytes_read < 4) {
    return FORMAT_UNKNOWN;
  }

  const FormatDesc *desc = format_by_magic(magic, bytes_read);
  if (!desc) {
    desc = format_by_ext(path, NULL);
  }
  return desc ? desc->id : FORMAT_UNKNOWN;
}

Format detect_format_from_path(const char *path) {
  if (!path) {
    return FORMAT_UNKNOWN;
  }

  FILE *f = fs_fopen(path, "rb");
  if (!f) {
    return FORMAT_UNKNOWN;
  }
  Format format = detect_format_from_stream(f, path);
  fclose(f);
  return format;
}

FILE *open_source(const char *path, Format *format) {
  FILE *f = fs_fopen(path, "rb");
  if (!f) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, path);
    return NULL;
  }

  // A directory opens fine on POSIX and only fails on read, with EISDIR
  *format = detect_format_from_stream(f, path);
  if (ferror(f)) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, path);
    fclose(f);
    return NULL;
  }
  rewind(f);
  return f;
}

// ---- Compression Pipeline ----

static int is_container(Format format) {
  const FormatDesc *desc = format_by_id(format);
  return desc && desc->kind == KIND_CONTAINER;
}

static const FormatDesc *archive_desc(ArchiveID archive) {
  if (archive == ARCHIVE_NONE)
    return NULL;
  for (const FormatDesc *d = FORMATS; d->name; d++)
    if (d->archive == archive)
      return d;
  return NULL;
}

ArchiveID archive_id_from_format(Format format) {
  const FormatDesc *desc = format_by_id(format);
  return desc ? desc->archive : ARCHIVE_NONE;
}

CompressionPipeline pipeline_from_name(const char *name, int level) {
  CompressionPipeline p;
  p.archive = ARCHIVE_NONE;
  p.codec = FORMAT_UNKNOWN;
  p.compression_level = level;

  if (!name)
    return p;

  // Combined dotted form
  const char *dot = strchr(name, '.');
  if (dot) {
    char base[16];
    size_t blen = (size_t)(dot - name);
    if (blen < sizeof(base)) {
      memcpy(base, name, blen);
      base[blen] = '\0';
      const FormatDesc *arch = format_by_name(base);
      const FormatDesc *codec = format_by_name(dot + 1);
      if (arch && arch->archive != ARCHIVE_NONE && codec &&
          codec->kind == KIND_CONTAINER) {
        p.archive = arch->archive;
        p.codec = codec->id;
        return p;
      }
    }
  } else {
    for (const FormatDesc *d = FORMATS; d->name; d++) {
      if (d->tar_short && strcmp(d->tar_short, name) == 0) {
        p.archive = ARCHIVE_TAR;
        p.codec = d->id;
        return p;
      }
    }
  }

  // Single format: an archive container, or a standalone codec / compresso
  const FormatDesc *desc = format_by_name(name);
  if (desc && desc->archive != ARCHIVE_NONE)
    p.archive = desc->archive;
  else if (desc)
    p.codec = desc->id;
  return p;
}

CompressionPipeline pipeline_from_format(Format f, const char *path) {
  CompressionPipeline p;
  p.archive = ARCHIVE_NONE;
  p.codec = FORMAT_UNKNOWN;
  p.compression_level = -1;

  if (f == FORMAT_UNKNOWN)
    return p;

  ArchiveID arch = archive_id_from_format(f);
  if (arch != ARCHIVE_NONE) {
    // A plain archive container
    p.archive = arch;
    return p;
  }

  // Standalone codec, compresso, or unknown
  p.codec = f;
  int in_tar;
  format_by_ext(path, &in_tar);
  if (is_container(f) && in_tar)
    p.archive = ARCHIVE_TAR;
  return p;
}

void pipeline_display_name(const CompressionPipeline *p, char *buf,
                           size_t buflen) {
  if (buflen == 0)
    return;
  if (!p) {
    snprintf(buf, buflen, "unknown");
    return;
  }

  const FormatDesc *arch = archive_desc(p->archive);
  const FormatDesc *codec = format_by_id(p->codec);

  if (arch && codec && codec->kind == KIND_CONTAINER)
    snprintf(buf, buflen, "%s.%s", arch->name, codec->exts[0]); // e.g. tar.gz
  else if (arch)
    snprintf(buf, buflen, "%s", arch->name); // e.g. tar, zip
  else if (codec)
    snprintf(buf, buflen, "%s", codec->name); // e.g. gzip
  else
    snprintf(buf, buflen, "unknown");
}

int pipeline_is_valid(const CompressionPipeline *p) {
  if (!p)
    return 0;

  // The pipeline must do something
  if (p->archive == ARCHIVE_NONE && p->codec == FORMAT_UNKNOWN)
    return 0;

  // ZIP has built-in compression, external codec stage is not allowed
  if (p->archive == ARCHIVE_ZIP && p->codec != FORMAT_UNKNOWN)
    return 0;

  // A codec, when present, must resolve to a real standalone format
  if (p->codec != FORMAT_UNKNOWN && !is_container(p->codec))
    return 0;

  return 1;
}
