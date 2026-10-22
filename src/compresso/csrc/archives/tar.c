#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "../archives.h"
#include "../common.h"
#include "archive_ops.h"
#include <archive.h>
#include <archive_entry.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>

// libarchive's own error codes
#ifdef EFTYPE
#define TAR_ERRNO_FILE_FORMAT EFTYPE
#else
#define TAR_ERRNO_FILE_FORMAT EILSEQ
#endif
#define TAR_ERRNO_MISC (-1)

// Raises OSError for a system call's errno, CorruptDataError for a damaged
// archive while reading, and BackendError otherwise; libarchive's readers
// report damage as FILE_FORMAT, MISC or EINVAL
static void set_tar_error(const CompErrors *errors, struct archive *a,
                          const char *what, const char *path, int reading) {
  int code = archive_errno(a);
  const char *detail = archive_error_string(a);
  if (!detail) {
    detail = "unknown error";
  }

  int corrupt = code == TAR_ERRNO_FILE_FORMAT ||
                (reading && (code == TAR_ERRNO_MISC || code == EINVAL));
  if (corrupt) {
    PyErr_Format(errors->corrupt_data, "%s: %s", what, detail);
  } else if (code > 0 && code != EINVAL) {
    errno = code;
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, path);
  } else {
    PyErr_Format(errors->backend, "%s: %s", what, detail);
  }
}

// Entry names are filesystem bytes: the locale charset on POSIX, UTF-8 on
// Windows; libarchive's plain forms match on POSIX, but the ANSI code page on
// Windows, hence the _utf8 forms; not used on POSIX, as they convert through
// the locale anyway, and pax writers hang on a name the locale can't convert
#if defined(_WIN32)
#define TAR_SET_NAME(ae, field, name)                                          \
  archive_entry_update_##field##_utf8(ae, name)
