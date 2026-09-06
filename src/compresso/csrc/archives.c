#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define PY_SSIZE_T_CLEAN
#include "archives.h"
#include "common.h"
#include "fsutil.h"
#include "standalone.h"
#include <Python.h>

// ---- Default Extraction Policies ----

static const ExtractionPolicy EXTRACTION_POLICY_DEFAULT = {
    .allow_symlinks = 0,
    .allow_absolute_paths = 0,
    .allow_special_files = 0,
    .overwrite_existing = 0,
    .preserve_permissions = 1,
    .preserve_timestamps = 1,
    .max_depth = 32,
    .max_total_size = 0,
};

ExtractionPolicy extraction_policy_default(void) {
  return EXTRACTION_POLICY_DEFAULT;
}

// ---- ArchiveEntry Helpers ----

static ArchiveEntry *entry_alloc(void) {
  ArchiveEntry *e = safe_malloc(sizeof(ArchiveEntry));
  if (!e)
    return NULL;
  e->path = NULL;
  e->type = ENTRY_FILE;
  e->size = 0;
  e->mtime = 0;
  e->mode = 0;
  e->symlink_target = NULL;
  e->internal_data = NULL;
  return e;
}

static void entry_free(ArchiveEntry *e) {
  if (!e)
    return;
  free(e->path);
  free(e->symlink_target);
  free(e);
}

// ---- Helpers ----

static ArchiveEntry *create_entry_from_path(const char *path,
                                            const char *base_path) {
  fs_stat st;
  if (fs_stat_path(path, &st) != 0) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, path);
    return NULL;
  }

  ArchiveEntry *entry = entry_alloc();
  if (!entry)
    return NULL;

  const char *rel_path = path;
  if (base_path && strncmp(path, base_path, strlen(base_path)) == 0) {
    rel_path = path + strlen(base_path);
    if (*rel_path == '/')
      rel_path++;
  }
  entry->path = strdup(rel_path);
  if (!entry->path) {
    entry_free(entry);
    PyErr_NoMemory();
    return NULL;
  }

  entry->size = st.size;
  entry->mtime = st.mtime;
  entry->mode = st.mode;

  if (st.type == FS_TYPE_DIR) {
    entry->type = ENTRY_DIR;
  } else if (st.type == FS_TYPE_SYMLINK) {
    entry->type = ENTRY_SYMLINK;
    char target[FS_PATH_MAX];
    if (fs_readlink(path, target, sizeof(target)) == 0) {
      entry->symlink_target = strdup(target);
    }
  } else {
    entry->type = ENTRY_FILE;
  }

  return entry;
}

static int add_directory_recursive(void *writer, const CArchive *archive,
                                   const char *dir_path,
                                   const char *base_path) {
  fs_dir *dir = fs_opendir(dir_path);
  if (!dir) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, dir_path);
    return -1;
  }

  const char *name;
  while ((name = fs_readdir(dir)) != NULL) {
    char full_path[FS_PATH_MAX];
    snprintf(full_path, sizeof(full_path), "%s/%s", dir_path, name);

    ArchiveEntry *ae = create_entry_from_path(full_path, base_path);
    if (!ae) {
      fs_closedir(dir);
      return -1;
    }

    if (ae->type == ENTRY_FILE) {
      FILE *f = fopen(full_path, "rb");
      if (!f) {
        entry_free(ae);
        fs_closedir(dir);
        PyErr_SetFromErrnoWithFilename(PyExc_OSError, full_path);
        return -1;
      }
      int ret = archive->add_entry(writer, ae, f);
      fclose(f);
      entry_free(ae);
      if (ret != 0) {
        fs_closedir(dir);
        return -1;
      }
    } else if (ae->type == ENTRY_DIR) {
      archive->add_entry(writer, ae, NULL);
      entry_free(ae);
      if (add_directory_recursive(writer, archive, full_path, base_path) != 0) {
        fs_closedir(dir);
        return -1;
      }
    } else {
      archive->add_entry(writer, ae, NULL);
      entry_free(ae);
    }
  }

  fs_closedir(dir);
  return 0;
}

