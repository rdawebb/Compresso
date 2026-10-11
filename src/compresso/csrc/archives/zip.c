#define PY_SSIZE_T_CLEAN
#include "../archives.h"
#include "../common.h"
#include <Python.h>
#include <errno.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <zip.h>

#ifndef ZIP_LENGTH_TO_END
#define ZIP_LENGTH_TO_END 0
#endif

// A damaged archive, as opposed to one using a feature libzip lacks
static int zip_error_is_corrupt(int code) {
  switch (code) {
  case ZIP_ER_CRC:
  case ZIP_ER_ZLIB:
  case ZIP_ER_EOF:
  case ZIP_ER_NOZIP:
  case ZIP_ER_INCONS:
#ifdef ZIP_ER_COMPRESSED_DATA
  case ZIP_ER_COMPRESSED_DATA:
#endif
#ifdef ZIP_ER_DATA_LENGTH
  case ZIP_ER_DATA_LENGTH:
#endif
#ifdef ZIP_ER_TRUNCATED_ZIP
  case ZIP_ER_TRUNCATED_ZIP:
#endif
    return 1;
  default:
    return 0;
  }
}

// Raises OSError for a system call's errno, CorruptDataError for a damaged
// archive while reading, and BackendError otherwise; `saved_errno` stands in
// when libzip didn't keep one
static void set_zip_error(zip_error_t *error, const char *what,
                          const char *path, int reading, int saved_errno) {
  int code = zip_error_code_zip(error);

  int sys = 0;
  if (zip_error_system_type(error) == ZIP_ET_SYS) {
    sys = zip_error_code_system(error);
    if (sys == 0) {
      sys = saved_errno ? saved_errno : EIO;
    }
  } else if (code == ZIP_ER_NOENT) {
    sys = ENOENT;
  } else if (code == ZIP_ER_EXISTS) {
    sys = EEXIST;
  }

  if (sys) {
    errno = sys;
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, path);
  } else if (code == ZIP_ER_MEMORY) {
    PyErr_NoMemory();
  } else if (reading && zip_error_is_corrupt(code)) {
    PyErr_Format(comp_CorruptDataError, "%s: %s", what,
                 zip_error_strerror(error));
  } else {
    PyErr_Format(comp_BackendError, "%s: %s", what, zip_error_strerror(error));
  }
}

