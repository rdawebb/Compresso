#define PY_SSIZE_T_CLEAN
#include "../archives.h"
#include "../common.h"
#include <Python.h>
#include <archive.h>
#include <archive_entry.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>

// ---- TAR Writer ----

typedef struct {
  struct archive *archive;
  const char *output_path;
} TarWriter;

static void *tar_create_writer(const char *output_path, int compression_level) {
  (void)compression_level; // TAR doesn't have compression

  TarWriter *writer = safe_malloc(sizeof(TarWriter));
  if (!writer) {
    return NULL;
  }

  writer->archive = archive_write_new();
  if (!writer->archive) {
    free(writer);
    PyErr_SetString(PyExc_RuntimeError, "Failed to create archive writer");
    return NULL;
  }

  // Use PAX format
  archive_write_set_format_pax_restricted(writer->archive);

  // Open file for writing
  int r = archive_write_open_filename(writer->archive, output_path);
  if (r != ARCHIVE_OK) {
    PyErr_Format(PyExc_IOError, "Failed to open archive: %s",
                 archive_error_string(writer->archive));
    archive_write_free(writer->archive);
    free(writer);
    return NULL;
  }

  writer->output_path = output_path;
  return writer;
}

static int tar_add_entry(void *writer_ptr, const ArchiveEntry *entry,
                         FILE *data) {
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
    if (entry->symlink_target) {
      archive_entry_set_symlink(ae, entry->symlink_target);
    }
    break;
  }

  // Write header
  int r = archive_write_header(writer->archive, ae);
  if (r != ARCHIVE_OK) {
    PyErr_Format(PyExc_IOError, "Failed to write header: %s",
                 archive_error_string(writer->archive));
    archive_entry_free(ae);
    return -1;
  }

  // Write data for files
  if (entry->type == ENTRY_FILE && data) {
    char buffer[65536];
    size_t bytes_read;

    Py_BEGIN_ALLOW_THREADS

        while ((bytes_read = fread(buffer, 1, sizeof(buffer), data)) > 0) {
      ssize_t bytes_written =
          archive_write_data(writer->archive, buffer, bytes_read);
      if (bytes_written < 0) {
        Py_BLOCK_THREADS PyErr_Format(PyExc_IOError, "Failed to write data: %s",
                                      archive_error_string(writer->archive));
        archive_entry_free(ae);
        return -1;
      }
    }

    Py_END_ALLOW_THREADS

        if (ferror(data)) {
      PyErr_SetString(PyExc_IOError, "Error reading input file");
      archive_entry_free(ae);
      return -1;
    }
  }

  archive_entry_free(ae);
  return 0;
}

static int tar_close_writer(void *writer_ptr) {
  TarWriter *writer = (TarWriter *)writer_ptr;

  int r = archive_write_close(writer->archive);
  archive_write_free(writer->archive);
  free(writer);

  if (r != ARCHIVE_OK) {
    PyErr_SetString(PyExc_IOError, "Failed to close archive");
    return -1;
  }

  return 0;
}

// ---- TAR Reader ----

typedef struct {
  struct archive *archive;
  struct archive_entry *current_entry;
} TarReader;

static void *tar_create_reader(const char *input_path) {
  TarReader *reader = safe_malloc(sizeof(TarReader));
  if (!reader) {
    return NULL;
  }

  reader->archive = archive_read_new();
  if (!reader->archive) {
    free(reader);
    PyErr_SetString(PyExc_RuntimeError, "Failed to create archive reader");
    return NULL;
  }

  // Support all formats and filters
  archive_read_support_format_tar(reader->archive);
  archive_read_support_filter_all(reader->archive);

  int r = archive_read_open_filename(reader->archive, input_path, 10240);
  if (r != ARCHIVE_OK) {
    PyErr_Format(PyExc_IOError, "Failed to open archive: %s",
                 archive_error_string(reader->archive));
    archive_read_free(reader->archive);
    free(reader);
    return NULL;
  }

  reader->current_entry = NULL;
  return reader;
}

static int tar_get_entry_count(void *reader_ptr) {
  // libarchive doesn't provide a direct way to get entry count
  return -1;
}

