#define PY_SSIZE_T_CLEAN
#include "common.h"
#include "fsutil.h"
#include <Python.h>
#include <errno.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdlib.h>

int validate_size(uint64_t size, uint64_t max_size, const char *name) {
  if (size == 0) {
    PyErr_Format(PyExc_ValueError, "%s is zero", name);
    return -1;
  }
  if (size > max_size) {
    PyErr_Format(PyExc_ValueError,
                 "%s (%llu bytes) exceeds maximum size (%llu bytes)", name,
                 (unsigned long long)size, (unsigned long long)max_size);
    return -1;
  }
  return 0;
}

void *safe_malloc(size_t size) {
  if (size == 0) {
    PyErr_SetString(PyExc_ValueError, "Cannot allocate zero bytes");
    return NULL;
  }
  if (size > SIZE_MAX / 2) {
    PyErr_Format(PyExc_MemoryError, "Allocation size (%zu bytes) is too large",
                 size);
    return NULL;
  }

  void *ptr = malloc(size);
  if (!ptr) {
    PyErr_NoMemory();
  }
  return ptr;
}

int check_source_readable(const char *path) {
  FILE *f = fs_fopen(path, "rb");
  if (!f) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, path);
    return -1;
  }

  // A directory opens fine on POSIX and only fails on read, with EISDIR
  (void)fgetc(f);
  int read_failed = ferror(f);
  int saved_errno = errno;
  fclose(f);

  if (read_failed) {
    errno = saved_errno;
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, path);
    return -1;
  }
  return 0;
}

void set_backend_error(const CBackend *backend, const char *op,
                       const char *context) {
  PyErr_Format(comp_BackendError, "Backend '%s' %s failed (%s)",
               backend && backend->name ? backend->name : "unknown", op,
               context);
}

// ---- Progress and Cancellation ----

// Report ~200 times per job, but never more often than once per MiB
#define CTX_TARGET_REPORTS 200
#define CTX_MIN_INTERVAL (1024ULL * 1024ULL)

void ctx_begin_job(CoreContext *ctx, uint64_t estimated_total) {
  if (!ctx) {
    return;
  }

  ctx->multi_stage = 1;
  ctx->job_total = estimated_total;
  ctx->job_done = 0;
}

void ctx_begin_stage(CoreContext *ctx, uint64_t total) {
  if (!ctx) {
    return;
  }

  if (ctx->multi_stage) {
    // Continue the job total across stages
    ctx->job_done += ctx->done_bytes;

    // The estimate stands until a stage proves it too small
    if (ctx->job_done + total > ctx->job_total) {
      ctx->job_total = ctx->job_done + total;
    }
  }

  ctx->total_bytes = total;
  ctx->done_bytes = 0;
  ctx->last_reported = 0;
  ctx->stage_base = 0;

  // Paced over the whole job, so a two-stage pipeline reports at the same rate
  // as a single-stage one
  uint64_t pace = ctx->multi_stage ? ctx->job_total : total;
  uint64_t interval = pace / CTX_TARGET_REPORTS;
  ctx->report_interval =
      interval > CTX_MIN_INTERVAL ? interval : CTX_MIN_INTERVAL;
}

// Shared tail of ctx_advance and ctx_set_position: done_bytes is already up to
// date, so check for cancellation and decide whether to report
static int ctx_check(CoreContext *ctx) {
  // Overshoot check
  if (ctx->total_bytes && ctx->done_bytes > ctx->total_bytes) {
    ctx->done_bytes = ctx->total_bytes;
  }

  // Checked every chunk, so cancellation lands within one chunk
  if (ctx->cancel_flag && *ctx->cancel_flag) {
    return COMP_CANCELLED;
  }

  if (!ctx->on_progress ||
      ctx->done_bytes - ctx->last_reported < ctx->report_interval) {
    return 0;
  }

  ctx->last_reported = ctx->done_bytes;

  if (ctx->multi_stage) {
    return ctx->on_progress(ctx, ctx->job_done + ctx->done_bytes,
                            ctx->job_total);
  }
  return ctx->on_progress(ctx, ctx->done_bytes, ctx->total_bytes);
}

void ctx_begin_stage_stream(CoreContext *ctx, FILE *input) {
  if (!ctx) {
    return;
  }

  int64_t size = input ? fs_stream_size(input) : -1;
  // How far the caller has already read (a header, typically); the stage covers
  // only what is left, and ctx_set_position subtracts it back off
  int64_t base = input ? fs_ftell(input) : -1;

  if (size < 0 || base < 0 || size <= base) {
    ctx_begin_stage(ctx, 0);
    return;
  }

  ctx_begin_stage(ctx, (uint64_t)(size - base));
  ctx->stage_base = (uint64_t)base;
}

int ctx_advance(CoreContext *ctx, size_t n) {
  if (!ctx) {
    return 0;
  }

  ctx->done_bytes += n;
  return ctx_check(ctx);
}

int ctx_set_position(CoreContext *ctx, uint64_t done) {
  if (!ctx) {
    return 0;
  }

  // For decoders that read through a library (BZ2_bzRead, lzma_stream) and have
  // no per-chunk delta to report, so pass an absolute ftello() instead; never
  // moves backwards, as a library may buffer ahead and then rewind
  uint64_t relative = done > ctx->stage_base ? done - ctx->stage_base : 0;
  if (relative > ctx->done_bytes) {
    ctx->done_bytes = relative;
  }
  return ctx_check(ctx);
}

