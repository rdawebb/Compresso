#define PY_SSIZE_T_CLEAN
#include "archives.h"
#include "fsutil.h"
#include "magics.h"
#include "standalone.h"
#include <Python.h>
#include <ctype.h>
#include <stdio.h>
#include <string.h>

// ---- Format Detection Functions ----

Format detect_format_from_magic_bytes(const unsigned char *magic, size_t size) {
  if (!magic || size < 2) {
    return FORMAT_UNKNOWN;
  }

  // Gzip and bzip2 need only 2 bytes
  if (magic_is_gzip(magic, size)) {
    return FORMAT_GZIP;
  }

  if (magic_is_bzip2(magic, size)) {
    return FORMAT_BZIP2;
  }

  if (size < 4) {
    return FORMAT_UNKNOWN;
  }

  if (magic_is_compresso(magic, size)) {
    return FORMAT_COMPRESSO;
  }

  if (magic_is_xz(magic, size)) {
    return FORMAT_XZ;
  }

  if (magic_is_zstd(magic, size)) {
    return FORMAT_ZSTD;
  }

  if (magic_is_lz4(magic, size)) {
    return FORMAT_LZ4;
  }

  if (magic_is_zip(magic, size)) {
    return FORMAT_ZIP;
  }

  if (magic_is_7z(magic, size)) {
    return FORMAT_7Z;
  }

  return FORMAT_UNKNOWN;
}

Format detect_format_from_path(const char *path) {
  if (!path) {
    return FORMAT_UNKNOWN;
  }

  FILE *f = fs_fopen(path, "rb");
  if (!f) {
    return FORMAT_UNKNOWN;
  }

  // Magic bytes detection
  unsigned char magic[512];
  size_t bytes_read = fread(magic, 1, sizeof(magic), f);
  if (bytes_read < 4) {
    fclose(f);
    return FORMAT_UNKNOWN;
  }

  Format format = detect_format_from_magic_bytes(magic, bytes_read);
  if (format != FORMAT_UNKNOWN) {
    fclose(f);
    // Single/base format
    return format;
  }
  // Check for TAR format
  if (bytes_read >= TAR_MAGIC_OFFSET + 5) {
    if (memcmp(magic + TAR_MAGIC_OFFSET, TAR_MAGIC, 5) == 0) {
      fclose(f);
      return FORMAT_TAR;
    }
  }
  fclose(f);

  // If magic bytes detection fails, try extension-based detection
  return detect_format_from_extension(path);
}

Format detect_format_from_extension(const char *path) {
  if (!path) {
    return FORMAT_UNKNOWN;
  }

  const char *ext = strrchr(path, '.');
  if (!ext) {
    return FORMAT_UNKNOWN;
  }
  ext++; // Skip the dot

  // Convert to lowercase
  char lower_ext[32];
  size_t i;
  for (i = 0; i < sizeof(lower_ext) - 1 && ext[i]; i++) {
    lower_ext[i] = tolower(ext[i]);
  }
  lower_ext[i] = '\0';

  // Single-file formats
  if (strcmp(lower_ext, "gz") == 0)
    return FORMAT_GZIP;
  if (strcmp(lower_ext, "bz2") == 0)
    return FORMAT_BZIP2;
  if (strcmp(lower_ext, "xz") == 0)
    return FORMAT_XZ;
  if (strcmp(lower_ext, "zst") == 0 || strcmp(lower_ext, "zstd") == 0)
    return FORMAT_ZSTD;
  if (strcmp(lower_ext, "lz4") == 0)
    return FORMAT_LZ4;
  if (strcmp(lower_ext, "comp") == 0)
    return FORMAT_COMPRESSO;

  // Archive formats
  if (strcmp(lower_ext, "zip") == 0)
    return FORMAT_ZIP;
  if (strcmp(lower_ext, "7z") == 0)
    return FORMAT_7Z;
  if (strcmp(lower_ext, "tar") == 0)
    return FORMAT_TAR;

  // Combined-archive shorthands map to their codec
  if (strcmp(lower_ext, "tgz") == 0)
    return FORMAT_GZIP;
  if (strcmp(lower_ext, "tbz2") == 0)
    return FORMAT_BZIP2;
  if (strcmp(lower_ext, "txz") == 0)
    return FORMAT_XZ;
  if (strcmp(lower_ext, "tzst") == 0)
    return FORMAT_ZSTD;
  if (strcmp(lower_ext, "tlz4") == 0)
    return FORMAT_LZ4;

  return FORMAT_UNKNOWN;
}