static int validate_entry_path(const char *output_dir, const char *entry_path,
                               uint32_t depth, const ExtractionPolicy *policy) {
  if (entry_path[0] == '/') {
    PyErr_Format(PyExc_ValueError, "Archive entry has an absolute path: %s",
                 entry_path);
    return -1;
  }

  if (policy->max_depth > 0 && depth > policy->max_depth) {
    PyErr_Format(PyExc_ValueError, "Archive entry exceeds max depth (%u): %s",
                 policy->max_depth, entry_path);
    return -1;
  }

  char candidate[FS_PATH_MAX];
  if (snprintf(candidate, sizeof(candidate), "%s/%s", output_dir, entry_path) >=
      (int)sizeof(candidate)) {
    PyErr_SetString(PyExc_ValueError, "Archive entry path too long");
    return -1;
  }

  char resolved_dir[FS_PATH_MAX];
  char *last_sep = strrchr(candidate, '/');
  if (last_sep)
    *last_sep = '\0';

  if (fs_realpath(candidate, resolved_dir) != 0) {
    if (strstr(candidate, "..") != NULL) {
      PyErr_Format(PyExc_ValueError, "Path traversal detected in entry: %s",
                   entry_path);
      return -1;
    }
  } else {
    char resolved_root[FS_PATH_MAX];
    if (fs_realpath(output_dir, resolved_root) != 0) {
      PyErr_SetFromErrnoWithFilename(PyExc_OSError, output_dir);
      return -1;
    }
    size_t root_len = strlen(resolved_root);
    if (strncmp(resolved_dir, resolved_root, root_len) != 0 ||
        (resolved_dir[root_len] != '/' && resolved_dir[root_len] != '\0')) {
      PyErr_Format(PyExc_ValueError, "Path traversal detected in entry: %s",
                   entry_path);
      return -1;
    }
  }

  if (last_sep)
    *last_sep = '/';

  return 0;
}

static int check_entry_policy(const ArchiveEntry *entry,
                              const ExtractionPolicy *policy) {
  if (entry->type == ENTRY_SYMLINK && !policy->allow_symlinks) {
    PyErr_Format(PyExc_ValueError,
                 "Archive contains symlink, but policy denies it: %s",
                 entry->path);
    return -1;
  }

  if (entry->type == ENTRY_SPECIAL && !policy->allow_special_files) {
    PyErr_Format(PyExc_ValueError,
                 "Archive contains special file, but policy denies it: %s",
                 entry->path);
    return -1;
  }

  return 0;
}

// ---- Archive Registry ----

const CArchive *find_archive_by_id(uint8_t id) {
  switch (id) {
  case ARCHIVE_TAR:
    return get_tar_archive();
  case ARCHIVE_ZIP:
    return get_zip_archive();
  default:
    return NULL;
  }
}

// ---- Pipeline Helpers ----

// Build a writable temporary path alongside final_path
static char *make_temp_path(const char *final_path) {
  static const char SUFFIX[] = ".compresso-XXXXXX";
  const char *slash = strrchr(final_path, '/');
  size_t dir_len = slash ? (size_t)(slash - final_path + 1) : 0;
  size_t len = dir_len + sizeof(SUFFIX); // sizeof includes the NUL

  char *tmpl = safe_malloc(len);
  if (!tmpl)
    return NULL;
  if (dir_len)
    memcpy(tmpl, final_path, dir_len);
  memcpy(tmpl + dir_len, SUFFIX, sizeof(SUFFIX));

  if (fs_mkstemp(tmpl) != 0) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, tmpl);
    free(tmpl);
    return NULL;
  }
  return tmpl;
}

// Write every input path into an already-open writer
static int add_paths_to_writer(const CArchive *archive, void *writer,
                               const char **input_paths, size_t num_paths) {
  for (size_t i = 0; i < num_paths; i++) {
    fs_stat st;
    if (fs_stat_path(input_paths[i], &st) != 0) {
      PyErr_SetFromErrnoWithFilename(PyExc_OSError, input_paths[i]);
      return -1;
    }

    if (st.type == FS_TYPE_DIR) {
      // Strip only the source's parent, so the source directory's own name is
      // preserved in stored entry paths
      char base[FS_PATH_MAX];
      const char *slash = strrchr(input_paths[i], '/');
      if (slash) {
        size_t base_len = (size_t)(slash - input_paths[i]);
        if (base_len >= sizeof(base)) {
          PyErr_SetString(PyExc_ValueError, "Source path too long");
          return -1;
        }
        memcpy(base, input_paths[i], base_len);
        base[base_len] = '\0';
      } else {
        base[0] = '\0';
      }

      // Record the directory itself so empty directories are preserved
      ArchiveEntry *dir_entry = create_entry_from_path(input_paths[i], base);
      if (!dir_entry)
        return -1;
      int dir_ret = archive->add_entry(writer, dir_entry, NULL);
      entry_free(dir_entry);
      if (dir_ret != 0)
        return -1;

      if (add_directory_recursive(writer, archive, input_paths[i], base) != 0)
        return -1;
    } else {
      ArchiveEntry *entry = create_entry_from_path(input_paths[i], NULL);
      if (!entry)
        return -1;

      FILE *f = fopen(input_paths[i], "rb");
      if (!f) {
        entry_free(entry);
        PyErr_SetFromErrnoWithFilename(PyExc_OSError, input_paths[i]);
        return -1;
      }

      int ret = archive->add_entry(writer, entry, f);
      fclose(f);
      entry_free(entry);

      if (ret != 0)
        return -1;
    }
  }

  return 0;
}

