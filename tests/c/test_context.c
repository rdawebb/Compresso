#include "../../src/compresso/csrc/common.h"
#include "unity.h"
#include <string.h>

// Records what the progress callback was handed, so the tests can assert on
// how often it fired and with what
typedef struct {
  int calls;
  uint64_t last_done, last_total;
  int return_value; // What the callback reports back to ctx_advance
} ProgressLog;

static ProgressLog log_state;

static int record_progress(CoreContext *ctx, uint64_t done, uint64_t total) {
  (void)ctx;
  log_state.calls++;
  log_state.last_done = done;
  log_state.last_total = total;
  return log_state.return_value;
}

static CoreContext ctx;
static volatile int cancel_flag;

void setUp(void) {
  memset(&log_state, 0, sizeof(log_state));
  memset(&ctx, 0, sizeof(ctx));
  cancel_flag = 0;
  ctx.on_progress = record_progress;
  ctx.cancel_flag = &cancel_flag;
}

void tearDown(void) {}

// ---- ctx_begin_stage ----

void test_begin_stage_sets_total_and_clears_counters(void) {
  ctx.done_bytes = 999;
  ctx.last_reported = 999;

  ctx_begin_stage(&ctx, 100 * 1024 * 1024);

  TEST_ASSERT_EQUAL_UINT64(100 * 1024 * 1024, ctx.total_bytes);
  TEST_ASSERT_EQUAL_UINT64(0, ctx.done_bytes);
  TEST_ASSERT_EQUAL_UINT64(0, ctx.last_reported);
}

void test_begin_stage_targets_200_reports(void) {
  // 400 MiB / 200 = 2 MiB, comfortably above the 1 MiB floor
  ctx_begin_stage(&ctx, 400ULL * 1024 * 1024);
  TEST_ASSERT_EQUAL_UINT64(2ULL * 1024 * 1024, ctx.report_interval);
}

void test_begin_stage_applies_minimum_interval(void) {
  // 10 MiB / 200 = 51.2 KiB, so the 1 MiB floor takes over
  ctx_begin_stage(&ctx, 10ULL * 1024 * 1024);
  TEST_ASSERT_EQUAL_UINT64(1024 * 1024, ctx.report_interval);
}

void test_begin_stage_tolerates_zero_total(void) {
  ctx_begin_stage(&ctx, 0);
  TEST_ASSERT_EQUAL_UINT64(0, ctx.total_bytes);
  TEST_ASSERT_EQUAL_UINT64(1024 * 1024, ctx.report_interval);
}

// ---- ctx_advance ----

void test_advance_accumulates_without_reporting(void) {
  ctx_begin_stage(&ctx, 400ULL * 1024 * 1024); // 2 MiB interval

  TEST_ASSERT_EQUAL_INT(0, ctx_advance(&ctx, 1024 * 1024));

  TEST_ASSERT_EQUAL_UINT64(1024 * 1024, ctx.done_bytes);
  TEST_ASSERT_EQUAL_INT(0, log_state.calls); // Below the threshold
}

void test_advance_reports_once_threshold_crossed(void) {
  ctx_begin_stage(&ctx, 400ULL * 1024 * 1024); // 2 MiB interval

  ctx_advance(&ctx, 1024 * 1024);
  TEST_ASSERT_EQUAL_INT(0, log_state.calls);

  ctx_advance(&ctx, 1024 * 1024);
  TEST_ASSERT_EQUAL_INT(1, log_state.calls);
  TEST_ASSERT_EQUAL_UINT64(2ULL * 1024 * 1024, log_state.last_done);
  TEST_ASSERT_EQUAL_UINT64(400ULL * 1024 * 1024, log_state.last_total);
}

void test_advance_throttles_across_many_chunks(void) {
  ctx_begin_stage(&ctx, 400ULL * 1024 * 1024); // 2 MiB interval

  // 400 MiB in 64 KiB chunks: 6400 chunks, but only ~200 reports
  for (int i = 0; i < 6400; i++) {
    TEST_ASSERT_EQUAL_INT(0, ctx_advance(&ctx, 65536));
  }

  TEST_ASSERT_EQUAL_INT(200, log_state.calls);
}

void test_advance_propagates_callback_failure(void) {
  ctx_begin_stage(&ctx, 400ULL * 1024 * 1024);
  log_state.return_value = -1;

  TEST_ASSERT_EQUAL_INT(-1, ctx_advance(&ctx, 4ULL * 1024 * 1024));
}

void test_advance_without_callback_still_accumulates(void) {
  ctx.on_progress = NULL;
  ctx_begin_stage(&ctx, 400ULL * 1024 * 1024);

  TEST_ASSERT_EQUAL_INT(0, ctx_advance(&ctx, 4ULL * 1024 * 1024));
  TEST_ASSERT_EQUAL_UINT64(4ULL * 1024 * 1024, ctx.done_bytes);
}

// ---- Cancellation ----

void test_advance_returns_cancelled_when_flag_set(void) {
  ctx_begin_stage(&ctx, 400ULL * 1024 * 1024);
  cancel_flag = 1;

  TEST_ASSERT_EQUAL_INT(COMP_CANCELLED, ctx_advance(&ctx, 1));
}

void test_cancel_checked_every_chunk_not_every_report(void) {
  ctx_begin_stage(&ctx, 400ULL * 1024 * 1024); // 2 MiB interval
  cancel_flag = 1;

  // A single byte is below the report threshold, yet cancellation still lands
  TEST_ASSERT_EQUAL_INT(COMP_CANCELLED, ctx_advance(&ctx, 1));
  TEST_ASSERT_EQUAL_INT(0, log_state.calls);
}

