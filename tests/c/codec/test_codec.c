// Driven by a stub engine rather than a real library, so the driver's own
// behaviour is tested, not zlib's or zstd's

#include "../../../src/compresso/csrc/codec/codec.h"
#include "../../../src/compresso/csrc/common.h"
#include "../unity.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define TEST_INPUT "../fixtures/alice29.txt"
#define TMP_OUT "tmp_codec_driver.out"
#define TMP_IN "tmp_codec_driver.in"

// The mode is smuggled in through params->level, the one setting the driver
// hands an engine untouched
enum {
  STUB_COPY = 0,
  STUB_FAIL_BEGIN,
  STUB_FAIL_PROCESS,
  STUB_STALL,
  STUB_EXPAND, // Four output bytes per input byte, to force the drain loop
};

typedef struct {
  int mode;
  int process_calls;
  unsigned char pending; // STUB_EXPAND: byte still being written out
  int pending_left;
} StubState;

// The driver frees the state before a test can read it, so what outlives the
// run is recorded here
static int stub_end_calls;
static int stub_last_finish;

static int stub_begin(void *state, const CodecParams *params, int decompress) {
  (void)decompress;
  StubState *s = (StubState *)state;
  s->mode = params->level;

  return s->mode == STUB_FAIL_BEGIN ? -1 : 0;
}

static int stub_process(void *state, CodecBuf *buf, int finish) {
  StubState *s = (StubState *)state;
  s->process_calls++;
  stub_last_finish = finish;

  if (s->mode == STUB_FAIL_PROCESS && s->process_calls > 1) {
    return CODEC_ERR;
  }

  if (s->mode == STUB_STALL) {
    return CODEC_MORE; // Consumes nothing and produces nothing, forever
  }

  if (s->mode == STUB_EXPAND) {
    while (buf->avail_out > 0) {
      if (s->pending_left == 0) {
        if (buf->avail_in == 0) {
          break;
        }
        s->pending = *buf->next_in++;
        buf->avail_in--;
        s->pending_left = 4;
      }

      *buf->next_out++ = s->pending;
      buf->avail_out--;
      s->pending_left--;
    }

    if (finish && buf->avail_in == 0 && s->pending_left == 0) {
      return CODEC_DONE;
    }
    return CODEC_MORE;
  }

  size_t n = buf->avail_in < buf->avail_out ? buf->avail_in : buf->avail_out;
  memcpy(buf->next_out, buf->next_in, n);
  buf->next_in += n;
  buf->avail_in -= n;
  buf->next_out += n;
  buf->avail_out -= n;

  if (finish && buf->avail_in == 0) {
    return CODEC_DONE;
  }
  return CODEC_MORE;
}

static void stub_end(void *state) {
  (void)state;
  stub_end_calls++;
}

static const char *stub_describe(void *state, int decompress) {
  (void)state;
  (void)decompress;
  return "stub engine failed";
}

static const CodecOps stub_ops = {
    .name = "stub",
    .state_size = sizeof(StubState),
    .begin = stub_begin,
    .process = stub_process,
    .end = stub_end,
    .describe = stub_describe,
};

// ---- Progress and cancellation plumbing ----

typedef struct {
  int calls;
  uint64_t last_done, last_total;
  int cancel_after; // Set the cancel flag once this many reports have landed
} ProgressLog;

static ProgressLog progress_log;
static volatile int cancel_flag;

static int record_progress(CoreContext *ctx, uint64_t done, uint64_t total) {
  (void)ctx;
  progress_log.calls++;
  progress_log.last_done = done;
  progress_log.last_total = total;

  if (progress_log.cancel_after &&
      progress_log.calls >= progress_log.cancel_after) {
    cancel_flag = 1;
  }
  return 0;
}

static CoreContext ctx;

void setUp(void) {
  if (!Py_IsInitialized()) {
    Py_Initialize();
  }

  // test_stubs.c leaves these NULL, which the driver would hand to
  // PyErr_SetString as an exception type
  comp_BackendError = PyExc_RuntimeError;
  comp_HeaderError = PyExc_ValueError;
  comp_Error = PyExc_RuntimeError;

  stub_end_calls = 0;
  stub_last_finish = -1;
  cancel_flag = 0;
  memset(&progress_log, 0, sizeof(progress_log));
  memset(&ctx, 0, sizeof(ctx));
  ctx.on_progress = record_progress;
  ctx.cancel_flag = &cancel_flag;

  PyErr_Clear();
}

void tearDown(void) {
  PyErr_Clear();
  remove(TMP_OUT);
  remove(TMP_IN);
}

static int files_equal(const char *a, const char *b) {
  FILE *fa = fopen(a, "rb");
  FILE *fb = fopen(b, "rb");
  if (!fa || !fb) {
    if (fa) {
      fclose(fa);
    }
    if (fb) {
      fclose(fb);
    }
    return 0;
  }

  int equal = 1;
  for (;;) {
    int ca = fgetc(fa);
    int cb = fgetc(fb);
    if (ca != cb) {
      equal = 0;
      break;
    }
    if (ca == EOF) {
      break;
    }
  }

  fclose(fa);
  fclose(fb);
  return equal;
}

static void write_file(const char *path, const char *contents) {
  FILE *f = fopen(path, "wb");
  TEST_ASSERT_NOT_NULL(f);
  if (contents[0] != '\0') {
    fputs(contents, f);
  }
  fclose(f);
}

static long file_size(const char *path) {
  FILE *f = fopen(path, "rb");
  if (!f) {
    return -1;
  }
  fseek(f, 0, SEEK_END);
  long size = ftell(f);
  fclose(f);
  return size;
}

