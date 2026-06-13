#include <dirent.h>
#include <limits.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#define PY_SSIZE_T_CLEAN
#include "archives.h"
#include "common.h"
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
  struct stat st;
  if (stat(path, &st) != 0) {
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

  entry->size = (uint64_t)st.st_size;
  entry->mtime = st.st_mtime;
  entry->mode = st.st_mode & 0777;

  if (S_ISDIR(st.st_mode)) {
    entry->type = ENTRY_DIR;
  } else if (S_ISLNK(st.st_mode)) {
    entry->type = ENTRY_SYMLINK;
    char target[PATH_MAX];
    ssize_t len = readlink(path, target, sizeof(target) - 1);
    if (len > 0) {
      target[len] = '\0';
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
  DIR *dir = opendir(dir_path);
  if (!dir) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, dir_path);
    return -1;
  }

  struct dirent *dent;
  while ((dent = readdir(dir)) != NULL) {
    if (strcmp(dent->d_name, ".") == 0 || strcmp(dent->d_name, "..") == 0)
      continue;

    char full_path[PATH_MAX];
    snprintf(full_path, sizeof(full_path), "%s/%s", dir_path, dent->d_name);

    ArchiveEntry *ae = create_entry_from_path(full_path, base_path);
    if (!ae) {
      closedir(dir);
      return -1;
    }

    if (ae->type == ENTRY_FILE) {
      FILE *f = fopen(full_path, "rb");
      if (!f) {
        entry_free(ae);
        closedir(dir);
        PyErr_SetFromErrnoWithFilename(PyExc_OSError, full_path);
        return -1;
      }
      int ret = archive->add_entry(writer, ae, f);
      fclose(f);
      entry_free(ae);
      if (ret != 0) {
        closedir(dir);
        return -1;
      }
    } else if (ae->type == ENTRY_DIR) {
      archive->add_entry(writer, ae, NULL);
      entry_free(ae);
      if (add_directory_recursive(writer, archive, full_path, base_path) != 0) {
        closedir(dir);
        return -1;
      }
    } else {
      archive->add_entry(writer, ae, NULL);
      entry_free(ae);
    }
  }

  closedir(dir);
  return 0;
}

