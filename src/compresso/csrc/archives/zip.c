#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#define PY_SSIZE_T_CLEAN
#include "../archives.h"
#include "../common.h"
#include <Python.h>
#include <sys/stat.h>
#include <time.h>
#include <zip.h>

#ifndef ZIP_LENGTH_TO_END
#define ZIP_LENGTH_TO_END 0
#endif

// ---- ZIP Writer ----

// Below libzip 1.6, progress callbacks are not available
#if defined(LIBZIP_VERSION_MAJOR) &&                                           \
    (LIBZIP_VERSION_MAJOR > 1 ||                                               \
     (LIBZIP_VERSION_MAJOR == 1 && LIBZIP_VERSION_MINOR >= 6))
#define ZIP_HAS_PROGRESS_CALLBACKS 1
#endif

typedef struct {
  zip_t *archive;
  const char *output_path;
  int compression_level;

  // Only set for the duration of zip_close
  CoreContext *ctx;
  int abort_code; // Non-zero once the context asked to stop
} ZipWriter;

static void *zip_create_writer(const char *output_path, int compression_level) {
  int err;
  zip_t *za = zip_open(output_path, ZIP_CREATE | ZIP_TRUNCATE, &err);
  if (!za) {
    zip_error_t error;
    zip_error_init_with_code(&error, err);
    PyErr_Format(PyExc_IOError, "Failed to create ZIP archive: %s",
                 zip_error_strerror(&error));
    zip_error_fini(&error);
    return NULL;
  }

  ZipWriter *writer = safe_malloc(sizeof(ZipWriter));
  if (!writer) {
    zip_close(za);
    return NULL;
  }

  writer->archive = za;
  writer->ctx = NULL;
  writer->abort_code = 0;
  writer->output_path = output_path;
  writer->compression_level = (compression_level >= 0 && compression_level <= 9)
                                  ? compression_level
                                  : 6; // Default

  return writer;
}

static int zip_add_entry(void *writer_ptr, const ArchiveEntry *entry,
                         FILE *data, const char *source_path,
                         CoreContext *ctx) {
  ZipWriter *writer = (ZipWriter *)writer_ptr;

  int cancelled = ctx_advance(ctx, 0);
  if (cancelled != 0) {
    // Remembered so the close discards the buffered sources
    writer->abort_code = cancelled;
    return cancelled;
  }

  if (entry->type == ENTRY_DIR) {
    // ZIP requires directories to end with '/
    size_t dir_path_len = strlen(entry->path) + 2;
    char *dir_path = safe_malloc(dir_path_len);
    if (!dir_path)
      return -1;
    snprintf(dir_path, dir_path_len, "%s/", entry->path);

    zip_int64_t idx = zip_dir_add(writer->archive, dir_path, ZIP_FL_ENC_UTF_8);
    free(dir_path);
    if (idx < 0) {
      PyErr_Format(PyExc_IOError, "Failed to add directory: %s",
                   zip_strerror(writer->archive));
      return -1;
    }

    return 0;
  }

  if (entry->type == ENTRY_FILE) {
    // Create source from FILE*
    if (!data) {
      PyErr_SetString(PyExc_ValueError, "FILE data required for file entry");
      return -1;
    }

    // libzip opens, reads and closes the file itself, lazily, usually from
    // zip_close - so neither the whole file nor an open fd is held here
    zip_source_t *source =
        zip_source_file(writer->archive, source_path, 0, ZIP_LENGTH_TO_END);
    if (!source) {
      PyErr_Format(PyExc_IOError, "Failed to create ZIP source: %s",
                   zip_strerror(writer->archive));
      return -1;
    }

    // Add file to archive
    zip_int64_t idx = zip_file_add(writer->archive, entry->path, source,
                                   ZIP_FL_ENC_UTF_8 | ZIP_FL_OVERWRITE);
    if (idx < 0) {
      zip_source_free(source);
      PyErr_Format(PyExc_IOError, "Failed to add file: %s",
                   zip_strerror(writer->archive));
      return -1;
    }

    // Set compression method and level
    if (zip_set_file_compression(writer->archive, idx, ZIP_CM_DEFLATE,
                                 writer->compression_level) < 0) {
      PyErr_Format(PyExc_IOError, "Failed to set compression: %s",
                   zip_strerror(writer->archive));
      return -1;
    }

    // Set modification time
    if (entry->mtime > 0) {
      zip_file_set_mtime(writer->archive, (zip_uint64_t)idx, entry->mtime, 0);
    }

    // No progress is reported here; libzip buffers each source and compresses
    // everything inside zip_close
    return 0;
  }

  if (entry->type == ENTRY_SYMLINK) {
    // Store as a regular file containing the symlink target
    if (!entry->symlink_target) {
      PyErr_SetString(PyExc_ValueError, "Symlink requires symlink_target");
      return -1;
    }

    size_t target_len = strlen(entry->symlink_target);
    zip_source_t *source = zip_source_buffer(
        writer->archive, strdup(entry->symlink_target), target_len, 1);
    if (!source) {
      PyErr_Format(PyExc_IOError, "Failed to create symlink source: %s",
                   zip_strerror(writer->archive));
      return -1;
    }

    zip_int64_t idx =
        zip_file_add(writer->archive, entry->path, source, ZIP_FL_ENC_UTF_8);
    if (idx < 0) {
      zip_source_free(source);
      PyErr_Format(PyExc_IOError, "Failed to add symlink: %s",
                   zip_strerror(writer->archive));
      return -1;
    }

    return 0;
  }

  PyErr_SetString(PyExc_ValueError, "Unknown entry type");
  return -1;
}