// Runs the driver over `input_path` with `level` selecting the stub's mode
static int run_stub(int level, const char *input_path) {
  CodecParams params = {.level = level};
  return codec_run_file(&stub_ops, &params, 0, input_path, TMP_OUT, &ctx,
                        "stub run failed");
}

// As run_stub, but against the stream entry point with a reporting interval
// small enough to fire: ctx_begin_stage floors it at 1 MiB, which no fixture
// comes close to
static int run_stub_stream(int level, const char *input_path) {
  FILE *src = fopen(input_path, "rb");
  TEST_ASSERT_NOT_NULL(src);
  FILE *dst = fopen(TMP_OUT, "wb");
  TEST_ASSERT_NOT_NULL(dst);

  ctx_begin_stage(&ctx, (uint64_t)file_size(input_path));
  ctx.report_interval = 1024;

  CodecParams params = {.level = level};
  int rc = codec_run_stream(&stub_ops, &params, 0, src, dst, &ctx);

  fclose(src);
  fclose(dst);
  return rc;
}

// ---- Copying ----

void test_driver_copies_a_multi_chunk_stream(void) {
  // alice29.txt is 152 KB, so the driver refills its 64 KB buffer twice
  TEST_ASSERT_EQUAL_INT(0, run_stub(STUB_COPY, TEST_INPUT));
  TEST_ASSERT_TRUE(files_equal(TEST_INPUT, TMP_OUT));
  TEST_ASSERT_EQUAL_INT(1, stub_end_calls);
}

void test_driver_handles_empty_input(void) {
  write_file(TMP_IN, "");

  TEST_ASSERT_EQUAL_INT(0, run_stub(STUB_COPY, TMP_IN));
  TEST_ASSERT_EQUAL_INT(0, file_size(TMP_OUT));
  // The flush signal still has to reach the engine, or a real codec would
  // never write its header
  TEST_ASSERT_EQUAL_INT(1, stub_last_finish);
}

void test_driver_drains_a_full_output_buffer(void) {
  write_file(TMP_IN, "abcd");

  TEST_ASSERT_EQUAL_INT(0, run_stub(STUB_EXPAND, TMP_IN));
  TEST_ASSERT_EQUAL_INT(16, file_size(TMP_OUT));
}

// ---- Progress and cancellation ----

void test_driver_reports_progress_over_the_whole_input(void) {
  TEST_ASSERT_EQUAL_INT(0, run_stub_stream(STUB_COPY, TEST_INPUT));

  TEST_ASSERT_GREATER_THAN_INT(0, progress_log.calls);
  TEST_ASSERT_EQUAL_UINT64((uint64_t)file_size(TEST_INPUT),
                           progress_log.last_total);
  TEST_ASSERT_EQUAL_UINT64(progress_log.last_total, progress_log.last_done);
}

void test_driver_returns_cancelled_not_failed(void) {
  // The flag is set by the first report, so it lands on the next chunk
  progress_log.cancel_after = 1;

  // Distinct from -1, so codec_finish_file leaves the exception unset
  TEST_ASSERT_EQUAL_INT(COMP_CANCELLED, run_stub_stream(STUB_COPY, TEST_INPUT));
  TEST_ASSERT_FALSE(PyErr_Occurred());
  TEST_ASSERT_EQUAL_INT(1, stub_end_calls);
}

void test_driver_unlinks_the_output_on_cancellation(void) {
  cancel_flag = 1; // Cancelled before the first chunk is accounted for

  TEST_ASSERT_EQUAL_INT(COMP_CANCELLED, run_stub(STUB_COPY, TEST_INPUT));
  TEST_ASSERT_EQUAL_INT(-1, file_size(TMP_OUT));
}

// ---- Failure reporting ----

void test_driver_reports_a_begin_failure(void) {
  TEST_ASSERT_EQUAL_INT(-1, run_stub(STUB_FAIL_BEGIN, TEST_INPUT));
  TEST_ASSERT_TRUE(PyErr_Occurred());
  TEST_ASSERT_EQUAL_INT(1, stub_end_calls);
}

void test_driver_reports_a_process_failure(void) {
  TEST_ASSERT_EQUAL_INT(-1, run_stub(STUB_FAIL_PROCESS, TEST_INPUT));
  TEST_ASSERT_TRUE(PyErr_Occurred());
  TEST_ASSERT_EQUAL_INT(1, stub_end_calls);
}

void test_driver_unlinks_the_output_on_failure(void) {
  TEST_ASSERT_EQUAL_INT(-1, run_stub(STUB_FAIL_PROCESS, TEST_INPUT));
  TEST_ASSERT_EQUAL_INT(-1, file_size(TMP_OUT));
}

void test_driver_stops_on_a_stalled_engine(void) {
  write_file(TMP_IN, "abcd");

  // An engine that makes no progress must be an error rather than a hang
  TEST_ASSERT_EQUAL_INT(-1, run_stub(STUB_STALL, TMP_IN));
  TEST_ASSERT_TRUE(PyErr_Occurred());
}

void test_driver_reports_a_missing_input(void) {
  TEST_ASSERT_EQUAL_INT(-1, run_stub(STUB_COPY, "does_not_exist.bin"));
  TEST_ASSERT_TRUE(PyErr_Occurred());
  // begin() never ran, so neither did end()
  TEST_ASSERT_EQUAL_INT(0, stub_end_calls);
}

// ---- Without a context ----

void test_driver_tolerates_a_null_context(void) {
  CodecParams params = {.level = STUB_COPY};

  TEST_ASSERT_EQUAL_INT(0, codec_run_file(&stub_ops, &params, 0, TEST_INPUT,
                                          TMP_OUT, NULL, "stub run failed"));
  TEST_ASSERT_TRUE(files_equal(TEST_INPUT, TMP_OUT));
}