// zip_open reports only a libzip code; errno still holds the cause
static void set_zip_open_error(int err, const char *what, const char *path,
                               int reading) {
  int saved_errno = errno;
  zip_error_t error;
  zip_error_init_with_code(&error, err);
  set_zip_error(&error, what, path, reading, saved_errno);
  zip_error_fini(&error);
}

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
    set_zip_open_error(err, "Failed to create ZIP archive", output_path, 0);
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
      set_zip_error(zip_get_error(writer->archive), "Failed to add directory",
                    writer->output_path, 0, 0);
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
      set_zip_error(zip_get_error(writer->archive), "Failed to read source",
                    source_path, 0, 0);
      return -1;
    }

    // Add file to archive
    zip_int64_t idx = zip_file_add(writer->archive, entry->path, source,
                                   ZIP_FL_ENC_UTF_8 | ZIP_FL_OVERWRITE);
    if (idx < 0) {
      zip_source_free(source);
      set_zip_error(zip_get_error(writer->archive), "Failed to add file",
                    writer->output_path, 0, 0);
      return -1;
    }

    // Set compression method and level
    if (zip_set_file_compression(writer->archive, idx, ZIP_CM_DEFLATE,
                                 writer->compression_level) < 0) {
      set_zip_error(zip_get_error(writer->archive), "Failed to set compression",
                    writer->output_path, 0, 0);
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
    if (!entry->link_target) {
      PyErr_SetString(PyExc_ValueError, "Symlink requires a target");
      return -1;
    }

    size_t target_len = strlen(entry->link_target);
    zip_source_t *source = zip_source_buffer(
        writer->archive, strdup(entry->link_target), target_len, 1);
    if (!source) {
      set_zip_error(zip_get_error(writer->archive),
                    "Failed to create symlink source", writer->output_path, 0,
                    0);
      return -1;
    }

    zip_int64_t idx =
        zip_file_add(writer->archive, entry->path, source, ZIP_FL_ENC_UTF_8);
    if (idx < 0) {
      zip_source_free(source);
      set_zip_error(zip_get_error(writer->archive), "Failed to add symlink",
                    writer->output_path, 0, 0);
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

// Writes the bare end-of-central-directory record, which readers accept
static int write_empty_zip(const char *path) {
  static const unsigned char EOCD[22] = {'P', 'K', 5, 6};
  FILE *f = fs_fopen(path, "wb");
  int ok = f && fwrite(EOCD, 1, sizeof(EOCD), f) == sizeof(EOCD);
  if (f && fclose(f) != 0)
    ok = 0;
  if (!ok)
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, path);
  return ok ? 0 : -1;
}

static int zip_close_writer(void *writer_ptr, CoreContext *ctx, int discard) {
  ZipWriter *writer = (ZipWriter *)writer_ptr;

  // Prevents redundant compression when the archive is discarded
  if (discard) {
    zip_discard(writer->archive);
    free(writer);
    return 0;
  }

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

  const char *path = writer->output_path;
  int empty = zip_get_num_entries(writer->archive, 0) == 0;
  int ret;
  // Compresses every entry; the progress bridge takes the GIL back itself
  Py_BEGIN_ALLOW_THREADS
  ret = zip_close(writer->archive);
  Py_END_ALLOW_THREADS
  int abort_code = writer->abort_code;

  // A failed zip_close leaves the archive open, so it is discarded here
  if (ret < 0) {
    if (abort_code == 0) {
      set_zip_error(zip_get_error(writer->archive),
                    "Failed to close ZIP archive", writer->output_path, 0, 0);
    }
    zip_discard(writer->archive);
  }
  free(writer);

  if (abort_code != 0) {
    // Cancelled from inside zip_close, which surfaces as a close failure
    return abort_code;
  }

  if (ret < 0)
    return -1;
  return empty ? write_empty_zip(path) : 0;
}

// ---- ZIP Reader ----

typedef struct {
  zip_t *archive;
  zip_int64_t num_entries;
  zip_int64_t current_index;
  zip_file_t *current_file;
  const char *input_path;
} ZipReader;

static void *zip_create_reader(const char *input_path) {
  int err;
  zip_t *za = zip_open(input_path, ZIP_RDONLY, &err);
  if (!za) {
    set_zip_open_error(err, "Failed to open ZIP archive", input_path, 1);
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
  reader->input_path = input_path;

  return reader;
}

static int zip_get_next_entry(void *reader_ptr, ArchiveEntry *entry,
                              CoreContext *ctx) {
  (void)ctx; // libzip has no warnings to pass on
  ZipReader *reader = (ZipReader *)reader_ptr;

  if (reader->current_index >= reader->num_entries) {
    return 0; // No more entries
  }

  struct zip_stat st;
  zip_stat_init(&st);

  if (zip_stat_index(reader->archive, reader->current_index, ZIP_FL_ENC_GUESS,
                     &st) < 0) {
    set_zip_error(zip_get_error(reader->archive), "Failed to stat entry",
                  reader->input_path, 1, 0);
    return -1;
  }

  // Set entry metadata
  entry->path = NULL;
  entry->link_target = NULL;

  if (st.valid & ZIP_STAT_NAME) {
    entry->path = strdup(st.name);
    if (!entry->path) {
      PyErr_NoMemory();
      return -1;
    }

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
    set_zip_error(zip_get_error(reader->archive),
                  "Failed to open file in archive", reader->input_path, 1, 0);
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
      Py_BLOCK_THREADS
      PyErr_SetFromErrno(PyExc_OSError);
      zip_fclose(zf);
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
    zip_fclose(zf);
    return advance;
  }

  if (over_limit) {
    zip_fclose(zf);
    PyErr_SetString(comp_ExtractionPolicyError,
                    "Archive exceeds the maximum extracted size");
    return -1;
  }

  if (bytes_read < 0) {
    set_zip_error(zip_file_get_error(zf), "Error reading from archive",
                  reader->input_path, 1, 0);
    zip_fclose(zf);
    return -1;
  }

  zip_fclose(zf);
  return 0;
}

static int zip_skip_entry(void *reader_ptr) {
  (void)reader_ptr;
  // ZIP reader moves to next entry by default
  return 0;
}

static int zip_close_reader(void *reader_ptr, int discard) {
  ZipReader *reader = (ZipReader *)reader_ptr;

  // Opened read-only, so there is nothing to lose
  if (discard) {
    zip_discard(reader->archive);
    free(reader);
    return 0;
  }

  int ret = zip_close(reader->archive);
  if (ret < 0) {
    set_zip_error(zip_get_error(reader->archive), "Failed to close ZIP archive",
                  reader->input_path, 1, 0);
    zip_discard(reader->archive);
  }
  free(reader);

  if (ret < 0) {
    return -1;
  }

  return 0;
}

// ---- Capability Functions ----

static int zip_supports_compression(void) {
  return 1; // ZIP has built-in DEFLATE compression
}

static int zip_supports_streaming(void) {
  return 0; // libzip requires seekable files
}

// ---- Backend Definition ----

const CArchive ZIP_ARCHIVE = {
    .name = "zip",
    .levels = LEVELS_ZLIB,
    .supports_compression = zip_supports_compression,
    .supports_streaming = zip_supports_streaming,
    .create_writer = zip_create_writer,
    .add_entry = zip_add_entry,
    .close_writer = zip_close_writer,
    .create_reader = zip_create_reader,
    .get_next_entry = zip_get_next_entry,
    .extract_entry_data = zip_extract_entry_data,
    .skip_entry_data = zip_skip_entry,
    .close_reader = zip_close_reader,
};
