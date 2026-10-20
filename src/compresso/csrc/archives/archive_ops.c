#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "archive_ops.h"
#include "paths.h"
#include "../common.h"
#include "../fsutil.h"
#include "../standalone.h"
#include "walk.h"
#include <archive_entry.h>
#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// ---- ArchiveEntry Helpers ----

void entry_reset(ArchiveEntry *e) {
  free(e->path);
  free(e->link_target);
  memset(e, 0, sizeof(*e));
}

int copy_entry_data(int64_t (*read)(void *, void *, size_t), void *handle,
                    void *buffer, FILE *output, uint64_t max_bytes,
                    uint64_t *bytes_written, const CompErrors *errors,
                    CoreContext *ctx) {
  int64_t n;
  uint64_t total = 0;
  int over_limit = 0, write_failed = 0, advance = 0;

  Py_BEGIN_ALLOW_THREADS

  while ((n = read(handle, buffer, CODEC_CHUNK)) > 0) {
    // Cap is applied to the bytes produced rather than to the declared size
    if ((uint64_t)n > max_bytes - total) {
      over_limit = 1;
      break;
    }
    if (fwrite(buffer, 1, (size_t)n, output) != (size_t)n || ferror(output)) {
      write_failed = 1;
      break;
    }
    total += (uint64_t)n;
    if ((advance = ctx_advance(ctx, (size_t)n)) != 0)
      break;
  }

  Py_END_ALLOW_THREADS

  *bytes_written = total;
  if (write_failed) {
    PyErr_SetFromErrno(PyExc_OSError);
    return -1;
  }
  if (advance != 0)
    return advance;
  if (over_limit) {
    PyErr_SetString(errors->extraction_policy,
                    "Archive exceeds the maximum extracted size");
    return -1;
  }
  return n < 0;
}

// Mirror extraction's checks, so an unsafe source fails at write time rather
// than producing an unextractable archive
static int check_storable_entry_name(const char *name) {
  if (name[0] == '\0' || fs_is_absolute(name) || fs_is_stream_path(name) ||
      path_has_parent_segment(name)) {
    PyErr_Format(PyExc_ValueError, "Refusing to store unsafe entry name: %s",
                 name);
    return -1;
  }

  return 0;
}

// ---- Helpers ----

// A walked path's entry name, or NULL with an exception set
static char *entry_name(const WalkItem *item) {
  const char *rel_path = item->path + item->prefix_len;
  while (FS_IS_SEP(*rel_path))
    rel_path++;

  char *name = strdup(rel_path);
  if (!name) {
    PyErr_NoMemory();
    return NULL;
  }

  // Normalise to '/', collapsing runs and dropping a trailing one; writers add
  // the trailing separator a directory needs
  char *w = name;
  for (const char *r = name; *r; r++) {
    if (FS_IS_SEP(*r)) {
      // Keep a leading separator so the absolute name is still rejected below
      if (w > name && w[-1] == '/')
        continue;
      *w++ = '/';
    } else {
      *w++ = *r;
    }
  }
  while (w > name + 1 && w[-1] == '/')
    w--;
  *w = '\0';

  if (check_storable_entry_name(name) != 0) {
    free(name);
    return NULL;
  }
  return name;
}

// Fills the zeroed `entry`; on failure, the caller's does entry_reset
static int entry_from_walk(const WalkItem *item, ArchiveEntry *entry) {
  const char *path = item->path;
  const fs_stat *st = &item->st;

  entry->path = entry_name(item);
  if (!entry->path)
    return -1;

  entry->size = st->size;
  entry->mtime = st->mtime;
  entry->mode = st->mode;

  if (item->link) {
    entry->type = ENTRY_HARDLINK;
    entry->size = 0;
    entry->link_target = entry_name(item->link);
    if (!entry->link_target)
      return -1;
  } else if (st->type == FS_TYPE_DIR) {
    entry->type = ENTRY_DIR;
  } else if (st->type == FS_TYPE_SYMLINK) {
    entry->type = ENTRY_SYMLINK;
    char target[FS_PATH_MAX];
    if (fs_readlink(path, target, sizeof(target)) != 0) {
      PyErr_SetFromErrnoWithFilename(PyExc_OSError, path);
      return -1;
    }
    entry->link_target = strdup(target);
    if (!entry->link_target) {
      PyErr_NoMemory();
      return -1;
    }
  } else if (st->type == FS_TYPE_OTHER) {
    entry->type = ENTRY_SPECIAL; // Only those the walk kept
    entry->special = st->special;
    entry->rdev = st->rdev;
    entry->size = 0;
  } else {
    entry->type = ENTRY_FILE;
  }

  return 0;
}