// ---- Format Information ----

int format_is_archive(Format format) {
  switch (format) {
  case FORMAT_ZIP:
  case FORMAT_7Z:
  case FORMAT_TAR:
    return 1;
  default:
    return 0;
  }
}

// ---- Compression Pipeline ----

ArchiveID archive_id_from_format(Format format) {
  switch (format) {
  case FORMAT_TAR:
    return ARCHIVE_TAR;
  case FORMAT_ZIP:
    return ARCHIVE_ZIP;
  case FORMAT_7Z:
    return ARCHIVE_7Z;
  default:
    return ARCHIVE_NONE;
  }
}

// The extension token for a standalone codec Format, or NULL
static const char *codec_extension(Format codec) {
  switch (codec) {
  case FORMAT_GZIP:
    return "gz";
  case FORMAT_BZIP2:
    return "bz2";
  case FORMAT_XZ:
    return "xz";
  case FORMAT_ZSTD:
    return "zst";
  case FORMAT_LZ4:
    return "lz4";
  default:
    return NULL;
  }
}

// The name token for an archive container, or NULL
static const char *archive_token(ArchiveID archive) {
  switch (archive) {
  case ARCHIVE_TAR:
    return "tar";
  case ARCHIVE_ZIP:
    return "zip";
  case ARCHIVE_7Z:
    return "7z";
  default:
    return NULL;
  }
}

// Return ARCHIVE_TAR if the path's extension indicates a tar container wrapping
// a codec, otherwise ARCHIVE_NONE
static ArchiveID tar_archive_from_extension(const char *path) {
  const char *ext = strrchr(path, '.');
  if (!ext)
    return ARCHIVE_NONE;

  char last[16];
  size_t i;
  for (i = 0; i < sizeof(last) - 1 && ext[i + 1]; i++)
    last[i] = tolower(ext[i + 1]);
  last[i] = '\0';

  // Combined shorthands
  if (strcmp(last, "tgz") == 0 || strcmp(last, "tbz2") == 0 ||
      strcmp(last, "txz") == 0 || strcmp(last, "tzst") == 0 ||
      strcmp(last, "tlz4") == 0)
    return ARCHIVE_TAR;

  // Dotted form: the component before the final extension is "tar"
  const char *prev = ext - 1;
  while (prev > path && *prev != '.')
    prev--;
  if (prev > path && strncmp(prev, ".tar", 4) == 0 &&
      (prev[4] == '.' || prev[4] == '\0'))
    return ARCHIVE_TAR;

  return ARCHIVE_NONE;
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
      ArchiveID arch = archive_id_from_format(format_from_name(base));
      Format codec = format_from_name(dot + 1);
      if (arch != ARCHIVE_NONE && find_standalone_format(codec) != NULL) {
        p.archive = arch;
        p.codec = codec;
        return p;
      }
    }
  } else {
    // Combined shorthands
    if (strcmp(name, "tgz") == 0) {
      p.archive = ARCHIVE_TAR;
      p.codec = FORMAT_GZIP;
      return p;
    }
    if (strcmp(name, "tbz2") == 0) {
      p.archive = ARCHIVE_TAR;
      p.codec = FORMAT_BZIP2;
      return p;
    }
    if (strcmp(name, "txz") == 0) {
      p.archive = ARCHIVE_TAR;
      p.codec = FORMAT_XZ;
      return p;
    }
    if (strcmp(name, "tzst") == 0) {
      p.archive = ARCHIVE_TAR;
      p.codec = FORMAT_ZSTD;
      return p;
    }
    if (strcmp(name, "tlz4") == 0) {
      p.archive = ARCHIVE_TAR;
      p.codec = FORMAT_LZ4;
      return p;
    }
  }

  // Single format: an archive container, or a standalone codec / compresso
  Format f = format_from_name(name);
  ArchiveID arch = archive_id_from_format(f);
  if (arch != ARCHIVE_NONE)
    p.archive = arch;
  else
    p.codec = f;
  return p;
}