// ---- Archive Operations ----

int create_archive(const char *output_path, const CompressionPipeline *pipeline,
                   const char **input_paths, size_t num_paths) {
  if (!pipeline_is_valid(pipeline) || pipeline->archive == ARCHIVE_NONE) {
    char name[32];
    pipeline_display_name(pipeline, name, sizeof(name));
    PyErr_Format(PyExc_ValueError, "Format does not support archives: %s",
                 name);
    return -1;
  }

  int level = pipeline->compression_level;

  const CArchive *archive = find_archive_by_id(pipeline->archive);
  if (!archive || !archive->is_available()) {
    PyErr_SetString(comp_Error, "Archive backend not available");
    return -1;
  }

  // Write archive to a temp file, then compress via the standalone codec
  char *tmp_path = NULL;
  const char *write_path = output_path;
  if (pipeline->codec != FORMAT_UNKNOWN) {
    tmp_path = make_temp_path(output_path);
    if (!tmp_path)
      return -1;
    write_path = tmp_path;
  }

  void *writer = archive->create_writer(write_path, level);
  if (!writer) {
    if (tmp_path) {
      fs_unlink(tmp_path);
      free(tmp_path);
    }
    return -1;
  }

  int ret = add_paths_to_writer(archive, writer, input_paths, num_paths);
  if (archive->close_writer(writer) != 0)
    ret = -1;

  if (ret == 0 && pipeline->codec != FORMAT_UNKNOWN) {
    const StandaloneFormat *codec = find_standalone_format(pipeline->codec);
    ret = codec->compress_file(tmp_path, output_path, level);
  }

  if (tmp_path) {
    fs_unlink(tmp_path);
    free(tmp_path);
  }
  return ret;
}

// Read and write each entry from an already-open reader
static int extract_entries(const CArchive *archive, void *reader,
                           const char *output_dir, const char **files,
                           size_t num_files, const ExtractionPolicy *policy) {
  ArchiveEntry entry = {0};
  int ret;

  while ((ret = archive->get_next_entry(reader, &entry)) == 1) {
    if (!entry.path) {
      archive->skip_entry_data(reader);
      continue;
    }

    if (validate_entry_path(output_dir, entry.path, 0, policy) != 0 ||
        check_entry_policy(&entry, policy) != 0) {
      free(entry.path);
      free(entry.symlink_target);
      return -1;
    }

    if (num_files > 0) {
      int should_extract = 0;
      for (size_t i = 0; i < num_files; i++) {
        if (strcmp(entry.path, files[i]) == 0) {
          should_extract = 1;
          break;
        }
      }
      if (!should_extract) {
        free(entry.path);
        free(entry.symlink_target);
        archive->skip_entry_data(reader);
        continue;
      }
    }

    char out_path[FS_PATH_MAX];
    snprintf(out_path, sizeof(out_path), "%s/%s", output_dir, entry.path);

    if (entry.type == ENTRY_DIR) {
      fs_mkdir_p(out_path, entry.mode);
    } else if (entry.type == ENTRY_FILE) {
      char *last_slash = strrchr(out_path, '/');
      if (last_slash) {
        *last_slash = '\0';
        fs_mkdir_p(out_path, 0755);
        *last_slash = '/';
      }

      FILE *f = fopen(out_path, "wb");
      if (!f) {
        free(entry.path);
        free(entry.symlink_target);
        PyErr_SetFromErrnoWithFilename(PyExc_OSError, out_path);
        return -1;
      }

      if (archive->extract_entry_data(reader, f) != 0) {
        fclose(f);
        free(entry.path);
        free(entry.symlink_target);
        return -1;
      }

      fclose(f);
      // Apply the recorded permission bits once the file is closed
      // Best-effort: a chmod failure must not fail extraction of
      // otherwise-valid data
      fs_chmod(out_path, entry.mode);
    }

    free(entry.path);
    free(entry.symlink_target);
  }

  return ret < 0 ? -1 : 0;
}