// The backend for a pipeline's container, so a recognised format without a
// backend (e.g. 7z) is identified
static const CArchive *archive_for_pipeline(const CompErrors *errors,
                                            const CompressionPipeline *p) {
  const CArchive *archive = find_archive_by_id(p->archive);
  if (archive)
    return archive;

  CompressionPipeline container = {.archive = p->archive,
                                   .codec = FORMAT_UNKNOWN};
  char name[32];
  pipeline_display_name(&container, name, sizeof(name));
  PyErr_Format(errors->backend,
               "%s archives are recognised but not supported yet", name);
  return NULL;
}

// Write every walked path into an already-open writer
static int add_walked_entries(const CArchive *archive, void *writer,
                              const WalkList *walked, CoreContext *ctx) {
  for (size_t i = 0; i < walked->count; i++) {
    const WalkItem *item = &walked->items[i];
    ArchiveEntry entry = {0};
    if (entry_from_walk(item, &entry) != 0) {
      entry_reset(&entry);
      return -1;
    }

    FILE *f = NULL;
    if (entry.type == ENTRY_FILE && !archive->reads_sources &&
        !(f = fs_fopen(item->path, "rb"))) {
      PyErr_SetFromErrnoWithFilename(PyExc_OSError, item->path);
      entry_reset(&entry);
      return -1;
    }

    int ret = archive->add_entry(writer, &entry, f, item->path, ctx);
    if (f)
      fclose(f);
    entry_reset(&entry);
    if (ret != 0)
      return ret;
  }
  return 0;
}

// ---- Archive Operations ----

int create_archive(const char *output_path, const CompressionPipeline *pipeline,
                   const char **input_paths, size_t num_paths,
                   int overwrite_existing, char *out_actual_path,
                   size_t out_actual_path_size, CoreContext *ctx) {
  if (!pipeline_is_valid(pipeline) || pipeline->archive == ARCHIVE_NONE) {
    char name[32];
    pipeline_display_name(pipeline, name, sizeof(name));
    PyErr_Format(PyExc_ValueError, "Format does not support archives: %s",
                 name);
    return -1;
  }

  int level = pipeline->compression_level;

  const CArchive *archive = archive_for_pipeline(ctx->errors, pipeline);
  if (!archive)
    return -1;

  OutputTarget out = {.path = output_path,
                      .overwrite = overwrite_existing,
                      .actual = out_actual_path,
                      .actual_size = out_actual_path_size};
  int checked = output_check(NULL, &out);
  if (checked != 0)
    return checked < 0 ? -1 : 0;

  // Written to a temp beside the output, then committed over it
  char tmp_path[FS_PATH_MAX];
  FILE *tmp = output_open(&out, tmp_path);
  if (!tmp)
    return -1;

  // No progress in the codec's context, as add_entry counts the source bytes;
  // it keeps cancel for the flush after the last entry
  const FormatDesc *fmt = NULL;
  CodecStream *stream = NULL;
  CoreContext stream_ctx = {.errors = ctx->errors,
                            .cancel_flag = ctx->cancel_flag};
  void *writer = NULL;
  if (pipeline->codec != FORMAT_UNKNOWN) {
    fmt = find_standalone_format(pipeline->codec);
    CodecParams params = fmt->enc;
    params.level = level;
    params.threads = ctx->threads;
    stream = codec_writer_open(fmt->engine(), &params, tmp, &stream_ctx);
    if (stream)
      writer = archive->create_codec_writer(stream, output_path, ctx);
  } else {
    fclose(tmp);
    tmp = NULL;
    writer = archive->create_writer(tmp_path, level, ctx);
  }
  if (!writer) {
    if (stream)
      codec_writer_close(stream, 1);
    if (tmp)
      fclose(tmp);
    fs_unlink(tmp_path);
    return -1;
  }

  // Once the writer has opened its output, so a tar's identity is known; an
  // existing output is skipped too, as the commit replaces it
  OwnFiles own = {0};
  own_files_add(&own, tmp_path);
  own_files_add(&own, output_path);

  WalkList walked = {0};
  int ret = walk_sources(input_paths, num_paths, &own, archive->stores_specials,
                         archive->stores_hardlinks, ctx, &walked);
  if (ret == 0) {
    ctx_begin_stage(ctx, walked.file_bytes);
    ret = add_walked_entries(archive, writer, &walked, ctx);
  }
  walk_list_free(&walked);

  // libzip does all its compression inside zip_close, so that is where its
  // progress comes from; a run that already failed is discarded instead
  int close_ret = archive->close_writer(writer, ctx, ret != 0);
  if (ret == 0 && close_ret != 0)
    ret = close_ret;

  if (!stream) {
    if (ret == 0)
      return output_commit(tmp_path, &out);
    fs_unlink(tmp_path);
    return ret;
  }

  // libarchive only knows its write callback failed; failed streams return
  // a failure to every write; empty ones swap in the cause (e.g. cancelled)
  if (ret != 0) {
    int stream_ret = codec_write(stream, NULL, 0);
    if (stream_ret != 0) {
      PyErr_Clear();
      ret = stream_ret;
    }
  }

  int codec_ret = codec_writer_close(stream, ret != 0);
  if (ret == 0)
    ret = codec_ret;

  char failure[64];
  snprintf(failure, sizeof(failure), "%s compression failed", fmt->name);
  return output_finish(ret, NULL, tmp, tmp_path, &out, ctx->errors, failure);
}