int codec_finish_file(int err, FILE *input, FILE *output,
                      const char *output_path, const char *failure_message) {
  if (input) {
    fclose(input);
  }

  // Flush and close the output file, preserving its errno if it fails
  int close_errno = 0;
  if (output) {
    if (fflush(output) != 0) {
      close_errno = errno;
    } else if (ferror(output)) {
      close_errno = EIO;
    }
    if (fclose(output) != 0 && !close_errno) {
      close_errno = errno;
    }
  }

  // An earlier failure is the one worth reporting
  if (err == 0 && close_errno) {
    errno = close_errno;
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, output_path);
    err = -1;
  }

  if (err == 0) {
    return 0;
  }

  // Remove partially-written output file
  if (output_path) {
    fs_unlink(output_path);
  }

  // A NULL message means the caller has already set a more specific exception
  // on every path that can fail, so there is no fallback to apply
  if (failure_message && err != COMP_CANCELLED && !PyErr_Occurred()) {
    PyErr_SetString(comp_BackendError, failure_message);
  }

  return err;
}

static void set_output_error(const char *path) {
  if (errno == ENAMETOOLONG) {
    PyErr_Format(PyExc_ValueError, "Output path too long: %s", path);
  } else {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, path);
  }
}

static void report_actual(const OutputTarget *out, const char *path) {
  if (out->actual) {
    snprintf(out->actual, out->actual_size, "%s", path);
  }
}

int output_check(const char *src_path, const OutputTarget *out) {
  char resolved[FS_PATH_MAX];
  int rc = fs_resolve_conflict(out->path, out->overwrite, resolved,
                               sizeof(resolved));
  if (rc < 0) {
    set_output_error(out->path);
    return -1;
  }
  if (rc > 0) {
    report_actual(out, out->path);
    return 1;
  }

  // The commit would replace the input with its own output
  fs_stat src, dst;
  if (out->overwrite == 2 && src_path && fs_stat_path(src_path, &src) == 0 &&
      fs_stat_path(out->path, &dst) == 0 && fs_same_file(&src, &dst)) {
    PyErr_Format(PyExc_ValueError, "Input and output are the same file: %s",
                 out->path);
    return -1;
  }
  return 0;
}

FILE *output_open(const OutputTarget *out, char *temp) {
  static const char SUFFIX[] = ".compresso-XXXXXX";
  const char *slash = fs_last_sep(out->path);
  size_t dir_len = slash ? (size_t)(slash - out->path + 1) : 0;
  if (dir_len + sizeof(SUFFIX) > FS_PATH_MAX) {
    errno = ENAMETOOLONG;
    set_output_error(out->path);
    return NULL;
  }
  memcpy(temp, out->path, dir_len);
  memcpy(temp + dir_len, SUFFIX, sizeof(SUFFIX));

  // Made owner-only while still empty
  FILE *f = fs_mkstemp(temp);
  if (f && out->owner_only && fs_chmod(temp, 0600) != 0) {
    int saved = errno;
    fclose(f);
    fs_unlink(temp);
    errno = saved;
    f = NULL;
  }
  // Sets the destination as filename for error reporting
  if (!f) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, out->path);
  }
  return f;
}

int output_commit(const char *temp, const OutputTarget *out) {
  char actual[FS_PATH_MAX];
  int rc =
      fs_commit_temp(temp, out->path, out->overwrite, actual, sizeof(actual));
  if (rc == 0) {
    report_actual(out, actual);
    return 0;
  }

  int saved = errno;
  fs_unlink(temp);
  if (rc > 0) { // SKIP, with the destination created since output_check
    report_actual(out, out->path);
    return 0;
  }
  errno = saved;
  set_output_error(out->path);
  return -1;
}

int output_finish(int err, FILE *input, FILE *output, const char *temp,
                  const OutputTarget *out, const char *failure_message) {
  err = codec_finish_file(err, input, output, output ? temp : NULL,
                          failure_message);
  if (err != 0 || !output) {
    return err;
  }
  return output_commit(temp, out);
}

int ctx_finish(CoreContext *ctx) {
  if (!ctx || !ctx->on_progress) {
    return 0;
  }

  // Forced report: the last chunk rarely crosses the throttle threshold, so a
  // job would otherwise stop short of the end; uses total rather than
  // done_bytes, which lags when a decoder stops reading before EOF
  ctx->last_reported = ctx->total_bytes;
  ctx->done_bytes = ctx->total_bytes;

  if (ctx->multi_stage) {
    uint64_t done = ctx->job_done + ctx->done_bytes;
    if (done > ctx->job_total) {
      ctx->job_total = done;
    }
    return ctx->on_progress(ctx, ctx->job_total, ctx->job_total);
  }
  return ctx->on_progress(ctx, ctx->total_bytes, ctx->total_bytes);
}

void ctx_log(CoreContext *ctx, int level, const char *fmt, ...) {
  if (!ctx || !ctx->on_log) {
    return;
  }

  char message[512];
  va_list args;
  va_start(args, fmt);
  vsnprintf(message, sizeof(message), fmt, args);
  va_end(args);

  ctx->on_log(ctx, level, message);
}