static int validate_entry_path(const char *output_dir, const char *entry_path,
                               uint32_t depth,
                               const ExtractionPolicy *policy) {
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

  char candidate[PATH_MAX];
  if (snprintf(candidate, sizeof(candidate), "%s/%s", output_dir, entry_path) >=
      (int)sizeof(candidate)) {
    PyErr_SetString(PyExc_ValueError, "Archive entry path too long");
    return -1;
  }

  char resolved_dir[PATH_MAX];
  char *last_sep = strrchr(candidate, '/');
  if (last_sep)
    *last_sep = '\0';

  if (!realpath(candidate, resolved_dir)) {
    if (strstr(candidate, "..") != NULL) {
      PyErr_Format(PyExc_ValueError, "Path traversal detected in entry: %s",
                   entry_path);
      return -1;
    }
  } else {
    char resolved_root[PATH_MAX];
    if (!realpath(output_dir, resolved_root)) {
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

// ---- Archive Operations ----

int create_archive(const char *output_path, Format format,
                   const char **input_paths, size_t num_paths,
                   int compression_level) {
  if (format == FORMAT_UNKNOWN) {
    PyErr_Format(PyExc_ValueError, "Unknown format: %s",
                 format_name_string(format));
    return -1;
  }

  const CArchive *archive = NULL;

  if (format == FORMAT_TAR || format_is_combined(format)) {
    archive = get_tar_archive();
  } else if (format == FORMAT_ZIP) {
    archive = get_zip_archive();
  } else {
    PyErr_SetString(PyExc_ValueError, "Format does not support archives");
    return -1;
  }

  if (!archive || !archive->is_available()) {
    PyErr_SetString(comp_Error, "Archive backend not available");
    return -1;
  }

  void *writer = archive->create_writer(output_path, compression_level);
  if (!writer)
    return -1;

  for (size_t i = 0; i < num_paths; i++) {
    struct stat st;
    if (stat(input_paths[i], &st) != 0) {
      archive->close_writer(writer);
      PyErr_SetFromErrnoWithFilename(PyExc_OSError, input_paths[i]);
      return -1;
    }

    if (S_ISDIR(st.st_mode)) {
      if (add_directory_recursive(writer, archive, input_paths[i],
                                  input_paths[i]) != 0) {
        archive->close_writer(writer);
        return -1;
      }
    } else {
      ArchiveEntry *entry = create_entry_from_path(input_paths[i], NULL);
      if (!entry) {
        archive->close_writer(writer);
        return -1;
      }

      FILE *f = fopen(input_paths[i], "rb");
      if (!f) {
        entry_free(entry);
        archive->close_writer(writer);
        PyErr_SetFromErrnoWithFilename(PyExc_OSError, input_paths[i]);
        return -1;
      }

      int ret = archive->add_entry(writer, entry, f);
      fclose(f);
      entry_free(entry);

      if (ret != 0) {
        archive->close_writer(writer);
        return -1;
      }
    }
  }

  return archive->close_writer(writer);
}

int extract_archive(const char *archive_path, const char *output_dir,
                    const char **files, size_t num_files) {
  const ExtractionPolicy *policy = &EXTRACTION_POLICY_DEFAULT;

  Format format = detect_format_from_path(archive_path);

  const CArchive *archive = NULL;

  if (format == FORMAT_TAR || format_is_combined(format)) {
    archive = get_tar_archive();
  } else if (format == FORMAT_ZIP) {
    archive = get_zip_archive();
  } else {
    PyErr_SetString(PyExc_ValueError, "Not an archive format");
    return -1;
  }

  if (!archive || !archive->is_available()) {
    PyErr_SetString(comp_Error, "Archive backend not available");
    return -1;
  }

  void *reader = archive->create_reader(archive_path);
  if (!reader)
    return -1;

  mkdir(output_dir, 0755);

  ArchiveEntry entry;
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
      archive->close_reader(reader);
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

    char out_path[PATH_MAX];
    snprintf(out_path, sizeof(out_path), "%s/%s", output_dir, entry.path);

    if (entry.type == ENTRY_DIR) {
      mkdir(out_path, entry.mode);
    } else if (entry.type == ENTRY_FILE) {
      char *last_slash = strrchr(out_path, '/');
      if (last_slash) {
        *last_slash = '\0';
        mkdir(out_path, 0755);
        *last_slash = '/';
      }

      FILE *f = fopen(out_path, "wb");
      if (!f) {
        free(entry.path);
        free(entry.symlink_target);
        archive->close_reader(reader);
        PyErr_SetFromErrnoWithFilename(PyExc_OSError, out_path);
        return -1;
      }

      if (archive->extract_entry_data(reader, f) != 0) {
        fclose(f);
        free(entry.path);
        free(entry.symlink_target);
        archive->close_reader(reader);
        return -1;
      }

      fclose(f);
      chmod(out_path, entry.mode);
    }

    free(entry.path);
    free(entry.symlink_target);
  }

  if (ret < 0) {
    archive->close_reader(reader);
    return -1;
  }

  return archive->close_reader(reader);
}

PyObject *list_archive_contents(const char *archive_path) {
  Format format = detect_format_from_path(archive_path);

  const CArchive *archive = NULL;

  if (format == FORMAT_TAR || format_is_combined(format)) {
    archive = get_tar_archive();
  } else if (format == FORMAT_ZIP) {
    archive = get_zip_archive();
  } else {
    PyErr_SetString(PyExc_ValueError, "Not an archive format");
    return NULL;
  }

  if (!archive || !archive->is_available()) {
    PyErr_SetString(comp_Error, "Archive backend not available");
    return NULL;
  }

  void *reader = archive->create_reader(archive_path);
  if (!reader)
    return NULL;

  PyObject *list = PyList_New(0);
  if (!list) {
    archive->close_reader(reader);
    return NULL;
  }

  ArchiveEntry entry;
  int ret;

  while ((ret = archive->get_next_entry(reader, &entry)) == 1) {
    PyObject *item =
        PyUnicode_FromString(entry.path ? entry.path : "");
    free(entry.path);
    free(entry.symlink_target);

    if (!item) {
      Py_DECREF(list);
      archive->close_reader(reader);
      return NULL;
    }
    if (PyList_Append(list, item) < 0) {
      Py_DECREF(item);
      Py_DECREF(list);
      archive->close_reader(reader);
      return NULL;
    }
    Py_DECREF(item);
    archive->skip_entry_data(reader);
  }

  archive->close_reader(reader);

  if (ret < 0) {
    Py_DECREF(list);
    return NULL;
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

    if (!name ||
        PyDict_SetItemString(dict, "name", name) < 0 ||
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
