#ifndef CONTEXT_H
#define CONTEXT_H

#include <stdint.h>
#include <stdio.h>

// ---- Progress and Cancellation ----

// Distinct from -1 so callers can tell cancelled from failed
#define COMP_CANCELLED (-2)

// Per-call progress and cancellation state; every field is optional, and a
// NULL CoreContext behaves as no context
typedef struct CoreContext {
  // Owned by a Python CancelToken, which outlives this stack-local context and
  // may be cancelled from another thread; NULL = uncancellable
  volatile int *cancel_flag;

  // Returns 0 to continue, non-zero to abort
  int (*on_progress)(struct CoreContext *ctx, uint64_t done, uint64_t total);
  void *userdata; // PyObject* callback, owned by the _core.c bridge

  uint64_t total_bytes, done_bytes;
  uint64_t report_interval, last_reported;

  // Input offset this stage began at, subtracted from absolute positions
  // passed to ctx_set_position; non-zero when the caller has already consumed
  // a header
  uint64_t stage_base;

  // Multi-stage accounting; reports are cumulative: job_done + done_bytes, out
  // of job_total (job_total is an estimate up front and grows as each stage
  // declares its total, so the reported total may rise during a job)
  int multi_stage;
  uint64_t job_total, job_done;
} CoreContext;

// Begins a job whose input is read in more than one stage, making reports
// cumulative rather than per-stage
void ctx_begin_job(CoreContext *ctx, uint64_t estimated_total);

// Starts a new stage; within a multi-stage job this banks the previous stage's
// bytes, so progress carries forward instead of restarting
void ctx_begin_stage(CoreContext *ctx, uint64_t total);

// ctx_begin_stage with the total taken from an open input stream, measured from
// its current position to the end; an unmeasurable stream (a pipe) gets a
// total of 0 rather than failing
void ctx_begin_stage_stream(CoreContext *ctx, FILE *input);

// Records `n` more input bytes consumed: returns 0 to continue, COMP_CANCELLED
// if the cancel flag is set, or -1 if the progress callback itself failed
int ctx_advance(CoreContext *ctx, size_t n);

// As ctx_advance, but takes an absolute input position instead of a delta, for
// decoders that read through a library and cannot report per-chunk deltas
int ctx_set_position(CoreContext *ctx, uint64_t done);

// Emits one final report at 100%; called after a successful run
int ctx_finish(CoreContext *ctx);

#endif // CONTEXT_H