FILE *open_archive_source(const char *archive_path, const CArchive **archive,
                          CompressionPipeline *pipe, CoreContext *ctx) {
  *archive = NULL;

  Format format;
  FILE *src = open_source(archive_path, &format);
  if (!src)
    return NULL;

  *pipe = pipeline_from_format(format, archive_path);
  if (pipeline_is_valid(pipe) && pipe->archive != ARCHIVE_NONE)
    *archive = archive_for_pipeline(ctx->errors, pipe);
  else
    PyErr_SetString(PyExc_ValueError, "Not an archive format");

  if (!*archive) {
    fclose(src);
    return NULL;
  }
  return src;
}

CodecStream *open_codec_source(const FormatDesc *fmt, FILE *src,
                               CoreContext *ctx) {
  CodecParams params = fmt->dec;
  params.threads = ctx->threads;
  return codec_reader_open(fmt->engine(), &params, src, ctx);
}

int close_codec_source(CodecStream *stream, FILE *src, int drain) {
  if (drain) {
    unsigned char rest[16384];
    size_t got;
    Py_BEGIN_ALLOW_THREADS
    while (codec_read(stream, rest, sizeof(rest), &got) == 0 && got > 0) {
    }
    Py_END_ALLOW_THREADS
  }

  int rc = codec_reader_close(stream);
  fclose(src);
  return rc;
}

// Name an entry's type as the Python layer sees it
static const char *entry_type_name(const ArchiveEntry *entry) {
  switch (entry->type) {
  case ENTRY_DIR:
    return "dir";
  case ENTRY_SYMLINK:
    return "symlink";
  case ENTRY_SPECIAL:
    return entry->special == AE_IFIFO   ? "fifo"
           : entry->special == AE_IFCHR ? "char"
           : entry->special == AE_IFBLK ? "block"
                                        : "special";
  case ENTRY_HARDLINK:
    return "hardlink";
  case ENTRY_FILE:
  default:
    return "file";
  }
}

// `compressed_size`, `crc` and `method` are present only for a container that
// compresses each entry separately; tar compresses the whole stream, so those
// keys are absent rather than None
//
// Names are UTF-8 by convention only; surrogateescape keeps one that isn't
// listable, as os.fsdecode does, and None stands for a missing name
static PyObject *decode_entry_name(const char *name) {
  if (!name)
    Py_RETURN_NONE;
  return PyUnicode_DecodeUTF8(name, (Py_ssize_t)strlen(name),
                              "surrogateescape");
}