CompressionPipeline detect_pipeline_from_path(const char *path) {
  CompressionPipeline p;
  p.archive = ARCHIVE_NONE;
  p.codec = FORMAT_UNKNOWN;
  p.compression_level = -1;

  if (!path)
    return p;

  Format f = detect_format_from_path(path);
  ArchiveID arch = archive_id_from_format(f);
  if (arch != ARCHIVE_NONE) {
    // A plain archive container
    p.archive = arch;
    return p;
  }

  // Standalone codec, compresso, or unknown
  p.codec = f;
  if (find_standalone_format(f) != NULL &&
      tar_archive_from_extension(path) == ARCHIVE_TAR)
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

  const char *arch = archive_token(p->archive);
  const char *cext = codec_extension(p->codec);

  if (arch && cext)
    snprintf(buf, buflen, "%s.%s", arch, cext); // e.g. tar.gz
  else if (arch)
    snprintf(buf, buflen, "%s", arch); // e.g. tar, zip
  else if (p->codec != FORMAT_UNKNOWN)
    snprintf(buf, buflen, "%s", format_name_string(p->codec)); // e.g. gzip
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
  if (p->codec != FORMAT_UNKNOWN && find_standalone_format(p->codec) == NULL)
    return 0;

  return 1;
}

const char *format_name_string(Format format) {
  switch (format) {
  case FORMAT_COMPRESSO:
    return "compresso";
  case FORMAT_GZIP:
    return "gzip";
  case FORMAT_BZIP2:
    return "bzip2";
  case FORMAT_XZ:
    return "xz";
  case FORMAT_ZSTD:
    return "zstd";
  case FORMAT_LZ4:
    return "lz4";
  case FORMAT_ZIP:
    return "zip";
  case FORMAT_7Z:
    return "7z";
  case FORMAT_TAR:
    return "tar";
  default:
    return "unknown";
  }
}

Format format_from_name(const char *name) {
  if (!name)
    return FORMAT_UNKNOWN;

  if (strcmp(name, "compresso") == 0)
    return FORMAT_COMPRESSO;
  if (strcmp(name, "gzip") == 0 || strcmp(name, "gz") == 0)
    return FORMAT_GZIP;
  if (strcmp(name, "bzip2") == 0 || strcmp(name, "bz2") == 0)
    return FORMAT_BZIP2;
  if (strcmp(name, "xz") == 0 || strcmp(name, "lzma") == 0)
    return FORMAT_XZ;
  if (strcmp(name, "zstd") == 0 || strcmp(name, "zst") == 0)
    return FORMAT_ZSTD;
  if (strcmp(name, "lz4") == 0)
    return FORMAT_LZ4;
  if (strcmp(name, "zip") == 0)
    return FORMAT_ZIP;
  if (strcmp(name, "7z") == 0)
    return FORMAT_7Z;
  if (strcmp(name, "tar") == 0)
    return FORMAT_TAR;

  return FORMAT_UNKNOWN;
}

// ---- Operation Mode ----

OperationMode get_operation_mode(Format format) {
  if (format_is_archive(format)) {
    return MODE_ARCHIVE;
  } else {
    return MODE_SINGLE_FILE;
  }
}