void test_cancel_takes_precedence_over_reporting(void) {
  ctx_begin_stage(&ctx, 400ULL * 1024 * 1024);
  cancel_flag = 1;

  // Enough bytes to cross the threshold, but cancellation wins and the
  // callback is never invoked for a run that is being abandoned
  TEST_ASSERT_EQUAL_INT(COMP_CANCELLED, ctx_advance(&ctx, 4ULL * 1024 * 1024));
  TEST_ASSERT_EQUAL_INT(0, log_state.calls);
}

void test_null_cancel_flag_never_cancels(void) {
  ctx.cancel_flag = NULL;
  ctx_begin_stage(&ctx, 400ULL * 1024 * 1024);

  TEST_ASSERT_EQUAL_INT(0, ctx_advance(&ctx, 1024));
}

// ---- ctx_set_position ----

void test_set_position_tracks_absolute_offset(void) {
  ctx_begin_stage(&ctx, 400ULL * 1024 * 1024);

  ctx_set_position(&ctx, 4ULL * 1024 * 1024);

  TEST_ASSERT_EQUAL_UINT64(4ULL * 1024 * 1024, ctx.done_bytes);
  TEST_ASSERT_EQUAL_INT(1, log_state.calls);
}

void test_set_position_never_moves_backwards(void) {
  ctx_begin_stage(&ctx, 400ULL * 1024 * 1024);
  ctx_set_position(&ctx, 8ULL * 1024 * 1024);

  // A library that buffered ahead and then rewound must not make a progress
  // bar jump back
  ctx_set_position(&ctx, 1024);

  TEST_ASSERT_EQUAL_UINT64(8ULL * 1024 * 1024, ctx.done_bytes);
}

void test_set_position_returns_cancelled_when_flag_set(void) {
  ctx_begin_stage(&ctx, 400ULL * 1024 * 1024);
  cancel_flag = 1;

  TEST_ASSERT_EQUAL_INT(COMP_CANCELLED, ctx_set_position(&ctx, 1024));
}

void test_set_position_subtracts_the_stage_base(void) {
  // A stage that starts past a header: the codec reports absolute file
  // offsets, but the total covers only the payload after it
  ctx_begin_stage(&ctx, 400ULL * 1024 * 1024);
  ctx.stage_base = 16;

  ctx_set_position(&ctx, 16 + 4ULL * 1024 * 1024);

  TEST_ASSERT_EQUAL_UINT64(4ULL * 1024 * 1024, ctx.done_bytes);
}

void test_set_position_inside_the_base_reports_zero(void) {
  ctx_begin_stage(&ctx, 400ULL * 1024 * 1024);
  ctx.stage_base = 16;

  ctx_set_position(&ctx, 8); // Still within the header
  TEST_ASSERT_EQUAL_UINT64(0, ctx.done_bytes);
}

// ---- Overshoot clamping ----

void test_done_never_exceeds_total(void) {
  // Codecs can overshoot slightly: a library reading ahead, or framing bytes
  // counted alongside payload
  ctx_begin_stage(&ctx, 4ULL * 1024 * 1024);

  ctx_advance(&ctx, 8ULL * 1024 * 1024);

  TEST_ASSERT_EQUAL_UINT64(4ULL * 1024 * 1024, ctx.done_bytes);
  TEST_ASSERT_EQUAL_UINT64(4ULL * 1024 * 1024, log_state.last_done);
}

void test_unknown_total_is_left_unclamped(void) {
  // 0 total means unmeasurable (e.g. a pipe), so the counter must still climb
  ctx_begin_stage(&ctx, 0);

  ctx_advance(&ctx, 4ULL * 1024 * 1024);

  TEST_ASSERT_EQUAL_UINT64(4ULL * 1024 * 1024, ctx.done_bytes);
}

// ---- ctx_finish ----

void test_finish_reports_one_hundred_percent(void) {
  ctx_begin_stage(&ctx, 400ULL * 1024 * 1024);
  ctx_advance(&ctx, 1024); // Far short of the total, and below the threshold

  TEST_ASSERT_EQUAL_INT(0, ctx_finish(&ctx));

  // The last thing a caller observes is always done == total
  TEST_ASSERT_EQUAL_INT(1, log_state.calls);
  TEST_ASSERT_EQUAL_UINT64(400ULL * 1024 * 1024, log_state.last_done);
  TEST_ASSERT_EQUAL_UINT64(400ULL * 1024 * 1024, log_state.last_total);
}

void test_finish_without_callback_is_a_noop(void) {
  ctx.on_progress = NULL;
  ctx_begin_stage(&ctx, 400ULL * 1024 * 1024);

  TEST_ASSERT_EQUAL_INT(0, ctx_finish(&ctx));
}

// ---- NULL context ----

void test_null_context_advance_is_a_noop(void) {
  TEST_ASSERT_EQUAL_INT(0, ctx_advance(NULL, 4096));
}

void test_null_context_set_position_is_a_noop(void) {
  TEST_ASSERT_EQUAL_INT(0, ctx_set_position(NULL, 4096));
}

void test_null_context_finish_is_a_noop(void) {
  TEST_ASSERT_EQUAL_INT(0, ctx_finish(NULL));
}

void test_null_context_begin_stage_does_not_crash(void) {
  ctx_begin_stage(NULL, 1234);
  TEST_ASSERT_EQUAL_INT(0, log_state.calls);
}
