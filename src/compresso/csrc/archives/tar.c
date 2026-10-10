#define PY_SSIZE_T_CLEAN
#include "../archives.h"
#include "../common.h"
#include <Python.h>
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
static void set_tar_error(struct archive *a, const char *what, const char *path,
                          int reading) {
  int code = archive_errno(a);
  const char *detail = archive_error_string(a);
  if (!detail) {
    detail = "unknown error";
  }

  int corrupt = code == TAR_ERRNO_FILE_FORMAT ||
                (reading && (code == TAR_ERRNO_MISC || code == EINVAL));
  if (corrupt) {
    PyErr_Format(comp_CorruptDataError, "%s: %s", what, detail);
  } else if (code > 0 && code != EINVAL) {
    errno = code;
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, path);
  } else {
    PyErr_Format(comp_BackendError, "%s: %s", what, detail);
  }
}

// ---- TAR Writer ----

typedef struct {
  struct archive *archive;
  const char *output_path;
} TarWriter;

static TarWriter *tar_writer_new(const char *output_path) {
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
  writer->output_path = output_path;
  return writer;
}

static void *tar_writer_opened(TarWriter *writer, int r) {
  if (r != ARCHIVE_OK) {
    set_tar_error(writer->archive, "Failed to open archive",
                  writer->output_path, 0);
    archive_write_free(writer->archive);
    free(writer);
    return NULL;
  }
  return writer;
}

static void *tar_create_writer(const char *output_path, int compression_level) {
  (void)compression_level; // TAR doesn't have compression

  TarWriter *writer = tar_writer_new(output_path);
  if (!writer) {
    return NULL;
  }
  return tar_writer_opened(
      writer, archive_write_open_filename(writer->archive, output_path));
}

static la_ssize_t tar_codec_write(struct archive *a, void *stream,
                                  const void *buf, size_t n) {
  (void)a;
  // The stream keeps its own failure, for the caller to raise at close
  return codec_write((CodecStream *)stream, buf, n) == 0 ? (la_ssize_t)n : -1;
}