static PyObject *entry_to_dict(const ArchiveEntry *entry) {
  PyObject *path = decode_entry_name(entry->path ? entry->path : "");
  int is_link = entry->type == ENTRY_SYMLINK || entry->type == ENTRY_HARDLINK;
  PyObject *target =
      path ? decode_entry_name(is_link ? entry->link_target : NULL) : NULL;
  if (!target) {
    Py_XDECREF(path);
    return NULL;
  }

  // "N" hands both references to the dict
  PyObject *item = Py_BuildValue(
      "{s:N, s:s, s:K, s:L, s:I, s:N}", "path", path, "type",
      entry_type_name(entry), "size", (unsigned long long)entry->size, "mtime",
      (long long)entry->mtime, "mode", (unsigned int)entry->mode, "link_target",
      target);

  if (!item || !entry->has_compression_detail)
    return item;

  PyObject *detail = Py_BuildValue("{s:K, s:I, s:I}", "compressed_size",
                                   (unsigned long long)entry->compressed_size,
                                   "crc", (unsigned int)entry->crc, "method",
                                   (unsigned int)entry->method);

  if (!detail) {
    Py_DECREF(item);
    return NULL;
  }

  int merged = PyDict_Update(item, detail);
  Py_DECREF(detail);

  if (merged < 0) {
    Py_DECREF(item);
    return NULL;
  }

  return item;
}

// Collect one dict per entry from an already-open reader into a new Python list
static PyObject *read_archive_entries(const CArchive *archive, void *reader,
                                      CoreContext *ctx) {
  PyObject *list = PyList_New(0);
  if (!list)
    return NULL;

  ArchiveEntry entry = {0};
  int ret;

  while ((ret = archive->get_next_entry(reader, &entry, ctx)) == 1) {
    PyObject *item = entry_to_dict(&entry);
    entry_reset(&entry);

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
  }

  if (ret < 0) {
    Py_DECREF(list);
    return NULL;
  }

  return list;
}

PyObject *list_archive_contents(const char *archive_path, CoreContext *ctx) {
  const CArchive *archive;
  CompressionPipeline pipe;
  FILE *src = open_archive_source(archive_path, &archive, &pipe, ctx);
  if (!src)
    return NULL;

  // Decoded as libarchive reads
  CodecStream *stream = NULL;
  void *reader = NULL;
  if (pipe.codec != FORMAT_UNKNOWN) {
    const FormatDesc *fmt = find_standalone_format(pipe.codec);
    ctx_begin_stage_stream(ctx, src);
    stream = open_codec_source(fmt, src, ctx);
    if (stream)
      reader = archive->create_codec_reader(stream, archive_path, ctx);
  } else {
    fclose(src);
    src = NULL;
    reader = archive->create_reader(archive_path, ctx);
  }

  PyObject *list = reader ? read_archive_entries(archive, reader, ctx) : NULL;
  int failed = list == NULL;
  // A close failure after a full listing would otherwise return the list
  if (reader && archive->close_reader(reader, failed) != 0)
    failed = 1;

  if (stream && close_codec_source(stream, src, !failed) != 0)
    failed = 1;
  else if (!stream && src)
    fclose(src);

  if (failed) {
    Py_XDECREF(list);
    return NULL;
  }
  return list;
}

// ---- Archive Capabilities ----

PyObject *get_archive_capabilities(void) {
  PyObject *list = PyList_New(0);
  if (!list)
    return NULL;

  for (const FormatDesc *d = FORMATS; d->name; d++) {
    const CArchive *a = d->backend;
    if (!a)
      continue;

    PyObject *dict = PyDict_New();
    if (!dict) {
      Py_DECREF(list);
      return NULL;
    }

    PyObject *name = PyUnicode_FromString(a->name);
    PyObject *streaming = a->streaming ? Py_True : Py_False;
    PyObject *compression = a->compression ? Py_True : Py_False;

    if (!name || PyDict_SetItemString(dict, "name", name) < 0 ||
        PyDict_SetItemString(dict, "streaming", streaming) < 0 ||
        PyDict_SetItemString(dict, "compression", compression) < 0) {
      Py_XDECREF(name);
      Py_DECREF(dict);
      Py_DECREF(list);
      return NULL;
    }
    Py_DECREF(name);

    if (PyList_Append(list, dict) < 0) {
      Py_DECREF(dict);
      Py_DECREF(list);
      return NULL;
    }
    Py_DECREF(dict);
  }

  return list;
}