#ifdef ZIP_HAS_PROGRESS_CALLBACKS

static void zip_on_progress(zip_t *za, double fraction, void *userdata) {
  (void)za;
  ZipWriter *writer = (ZipWriter *)userdata;
  if (!writer->ctx || writer->abort_code != 0)
    return;

  if (fraction < 0.0)
    fraction = 0.0;
  if (fraction > 1.0)
    fraction = 1.0;

  uint64_t position = (uint64_t)(fraction * (double)writer->ctx->total_bytes);
  int rc = ctx_set_position(writer->ctx, position);
  if (rc != 0) {
    // Recorded rather than acted on: libzip ignores this callback's return
    writer->abort_code = rc;
  }
}

static int zip_on_cancel(zip_t *za, void *userdata) {
  (void)za;
  return ((ZipWriter *)userdata)->abort_code != 0;
}

#endif

static int zip_close_writer(void *writer_ptr, CoreContext *ctx) {
  ZipWriter *writer = (ZipWriter *)writer_ptr;

  writer->ctx = ctx;

#ifdef ZIP_HAS_PROGRESS_CALLBACKS
  if (ctx) {
    // 0.01 so libzip reports each 1% of the write
    zip_register_progress_callback_with_state(writer->archive, 0.01,
                                              zip_on_progress, NULL, writer);
    zip_register_cancel_callback_with_state(writer->archive, zip_on_cancel,
                                            NULL, writer);
  }
#endif

  // Discard buffered sources if cancelled
  if (writer->abort_code != 0) {
    int abort_code = writer->abort_code;
    zip_discard(writer->archive);
    free(writer);
    return abort_code;
  }

  int ret = zip_close(writer->archive);
  int abort_code = writer->abort_code;
  free(writer);

  if (abort_code != 0) {
    // Cancelled from inside zip_close, which surfaces as a close failure
    return abort_code;
  }

  if (ret < 0) {
    PyErr_SetString(PyExc_IOError, "Failed to close ZIP archive");
    return -1;
  }

  return 0;
}

// ---- ZIP Reader ----

typedef struct {
  zip_t *archive;
  zip_int64_t num_entries;
  zip_int64_t current_index;
  zip_file_t *current_file;
} ZipReader;

static void *zip_create_reader(const char *input_path) {
  int err;
  zip_t *za = zip_open(input_path, ZIP_RDONLY, &err);
  if (!za) {
    zip_error_t error;
    zip_error_init_with_code(&error, err);
    PyErr_Format(PyExc_IOError, "Failed to open ZIP archive: %s",
                 zip_error_strerror(&error));
    zip_error_fini(&error);
    return NULL;
  }

  ZipReader *reader = safe_malloc(sizeof(ZipReader));
  if (!reader) {
    zip_close(za);
    return NULL;
  }

  reader->archive = za;
  reader->num_entries = zip_get_num_entries(za, 0);
  reader->current_index = 0;
  reader->current_file = NULL;

  return reader;
}

static int zip_get_entry_count(void *reader_ptr) {
  ZipReader *reader = (ZipReader *)reader_ptr;
  return (int)reader->num_entries;
}

static int zip_get_next_entry(void *reader_ptr, ArchiveEntry *entry) {
  ZipReader *reader = (ZipReader *)reader_ptr;

  if (reader->current_index >= reader->num_entries) {
    return 0; // No more entries
  }

  struct zip_stat st;
  zip_stat_init(&st);

  if (zip_stat_index(reader->archive, reader->current_index, ZIP_FL_ENC_GUESS,
                     &st) < 0) {
    PyErr_Format(PyExc_IOError, "Failed to stat entry: %s",
                 zip_strerror(reader->archive));
    return -1;
  }

  // Set entry metadata
  entry->path = NULL;
  entry->symlink_target = NULL;

  if (st.valid & ZIP_STAT_NAME) {
    entry->path = strdup(st.name);

    // Check if directory (ends with '/')
    size_t name_len = strlen(st.name);
    if (name_len > 0 && st.name[name_len - 1] == '/') {
      entry->type = ENTRY_DIR;
      entry->size = 0;
    } else {
      entry->type = ENTRY_FILE;
      entry->size = (st.valid & ZIP_STAT_SIZE) ? st.size : 0;
    }
  }

  if (st.valid & ZIP_STAT_MTIME) {
    entry->mtime = st.mtime;
  }

  // ZIP doesn't store Unix permissions by default
  entry->mode = (entry->type == ENTRY_DIR) ? 0755 : 0644;

  // A directory has no data and so nothing worth reporting
  if (entry->type != ENTRY_DIR) {
    entry->has_compression_detail = 1;
    entry->compressed_size =
        (st.valid & ZIP_STAT_COMP_SIZE) ? (uint64_t)st.comp_size : 0;
    entry->crc = (st.valid & ZIP_STAT_CRC) ? (uint32_t)st.crc : 0;
    entry->method =
        (st.valid & ZIP_STAT_COMP_METHOD) ? (uint16_t)st.comp_method : 0;
  }

  reader->current_index++;

  return 1; // Entry retrieved
}