static void *tar_create_codec_writer(CodecStream *stream,
                                     const char *output_path) {
  TarWriter *writer = tar_writer_new(output_path);
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
  archive_entry_set_pathname(ae, entry->path);
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
      archive_entry_set_symlink(ae, entry->link_target);
    }
    break;
  case ENTRY_SPECIAL:
    archive_entry_set_filetype(ae, AE_IFCHR);
    break;
  case ENTRY_HARDLINK:
    // A link entry carries no data of its own
    archive_entry_set_filetype(ae, AE_IFREG);
    archive_entry_set_size(ae, 0);
    if (entry->link_target) {
      archive_entry_set_hardlink(ae, entry->link_target);
    }
    break;
  }

  // Write header
  int r = archive_write_header(writer->archive, ae);
  if (r != ARCHIVE_OK) {
    set_tar_error(writer->archive, "Failed to write header",
                  writer->output_path, 0);
    archive_entry_free(ae);
    return -1;
  }

  // Write data for files
  if (entry->type == ENTRY_FILE && data) {
    char buffer[65536];
    size_t bytes_read;

    int advance = 0;

    Py_BEGIN_ALLOW_THREADS

    while ((bytes_read = fread(buffer, 1, sizeof(buffer), data)) > 0) {
      la_ssize_t bytes_written =
          archive_write_data(writer->archive, buffer, bytes_read);
      if (bytes_written < 0) {
        Py_BLOCK_THREADS
        set_tar_error(writer->archive, "Failed to write data",
                      writer->output_path, 0);
        archive_entry_free(ae);
        return -1;
      }

      advance = ctx_advance(ctx, bytes_read);
      if (advance != 0) {
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

  int r = archive_write_close(writer->archive);
  if (r != ARCHIVE_OK) {
    set_tar_error(writer->archive, "Failed to close archive",
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
} TarReader;

static void *tar_create_reader(const char *input_path) {
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

  int r = archive_read_open_filename(reader->archive, input_path, 10240);
  if (r != ARCHIVE_OK) {
    set_tar_error(reader->archive, "Failed to open archive", input_path, 1);
    archive_read_free(reader->archive);
    free(reader);
    return NULL;
  }

  reader->current_entry = NULL;
  reader->input_path = input_path;
  return reader;
}

static int tar_get_next_entry(void *reader_ptr, ArchiveEntry *entry,
                              CoreContext *ctx) {
  TarReader *reader = (TarReader *)reader_ptr;

  // Zero the caller's out-param, so unset fields are never freed
  memset(entry, 0, sizeof(*entry));

  int r = archive_read_next_header(reader->archive, &reader->current_entry);

  if (r == ARCHIVE_EOF) {
    return 0; // No more entries
  }

  // WARN still delivers a valid header, e.g. a name libarchive couldn't
  // convert to the locale's charset
  if (r == ARCHIVE_WARN) {
    const char *name = archive_entry_pathname(reader->current_entry);
    ctx_log(ctx, CTX_LOG_WARNING, "Archive entry %s: %s", name ? name : "?",
            archive_error_string(reader->archive));
  } else if (r != ARCHIVE_OK) {
    set_tar_error(reader->archive, "Error reading archive", reader->input_path,
                  1);
    return -1; // Error
  }

  // Populate ArchiveEntry
  const char *pathname = archive_entry_pathname(reader->current_entry);
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
  const char *hardlink = archive_entry_hardlink(reader->current_entry);
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
    const char *link = archive_entry_symlink(reader->current_entry);
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
    // FIFOs, device nodes and sockets
    entry->type = ENTRY_SPECIAL;
  }

  return 1; // Entry retrieved successfully
}

static int tar_extract_entry_data(void *reader_ptr, FILE *output,
                                  uint64_t max_bytes, uint64_t *bytes_written,
                                  CoreContext *ctx) {
  TarReader *reader = (TarReader *)reader_ptr;

  if (!reader->current_entry) {
    PyErr_SetString(PyExc_RuntimeError, "No current entry to extract");
    return -1;
  }

  char buffer[65536];
  // la_ssize_t, not ssize_t: see the note in tar_add_entry
  la_ssize_t bytes_read;
  uint64_t total = 0;
  int over_limit = 0;
  int advance = 0;

  Py_BEGIN_ALLOW_THREADS

  while ((bytes_read =
              archive_read_data(reader->archive, buffer, sizeof(buffer))) > 0) {
    // Cap is applied to the bytes produced rather than to the declared size
    if ((uint64_t)bytes_read > max_bytes - total) {
      over_limit = 1;
      break;
    }

    size_t written = fwrite(buffer, 1, bytes_read, output);
    if (written != (size_t)bytes_read || ferror(output)) {
      Py_BLOCK_THREADS
      PyErr_SetFromErrno(PyExc_OSError);
      return -1;
    }
    total += (uint64_t)bytes_read;

    advance = ctx_advance(ctx, (size_t)bytes_read);
    if (advance != 0) {
      break;
    }
  }

  Py_END_ALLOW_THREADS

  if (bytes_written)
    *bytes_written = total;

  if (advance != 0) {
    return advance;
  }

  if (over_limit) {
    PyErr_SetString(comp_ExtractionPolicyError,
                    "Archive exceeds the maximum extracted size");
    return -1;
  }

  if (bytes_read < 0) {
    set_tar_error(reader->archive, "Error reading archive data",
                  reader->input_path, 1);
    return -1;
  }

  return 0;
}

static int tar_skip_entry(void *reader_ptr) {
  (void)reader_ptr; // Unused - libarchive automatically skips entry data
  return 0;
}

static int tar_close_reader(void *reader_ptr, int discard) {
  TarReader *reader = (TarReader *)reader_ptr;

  if (discard) {
    archive_read_free(reader->archive);
    free(reader);
    return 0;
  }

  int r = archive_read_close(reader->archive);
  if (r != ARCHIVE_OK) {
    set_tar_error(reader->archive, "Failed to close archive",
                  reader->input_path, 1);
  }
  archive_read_free(reader->archive);
  free(reader);

  if (r != ARCHIVE_OK) {
    return -1;
  }

  return 0;
}

// ---- Capability Functions ----

static int tar_supports_compression(void) {
  return 0; // TAR itself doesn't have compression
}

static int tar_supports_streaming(void) {
  return 1; // TAR supports streaming
}

// ---- Backend Definition ----

const CArchive TAR_ARCHIVE = {
    .name = "tar",
    .levels = LEVELS_NONE,
    .supports_compression = tar_supports_compression,
    .supports_streaming = tar_supports_streaming,
    .create_writer = tar_create_writer,
    .create_codec_writer = tar_create_codec_writer,
    .add_entry = tar_add_entry,
    .close_writer = tar_close_writer,
    .create_reader = tar_create_reader,
    .get_next_entry = tar_get_next_entry,
    .extract_entry_data = tar_extract_entry_data,
    .skip_entry_data = tar_skip_entry,
    .close_reader = tar_close_reader,
};
