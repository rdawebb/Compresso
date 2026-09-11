#define PY_SSIZE_T_CLEAN
#include "common.h"
#include "fsutil.h"
#include <Python.h>
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

void ctx_begin_stage(CoreContext *ctx, uint64_t total) {
  if (!ctx) {
    return;
  }

  ctx->total_bytes = total;
  ctx->done_bytes = 0;
  ctx->last_reported = 0;
  ctx->stage_base = 0;

  uint64_t interval = total / CTX_TARGET_REPORTS;
  ctx->report_interval =
      interval > CTX_MIN_INTERVAL ? interval : CTX_MIN_INTERVAL;
}

// Shared tail of ctx_advance and ctx_set_position: done_bytes is already
// up to date, so check for cancellation and decide whether to report
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
  if (output) {
    fclose(output);
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

int ctx_finish(CoreContext *ctx) {
  if (!ctx || !ctx->on_progress) {
    return 0;
  }

  // Forced report: the last chunk rarely crosses the throttle threshold, so a
  // job would otherwise stop short of the end; uses total rather than
  // done_bytes, which lags when a decoder stops reading before EOF
  ctx->last_reported = ctx->total_bytes;
  ctx->done_bytes = ctx->total_bytes;
  return ctx->on_progress(ctx, ctx->total_bytes, ctx->total_bytes);
}