static int zip_extract_entry_data(void *reader_ptr, FILE *output,
                                  uint64_t max_bytes, uint64_t *bytes_written,
                                  CoreContext *ctx) {
  ZipReader *reader = (ZipReader *)reader_ptr;

  // Open the file at current_index - 1 (already incremented)
  zip_int64_t idx = reader->current_index - 1;

  zip_file_t *zf = zip_fopen_index(reader->archive, idx, 0);
  if (!zf) {
    PyErr_Format(PyExc_IOError, "Failed to open file in archive: %s",
                 zip_strerror(reader->archive));
    return -1;
  }

  char buffer[65536];
  zip_int64_t bytes_read;
  uint64_t total = 0;
  int over_limit = 0;
  int advance = 0;

  Py_BEGIN_ALLOW_THREADS

      while ((bytes_read = zip_fread(zf, buffer, sizeof(buffer))) > 0) {
    // Cap is applied to the bytes produced rather than to the declared size
    if ((uint64_t)bytes_read > max_bytes - total) {
      over_limit = 1;
      break;
    }

    size_t written = fwrite(buffer, 1, bytes_read, output);
    if (written != (size_t)bytes_read || ferror(output)) {
      Py_BLOCK_THREADS zip_fclose(zf);
      PyErr_SetString(PyExc_IOError, "Error writing output");
      return -1;
    }
    total += (uint64_t)bytes_read;

    advance = ctx_advance(ctx, (size_t)bytes_read);
    if (advance != 0) {
      break;
    }
  }

  Py_END_ALLOW_THREADS

      if (bytes_written) *bytes_written = total;

  if (advance != 0) {
    zip_fclose(zf);
    return advance;
  }

  if (over_limit) {
    zip_fclose(zf);
    PyErr_SetString(PyExc_ValueError,
                    "Archive exceeds the maximum extracted size");
    return -1;
  }

  if (bytes_read < 0) {
    PyErr_Format(PyExc_IOError, "Error reading from archive: %s",
                 zip_file_strerror(zf));
    zip_fclose(zf);
    return -1;
  }

  zip_fclose(zf);
  return 0;
}

static int zip_skip_entry(void *reader_ptr) {
  // ZIP reader moves to next entry by default
  return 0;
}

static int zip_reset_reader(void *reader_ptr) {
  ZipReader *reader = (ZipReader *)reader_ptr;
  reader->current_index = 0;
  return 0;
}

static int zip_close_reader(void *reader_ptr) {
  ZipReader *reader = (ZipReader *)reader_ptr;

  int ret = zip_close(reader->archive);
  free(reader);

  if (ret < 0) {
    PyErr_SetString(PyExc_IOError, "Failed to close ZIP archive");
    return -1;
  }

  return 0;
}

// ---- Capability Functions ----

static int zip_is_available(void) { return 1; }

static int zip_supports_compression(void) {
  return 1; // ZIP has built-in DEFLATE compression
}

static int zip_requires_external_compression(void) { return 0; }

static int zip_supports_streaming(void) {
  return 0; // libzip requires seekable files
}

// ---- Backend Definition ----

static const CArchive zip_archive = {
    .name = "zip",
    .id = ARCHIVE_ZIP,
    .is_available = zip_is_available,
    .supports_compression = zip_supports_compression,
    .requires_external_compression = zip_requires_external_compression,
    .supports_streaming = zip_supports_streaming,
    .create_writer = zip_create_writer,
    .add_entry = zip_add_entry,
    .close_writer = zip_close_writer,
    .create_reader = zip_create_reader,
    .get_entry_count = zip_get_entry_count,
    .get_next_entry = zip_get_next_entry,
    .extract_entry_data = zip_extract_entry_data,
    .skip_entry_data = zip_skip_entry,
    .reset_reader = zip_reset_reader,
    .close_reader = zip_close_reader,
};

const CArchive *get_zip_archive(void) { return &zip_archive; }