static int tar_get_next_entry(void *reader_ptr, ArchiveEntry *entry) {
  TarReader *reader = (TarReader *)reader_ptr;

  int r = archive_read_next_header(reader->archive, &reader->current_entry);

  if (r == ARCHIVE_EOF) {
    return 0; // No more entries
  }

  if (r != ARCHIVE_OK) {
    PyErr_Format(PyExc_IOError, "Error reading archive: %s",
                 archive_error_string(reader->archive));
    return -1; // Error
  }

  // Populate ArchiveEntry
  const char *pathname = archive_entry_pathname(reader->current_entry);
  if (pathname) {
    entry->path = safe_malloc(strlen(pathname) + 1);
    if (entry->path) {
      strcpy(entry->path, pathname);
    }
  }

  entry->size = archive_entry_size(reader->current_entry);
  entry->mtime = archive_entry_mtime(reader->current_entry);
  entry->mode = archive_entry_perm(reader->current_entry);

  // Determine entry type
  mode_t filetype = archive_entry_filetype(reader->current_entry);
  if (S_ISREG(filetype)) {
    entry->type = ENTRY_FILE;
  } else if (S_ISDIR(filetype)) {
    entry->type = ENTRY_DIR;
  } else if (S_ISLNK(filetype)) {
    entry->type = ENTRY_SYMLINK;
    const char *link = archive_entry_symlink(reader->current_entry);
    if (link) {
      entry->symlink_target = safe_malloc(strlen(link) + 1);
      if (entry->symlink_target) {
        strcpy(entry->symlink_target, link);
      }
    }
  }

  return 1; // Entry retrieved successfully
}

static int tar_extract_entry_data(void *reader_ptr, FILE *output) {
  TarReader *reader = (TarReader *)reader_ptr;

  if (!reader->current_entry) {
    PyErr_SetString(PyExc_RuntimeError, "No current entry to extract");
    return -1;
  }

  char buffer[65536];
  ssize_t bytes_read;

  Py_BEGIN_ALLOW_THREADS

      while ((bytes_read = archive_read_data(reader->archive, buffer,
                                             sizeof(buffer))) > 0) {
    size_t bytes_written = fwrite(buffer, 1, bytes_read, output);
    if (bytes_written != (size_t)bytes_read || ferror(output)) {
      Py_BLOCK_THREADS PyErr_SetString(PyExc_IOError,
                                       "Error writing output file");
      return -1;
    }
  }

  Py_END_ALLOW_THREADS

      if (bytes_read < 0) {
    PyErr_Format(PyExc_IOError, "Error reading archive data: %s",
                 archive_error_string(reader->archive));
    return -1;
  }

  return 0;
}

static int tar_skip_entry(void *reader_ptr) {
  (void)reader_ptr; // Unused - libarchive automatically skips entry data
  return 0;
}

static int tar_reset_reader(void *reader_ptr) {
  // TAR archives don't support seeking/resetting
  PyErr_SetString(PyExc_NotImplementedError, "TAR reader cannot be reset");
  return -1;
}

static int tar_close_reader(void *reader_ptr) {
  TarReader *reader = (TarReader *)reader_ptr;

  int r = archive_read_close(reader->archive);
  archive_read_free(reader->archive);
  free(reader);

  if (r != ARCHIVE_OK) {
    PyErr_SetString(PyExc_IOError, "Failed to close archive");
    return -1;
  }

  return 0;
}

// ---- Capability Functions ----

static int tar_is_available(void) {
  return 1; // TAR is always available if libarchive is compiled
}

static int tar_supports_compression(void) {
  return 0; // TAR itself doesn't have compression
}

static int tar_requires_external_compression(void) {
  return 1; // TAR needs external compression
}

static int tar_supports_streaming(void) {
  return 1; // TAR supports streaming
}

// ---- Backend Definition ----

static const CArchive tar_archive = {
    .name = "tar",
    .id = ARCHIVE_TAR,
    .is_available = tar_is_available,
    .supports_compression = tar_supports_compression,
    .requires_external_compression = tar_requires_external_compression,
    .supports_streaming = tar_supports_streaming,
    .create_writer = tar_create_writer,
    .add_entry = tar_add_entry,
    .close_writer = tar_close_writer,
    .create_reader = tar_create_reader,
    .get_entry_count = tar_get_entry_count,
    .get_next_entry = tar_get_next_entry,
    .extract_entry_data = tar_extract_entry_data,
    .skip_entry_data = tar_skip_entry,
    .reset_reader = tar_reset_reader,
    .close_reader = tar_close_reader,
};

const CArchive *get_tar_archive(void) { return &tar_archive; }
