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

// ---- ZIP Writer ----

typedef struct {
  zip_t *archive;
  const char *output_path;
  int compression_level;
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
  writer->output_path = output_path;
  writer->compression_level = (compression_level >= 0 && compression_level <= 9)
                                  ? compression_level
                                  : 6; // Default

  return writer;
}

static int zip_add_entry(void *writer_ptr, const ArchiveEntry *entry,
                         FILE *data) {
  ZipWriter *writer = (ZipWriter *)writer_ptr;

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

    // Read file contents into memory (libzip requires seekable sources)
    fseek(data, 0, SEEK_END);
    long file_size = ftell(data);
    fseek(data, 0, SEEK_SET);

    if (file_size < 0) {
      PyErr_SetString(PyExc_IOError, "Failed to get file size");
      return -1;
    }

    char *buffer = safe_malloc(file_size);
    if (!buffer) {
      return -1;
    }

    size_t read = fread(buffer, 1, file_size, data);
    if (read != (size_t)file_size) {
      free(buffer);
      PyErr_SetString(PyExc_IOError, "Failed to read file data");
      return -1;
    }

    // Create ZIP source from buffer
    zip_source_t *source = zip_source_buffer(
        writer->archive, buffer, (zip_uint64_t)file_size, 1); // 1 = free buffer
    if (!source) {
      free(buffer);
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

static int zip_close_writer(void *writer_ptr) {
  ZipWriter *writer = (ZipWriter *)writer_ptr;

  int ret = zip_close(writer->archive);
  free(writer);

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

  reader->current_index++;

  return 1; // Entry retrieved
}

static int zip_extract_entry_data(void *reader_ptr, FILE *output) {
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

  Py_BEGIN_ALLOW_THREADS

      while ((bytes_read = zip_fread(zf, buffer, sizeof(buffer))) > 0) {
    size_t written = fwrite(buffer, 1, bytes_read, output);
    if (written != (size_t)bytes_read || ferror(output)) {
      Py_BLOCK_THREADS zip_fclose(zf);
      PyErr_SetString(PyExc_IOError, "Error writing output");
      return -1;
    }
  }

  Py_END_ALLOW_THREADS

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