// A name that isn't valid UTF-8 has no UTF-8 form, so falls back to its bytes
#define TAR_NAME(ae, field)                                                    \
  (archive_entry_##field##_utf8(ae) ? archive_entry_##field##_utf8(ae)         \
                                    : archive_entry_##field(ae))
#else
#define TAR_SET_NAME(ae, field, name) archive_entry_set_##field(ae, name)
#define TAR_NAME(ae, field) archive_entry_##field(ae)
#endif

// ---- TAR Writer ----

#define TAR_BLOCK (1024 * 1024)

typedef struct {
  struct archive *archive;
  const char *output_path;
  const CompErrors *errors;
  unsigned char data[CODEC_CHUNK]; // For entry data
} TarWriter;

static TarWriter *tar_writer_new(const char *output_path, CoreContext *ctx) {
  TarWriter *writer = safe_malloc(sizeof(TarWriter));
  if (!writer) {
    return NULL;
  }

  writer->archive = archive_write_new();
  if (!writer->archive) {
    free(writer);
    PyErr_NoMemory();
    return NULL;
  }

  archive_write_set_format_pax_restricted(writer->archive);
  archive_write_set_bytes_per_block(writer->archive, TAR_BLOCK);
  writer->output_path = output_path;
  writer->errors = ctx->errors;
  return writer;
}

static void *tar_writer_opened(TarWriter *writer, int r) {
  if (r != ARCHIVE_OK) {
    set_tar_error(writer->errors, writer->archive, "Failed to open archive",
                  writer->output_path, 0);
    archive_write_free(writer->archive);
    free(writer);
    return NULL;
  }
  return writer;
}

static void *tar_create_writer(const char *output_path, int compression_level,
                               CoreContext *ctx) {
  (void)compression_level; // TAR doesn't have compression

  TarWriter *writer = tar_writer_new(output_path, ctx);
  if (!writer) {
    return NULL;
  }
#ifdef _WIN32
  wchar_t wpath[FS_PATH_MAX];
  if (fs_widen(output_path, wpath, FS_PATH_MAX) != 0) {
    archive_set_error(writer->archive, errno, "Invalid path");
    return tar_writer_opened(writer, ARCHIVE_FATAL);
  }
  return tar_writer_opened(writer,
                           archive_write_open_filename_w(writer->archive, wpath));
#else
  return tar_writer_opened(
      writer, archive_write_open_filename(writer->archive, output_path));
#endif
}

static la_ssize_t tar_codec_write(struct archive *a, void *stream,
                                  const void *buf, size_t n) {
  (void)a;
  // The stream keeps its own failure, for the caller to raise at close
  return codec_write((CodecStream *)stream, buf, n) == 0 ? (la_ssize_t)n : -1;
}

static void *tar_create_codec_writer(CodecStream *stream,
                                     const char *output_path,
                                     CoreContext *ctx) {
  TarWriter *writer = tar_writer_new(output_path, ctx);
  if (!writer) {
    return NULL;
  }

  // As archive_write_open_filename does for a regular file: no padding after
  // the end-of-archive records, so the tar inside matches a plain one
  archive_write_set_bytes_in_last_block(writer->archive, 1);
  return tar_writer_opened(writer,
                           archive_write_open2(writer->archive, stream, NULL,
                                               tar_codec_write, NULL, NULL));
}

static int tar_add_entry(void *writer_ptr, const ArchiveEntry *entry,
                         FILE *data, const char *source_path,
                         CoreContext *ctx) {
  TarWriter *writer = (TarWriter *)writer_ptr;
  struct archive_entry *ae = archive_entry_new();

  if (!ae) {
    PyErr_NoMemory();
    return -1;
  }

  // Set entry metadata
  TAR_SET_NAME(ae, pathname, entry->path);
  archive_entry_set_size(ae, entry->size);
  archive_entry_set_mtime(ae, entry->mtime, 0);
  archive_entry_set_perm(ae, entry->mode);

  // Set entry type
  switch (entry->type) {
  case ENTRY_FILE:
    archive_entry_set_filetype(ae, AE_IFREG);
    break;
  case ENTRY_DIR:
    archive_entry_set_filetype(ae, AE_IFDIR);
    break;
  case ENTRY_SYMLINK:
    archive_entry_set_filetype(ae, AE_IFLNK);
    if (entry->link_target) {
      TAR_SET_NAME(ae, symlink, entry->link_target);
    }
    break;
  case ENTRY_SPECIAL:
    archive_entry_set_filetype(ae, entry->special);
    archive_entry_set_rdev(ae, (dev_t)entry->rdev);
    archive_entry_set_size(ae, 0);
    break;
  case ENTRY_HARDLINK:
    // A link entry carries no data of its own
    archive_entry_set_filetype(ae, AE_IFREG);
    archive_entry_set_size(ae, 0);
    if (entry->link_target) {
      TAR_SET_NAME(ae, hardlink, entry->link_target);
    }
    break;
  }

  // A header that fills libarchive's block flushes it through any codec
  int r;
  Py_BEGIN_ALLOW_THREADS
  r = archive_write_header(writer->archive, ae);
  Py_END_ALLOW_THREADS
  // WARN still writes the header, e.g. a name that can't be converted, stored
  // as is under hdrcharset=BINARY
  if (r == ARCHIVE_WARN) {
    ctx_log(ctx, CTX_LOG_WARNING, "Archive entry %s: %s", entry->path,
            archive_error_string(writer->archive));
  } else if (r != ARCHIVE_OK) {
    set_tar_error(writer->errors, writer->archive, "Failed to write header",
                  writer->output_path, 0);
    archive_entry_free(ae);
    return -1;
  }

  // Write data for files
  if (entry->type == ENTRY_FILE && data) {
    size_t bytes_read;
    uint64_t total = 0;
    int grew = 0;

    int advance = 0;

    Py_BEGIN_ALLOW_THREADS

    while ((bytes_read = fread(writer->data, 1, CODEC_CHUNK, data)) > 0) {
      la_ssize_t bytes_written =
          archive_write_data(writer->archive, writer->data, bytes_read);
      if (bytes_written < 0) {
        Py_BLOCK_THREADS
        set_tar_error(writer->errors, writer->archive, "Failed to write data",
                      writer->output_path, 0);
        archive_entry_free(ae);
        return -1;
      }
      total += (uint64_t)bytes_written;

      advance = ctx_advance(ctx, bytes_read);
      // libarchive drops whatever runs past the header's size
      grew = (size_t)bytes_written < bytes_read;
      if (advance != 0 || grew) {
        break;
      }
    }

    Py_END_ALLOW_THREADS

    if (advance != 0) {
      archive_entry_free(ae);
      return advance;
    }

    if (ferror(data)) {
      // tar streams from the already-open `data`; the path only names it
      PyErr_SetFromErrnoWithFilename(PyExc_OSError, source_path);
      archive_entry_free(ae);
      return -1;
    }

    // Archive is still valid, so warn and continue; libarchive pads a short
    // entry with NULs
    if (grew) {
      ctx_log(ctx, CTX_LOG_WARNING,
              "%s: file grew while being archived; truncated to %llu bytes",
              source_path, (unsigned long long)entry->size);
    } else if (total < entry->size) {
      ctx_log(ctx, CTX_LOG_WARNING,
              "%s: file shrank by %llu bytes; padded with zeros", source_path,
              (unsigned long long)(entry->size - total));
    }
  }

  archive_entry_free(ae);
  return 0;
}

static int tar_close_writer(void *writer_ptr, CoreContext *ctx, int discard) {
  // libarchive streams as it goes, so everything has already been written and
  // reported by the time this runs
  (void)ctx;

  TarWriter *writer = (TarWriter *)writer_ptr;

  if (discard) {
    archive_write_free(writer->archive);
    free(writer);
    return 0;
  }

  // Flushes the last block through any codec
  int r;
  Py_BEGIN_ALLOW_THREADS
  r = archive_write_close(writer->archive);
  Py_END_ALLOW_THREADS
  if (r != ARCHIVE_OK) {
    set_tar_error(writer->errors, writer->archive, "Failed to close archive",
                  writer->output_path, 0);
  }
  archive_write_free(writer->archive);
  free(writer);

  if (r != ARCHIVE_OK) {
    return -1;
  }

  return 0;
}

// ---- TAR Reader ----

typedef struct {
  struct archive *archive;
  struct archive_entry *current_entry;
  const char *input_path;
  const CompErrors *errors;
  CodecStream *stream;             // Not owned; NULL without a codec stage
  unsigned char *block;            // libarchive's read buffer
  unsigned char data[CODEC_CHUNK]; // For entry data
} TarReader;

// Prefers the stream's own failure (e.g. COMP_CANCELLED), raised when it is
// closed, over libarchive's bare -1
static int tar_read_failed(TarReader *reader, const char *what) {
  size_t got;
  int err = reader->stream ? codec_read(reader->stream, NULL, 0, &got) : 0;
  if (err != 0) {
    return err;
  }
  set_tar_error(reader->errors, reader->archive, what, reader->input_path, 1);
  return -1;
}

static TarReader *tar_reader_new(const char *input_path, CoreContext *ctx) {
  TarReader *reader = safe_malloc(sizeof(TarReader));
  if (!reader) {
    return NULL;
  }

  reader->archive = archive_read_new();
  if (!reader->archive) {
    free(reader);
    PyErr_NoMemory();
    return NULL;
  }

  // Support all formats and filters
  archive_read_support_format_tar(reader->archive);
  archive_read_support_filter_all(reader->archive);

  reader->current_entry = NULL;
  reader->input_path = input_path;
  reader->errors = ctx->errors;
  reader->stream = NULL;
  reader->block = NULL;
  return reader;
}

static void tar_reader_free(TarReader *reader) {
  archive_read_free(reader->archive);
  free(reader->block);
  free(reader);
}

static void *tar_reader_opened(TarReader *reader, int r) {
  if (r != ARCHIVE_OK) {
    tar_read_failed(reader, "Failed to open archive");
    tar_reader_free(reader);
    return NULL;
  }
  return reader;
}

static void *tar_create_reader(const char *input_path, CoreContext *ctx) {
  TarReader *reader = tar_reader_new(input_path, ctx);
  if (!reader) {
    return NULL;
  }
#ifdef _WIN32
  wchar_t wpath[FS_PATH_MAX];
  if (fs_widen(input_path, wpath, FS_PATH_MAX) != 0) {
    archive_set_error(reader->archive, errno, "Invalid path");
    return tar_reader_opened(reader, ARCHIVE_FATAL);
  }
  return tar_reader_opened(reader, archive_read_open_filename_w(
                                       reader->archive, wpath, TAR_BLOCK));
#else
  return tar_reader_opened(reader, archive_read_open_filename(
                                       reader->archive, input_path, TAR_BLOCK));
#endif
}

static la_ssize_t tar_codec_read(struct archive *a, void *reader_ptr,
                                 const void **buffer) {
  (void)a;
  TarReader *reader = (TarReader *)reader_ptr;
  size_t got;
  if (codec_read(reader->stream, reader->block, CODEC_CHUNK, &got) != 0) {
    return -1;
  }
  *buffer = reader->block;
  return (la_ssize_t)got;
}

static void *tar_create_codec_reader(CodecStream *stream,
                                     const char *input_path, CoreContext *ctx) {
  TarReader *reader = tar_reader_new(input_path, ctx);
  if (!reader) {
    return NULL;
  }

  reader->stream = stream;
  reader->block = safe_malloc(CODEC_CHUNK);
  if (!reader->block) {
    tar_reader_free(reader);
    return NULL;
  }

  // Decodes the first block, hence no GIL
  int r;
  Py_BEGIN_ALLOW_THREADS
  r = archive_read_open2(reader->archive, reader, NULL, tar_codec_read, NULL,
                         NULL);
  Py_END_ALLOW_THREADS
  return tar_reader_opened(reader, r);
}

static int tar_get_next_entry(void *reader_ptr, ArchiveEntry *entry,
                              CoreContext *ctx) {
  TarReader *reader = (TarReader *)reader_ptr;

  // Zero the caller's out-param, so unset fields are never freed
  memset(entry, 0, sizeof(*entry));

  // Decodes any codec stage, hence no GIL
  int r;
  Py_BEGIN_ALLOW_THREADS
  r = archive_read_next_header(reader->archive, &reader->current_entry);
  Py_END_ALLOW_THREADS

  if (r == ARCHIVE_EOF) {
    return 0; // No more entries
  }

  // WARN still delivers a valid header, e.g. a name libarchive couldn't
  // convert to the locale's charset
  if (r == ARCHIVE_WARN) {
    const char *name = TAR_NAME(reader->current_entry, pathname);
    ctx_log(ctx, CTX_LOG_WARNING, "Archive entry %s: %s", name ? name : "?",
            archive_error_string(reader->archive));
  } else if (r != ARCHIVE_OK) {
    return tar_read_failed(reader, "Error reading archive");
  }

  // Populate ArchiveEntry
  const char *pathname = TAR_NAME(reader->current_entry, pathname);
  if (pathname) {
    entry->path = strdup(pathname);
    if (!entry->path) {
      PyErr_NoMemory();
      return -1;
    }
  }

  entry->size = archive_entry_size(reader->current_entry);
  entry->mtime = archive_entry_mtime(reader->current_entry);
  entry->mode = archive_entry_perm(reader->current_entry);

  // Determine entry type; a hardlink's own filetype varies by tar format, so
  // identified by its target
  const char *hardlink = TAR_NAME(reader->current_entry, hardlink);
  unsigned int filetype =
      (unsigned int)archive_entry_filetype(reader->current_entry);
  if (hardlink) {
    entry->type = ENTRY_HARDLINK;
    entry->link_target = strdup(hardlink);
    if (!entry->link_target) {
      free(entry->path);
      entry->path = NULL;
      PyErr_NoMemory();
      return -1;
    }
  } else if (filetype == AE_IFREG) {
    entry->type = ENTRY_FILE;
  } else if (filetype == AE_IFDIR) {
    entry->type = ENTRY_DIR;
  } else if (filetype == AE_IFLNK) {
    entry->type = ENTRY_SYMLINK;
    const char *link = TAR_NAME(reader->current_entry, symlink);
    if (link) {
      entry->link_target = strdup(link);
      if (!entry->link_target) {
        free(entry->path);
        entry->path = NULL;
        PyErr_NoMemory();
        return -1;
      }
    }
  } else {
    entry->type = ENTRY_SPECIAL;
    // Sockets keep 0, as nothing can recreate them
    if (filetype == AE_IFIFO || filetype == AE_IFCHR || filetype == AE_IFBLK) {
      entry->special = filetype;
      entry->rdev = (uint64_t)archive_entry_rdev(reader->current_entry);
    }
  }

  return 1; // Entry retrieved successfully
}

static int64_t tar_read(void *archive, void *buf, size_t len) {
  return archive_read_data(archive, buf, len);
}

static int tar_extract_entry_data(void *reader_ptr, FILE *output,
                                  uint64_t max_bytes, uint64_t *bytes_written,
                                  CoreContext *ctx) {
  TarReader *reader = (TarReader *)reader_ptr;

  if (!reader->current_entry) {
    PyErr_SetString(PyExc_RuntimeError, "No current entry to extract");
    return -1;
  }

  int rc = copy_entry_data(tar_read, reader->archive, reader->data, output,
                           max_bytes, bytes_written, reader->errors, ctx);
  return rc == 1 ? tar_read_failed(reader, "Error reading archive data") : rc;
}

static int tar_close_reader(void *reader_ptr, int discard) {
  TarReader *reader = (TarReader *)reader_ptr;

  int r = discard ? ARCHIVE_OK : archive_read_close(reader->archive);
  if (r != ARCHIVE_OK) {
    tar_read_failed(reader, "Failed to close archive");
  }
  tar_reader_free(reader);

  return r == ARCHIVE_OK ? 0 : -1;
}

// ---- Backend Definition ----

const CArchive TAR_ARCHIVE = {
    .name = "tar",
    .levels = LEVELS_NONE,
    .stores_specials = 1,
    .stores_hardlinks = 1,
    .streaming = 1,
    .create_writer = tar_create_writer,
    .create_codec_writer = tar_create_codec_writer,
    .add_entry = tar_add_entry,
    .close_writer = tar_close_writer,
    .create_reader = tar_create_reader,
    .create_codec_reader = tar_create_codec_reader,
    .get_next_entry = tar_get_next_entry,
    .extract_entry_data = tar_extract_entry_data,
    .close_reader = tar_close_reader,
};