int extract_archive(const char *archive_path, const char *output_dir,
                    const char **files, size_t num_files) {
  const ExtractionPolicy *policy = &EXTRACTION_POLICY_DEFAULT;

  CompressionPipeline pipe = detect_pipeline_from_path(archive_path);
  if (!pipeline_is_valid(&pipe) || pipe.archive == ARCHIVE_NONE) {
    PyErr_SetString(PyExc_ValueError, "Not an archive format");
    return -1;
  }

  const CArchive *archive = find_archive_by_id(pipe.archive);
  if (!archive || !archive->is_available()) {
    PyErr_SetString(comp_Error, "Archive backend not available");
    return -1;
  }

  // Decode the codec stage to a temporary archive first, if present
  char *tmp_path = NULL;
  const char *read_path = archive_path;
  if (pipe.codec != FORMAT_UNKNOWN) {
    const StandaloneFormat *codec = find_standalone_format(pipe.codec);
    tmp_path = make_temp_path(archive_path);
    if (!tmp_path)
      return -1;
    if (codec->decompress_file(archive_path, tmp_path) != 0) {
      fs_unlink(tmp_path);
      free(tmp_path);
      return -1;
    }
    read_path = tmp_path;
  }

  void *reader = archive->create_reader(read_path);
  if (!reader) {
    if (tmp_path) {
      fs_unlink(tmp_path);
      free(tmp_path);
    }
    return -1;
  }

  fs_mkdir_p(output_dir, 0755);

  int ret =
      extract_entries(archive, reader, output_dir, files, num_files, policy);
  if (archive->close_reader(reader) != 0)
    ret = -1;

  if (tmp_path) {
    fs_unlink(tmp_path);
    free(tmp_path);
  }
  return ret;
}

// Collect entry paths from an already-open reader into a new Python list
static PyObject *read_archive_names(const CArchive *archive, void *reader) {
  PyObject *list = PyList_New(0);
  if (!list)
    return NULL;

  ArchiveEntry entry = {0};
  int ret;

  while ((ret = archive->get_next_entry(reader, &entry)) == 1) {
    PyObject *item = PyUnicode_FromString(entry.path ? entry.path : "");
    free(entry.path);
    free(entry.symlink_target);

    if (!item) {
      Py_DECREF(list);
      return NULL;
    }
    if (PyList_Append(list, item) < 0) {
      Py_DECREF(item);
      Py_DECREF(list);
      return NULL;
    }
    Py_DECREF(item);
    archive->skip_entry_data(reader);
  }

  if (ret < 0) {
    Py_DECREF(list);
    return NULL;
  }

  return list;
}

PyObject *list_archive_contents(const char *archive_path) {
  CompressionPipeline pipe = detect_pipeline_from_path(archive_path);
  if (!pipeline_is_valid(&pipe) || pipe.archive == ARCHIVE_NONE) {
    PyErr_SetString(PyExc_ValueError, "Not an archive format");
    return NULL;
  }

  const CArchive *archive = find_archive_by_id(pipe.archive);
  if (!archive || !archive->is_available()) {
    PyErr_SetString(comp_Error, "Archive backend not available");
    return NULL;
  }

  // Decode the codec stage to a temporary archive first, if present
  char *tmp_path = NULL;
  const char *read_path = archive_path;
  if (pipe.codec != FORMAT_UNKNOWN) {
    const StandaloneFormat *codec = find_standalone_format(pipe.codec);
    tmp_path = make_temp_path(archive_path);
    if (!tmp_path)
      return NULL;
    if (codec->decompress_file(archive_path, tmp_path) != 0) {
      fs_unlink(tmp_path);
      free(tmp_path);
      return NULL;
    }
    read_path = tmp_path;
  }

  void *reader = archive->create_reader(read_path);
  if (!reader) {
    if (tmp_path) {
      fs_unlink(tmp_path);
      free(tmp_path);
    }
    return NULL;
  }

  PyObject *list = read_archive_names(archive, reader);
  archive->close_reader(reader);

  if (tmp_path) {
    fs_unlink(tmp_path);
    free(tmp_path);
  }
  return list;
}

// ---- Archive Capabilities ----

PyObject *get_archive_capabilities(void) {
  PyObject *list = PyList_New(0);
  if (!list)
    return NULL;

  const CArchive *backends[] = {get_tar_archive(), get_zip_archive()};
  size_t n = sizeof(backends) / sizeof(backends[0]);

  for (size_t i = 0; i < n; i++) {
    const CArchive *a = backends[i];
    if (!a || !a->is_available())
      continue;

    PyObject *dict = PyDict_New();
    if (!dict) {
      Py_DECREF(list);
      return NULL;
    }

    PyObject *name = PyUnicode_FromString(a->name ? a->name : "");
    PyObject *streaming = a->supports_streaming() ? Py_True : Py_False;
    PyObject *compression = a->supports_compression() ? Py_True : Py_False;

    if (!name || PyDict_SetItemString(dict, "name", name) < 0 ||
        PyDict_SetItemString(dict, "streaming", streaming) < 0 ||
        PyDict_SetItemString(dict, "compression", compression) < 0) {
      Py_XDECREF(name);
      Py_DECREF(dict);
      Py_DECREF(list);
      return NULL;
    }
    Py_DECREF(name);
    Py_INCREF(streaming);
    Py_INCREF(compression);

    if (PyList_Append(list, dict) < 0) {
      Py_DECREF(dict);
      Py_DECREF(list);
      return NULL;
    }
    Py_DECREF(dict);
  }

  return list;
}
