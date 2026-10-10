// Driven by a stub engine rather than a real library, so the driver's own
// behaviour is tested, not zlib's or zstd's

// For fopencookie
#define _GNU_SOURCE

#include "codec/codec.h"
#include "common.h"
#include "files.h"
#include "test_stubs.h"
#include "unity.h"
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define TEST_INPUT FIXTURE_DIR "/alice29.txt"
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
  // Copies members that each end at a '|'; a member starting with '!' is
  // invalid, and one without its '|' never completes
  STUB_MEMBERS,
};

typedef struct {
  int mode;
  int process_calls;
  unsigned char pending; // STUB_EXPAND: byte still being written out
  int pending_left;
  int in_member; // STUB_MEMBERS: past the current member's first byte
} StubState;

// The driver frees the state before a test can read it, so what outlives the
// run is recorded here
static int stub_end_calls;
static int stub_reset_calls;
static int stub_last_finish;
static const char *stub_last_label;
static int stub_calls;
static size_t stub_max_in; // Most input any one process() call was given

static int stub_begin(void *state, const CodecParams *params, int decompress,
                      CoreContext *ctx) {
  (void)decompress;
  (void)ctx;
  StubState *s = (StubState *)state;
  s->mode = params->level;

  return s->mode == STUB_FAIL_BEGIN ? -1 : 0;
}

static int stub_process(void *state, CodecBuf *buf, int finish) {
  StubState *s = (StubState *)state;
  s->process_calls++;
  stub_calls++;
  stub_last_finish = finish;
  if (buf->avail_in > stub_max_in) {
    stub_max_in = buf->avail_in;
  }

  if (s->mode == STUB_FAIL_PROCESS && s->process_calls > 1) {
    return CODEC_ERR;
  }

  if (s->mode == STUB_STALL) {
    return CODEC_MORE; // Consumes nothing and produces nothing, forever
  }

  if (s->mode == STUB_MEMBERS) {
    if (!s->in_member && buf->avail_in > 0 && *buf->next_in == '!') {
      return CODEC_ERR;
    }

    while (buf->avail_in > 0 && buf->avail_out > 0) {
      unsigned char c = *buf->next_in++;
      buf->avail_in--;
      if (c == '|') {
        s->in_member = 0;
        return CODEC_STREAM_END;
      }
      *buf->next_out++ = c;
      buf->avail_out--;
      s->in_member = 1;
    }
    return CODEC_MORE;
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

static int stub_reset(void *state) {
  (void)state;
  stub_reset_calls++;
  return 0;
}

static void stub_end(void *state) {
  (void)state;
  stub_end_calls++;
}

static const char *stub_describe(void *state, const char *label, int decompress,
                                 int *corrupt) {
  (void)state;
  // Blames the input when decoding, as a real engine's data errors would
  *corrupt = decompress;
  stub_last_label = label;
  return "stub engine failed";
}

static const CodecOps stub_ops = {
    .name = "stub",
    .state_size = sizeof(StubState),
    .begin = stub_begin,
    .process = stub_process,
    .reset = stub_reset,
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

  ensure_comp_exceptions();

  stub_end_calls = 0;
  stub_reset_calls = 0;
  stub_last_finish = -1;
  stub_last_label = NULL;
  stub_calls = 0;
  stub_max_in = 0;
  cancel_flag = 0;
  memset(&progress_log, 0, sizeof(progress_log));
  memset(&ctx, 0, sizeof(ctx));
  ctx.on_progress = record_progress;
  ctx.cancel_flag = &cancel_flag;

  PyErr_Clear();
}

void tearDown(void) {
  PyErr_Clear();
  PyRun_SimpleString("import warnings; warnings.resetwarnings()");
  remove(TMP_OUT);
  remove(TMP_IN);
}

// Runs the driver over `input_path` with `level` selecting the stub's mode
static int run_stub(int level, const char *input_path) {
  CodecParams params = {.level = level};
  return codec_run_file(&stub_ops, &params, 0, input_path,
                        OVERWRITE_TO(TMP_OUT), &ctx, "stub run failed");
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

void test_driver_blames_the_engine_for_an_encoding_failure(void) {
  TEST_ASSERT_EQUAL_INT(-1, run_stub(STUB_FAIL_PROCESS, TEST_INPUT));
  TEST_ASSERT_TRUE(PyErr_ExceptionMatches(comp_BackendError));
  TEST_ASSERT_FALSE(PyErr_ExceptionMatches(comp_CorruptDataError));
}

void test_driver_blames_the_input_when_the_engine_does(void) {
  CodecParams params = {.level = STUB_FAIL_PROCESS};

  TEST_ASSERT_EQUAL_INT(-1, codec_run_file(&stub_ops, &params, 1, TEST_INPUT,
                                           OVERWRITE_TO(TMP_OUT), &ctx,
                                           "stub run failed"));
  TEST_ASSERT_TRUE(PyErr_ExceptionMatches(comp_CorruptDataError));
}

void test_driver_unlinks_the_output_on_failure(void) {
  TEST_ASSERT_EQUAL_INT(-1, run_stub(STUB_FAIL_PROCESS, TEST_INPUT));
  TEST_ASSERT_EQUAL_INT(-1, file_size(TMP_OUT));
}

void test_driver_names_the_codec_when_no_label_is_given(void) {
  TEST_ASSERT_EQUAL_INT(-1, run_stub(STUB_FAIL_PROCESS, TEST_INPUT));
  TEST_ASSERT_EQUAL_STRING("stub", stub_last_label);
}

void test_driver_prefers_the_label_over_the_codec_name(void) {
  // What keeps .xz saying "xz" rather than "lzma", and .gz "gzip"
  CodecParams params = {.level = STUB_FAIL_PROCESS, .label = "container"};

  TEST_ASSERT_EQUAL_INT(-1, codec_run_file(&stub_ops, &params, 0, TEST_INPUT,
                                           OVERWRITE_TO(TMP_OUT), &ctx,
                                           "stub run failed"));
  TEST_ASSERT_EQUAL_STRING("container", stub_last_label);
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

// ---- Concatenated members ----

// Decodes `input` with STUB_MEMBERS, allowing concatenation or not
static int run_members(const char *input, int concatenated) {
  write_file(TMP_IN, input);
  CodecParams params = {.level = STUB_MEMBERS, .concatenated = concatenated};
  return codec_run_file(&stub_ops, &params, 1, TMP_IN, OVERWRITE_TO(TMP_OUT),
                        &ctx, "stub run failed");
}

// Whether the pending exception's message contains `needle`; leaves it set
static int error_says(const char *needle) {
#if PY_VERSION_HEX >= 0x030C0000
  PyObject *exc = PyErr_GetRaisedException();
#else
  PyObject *type, *exc, *tb;
  PyErr_Fetch(&type, &exc, &tb);
  PyErr_NormalizeException(&type, &exc, &tb);
#endif
  if (!exc) {
    return 0;
  }
  PyObject *text = PyObject_Str(exc);
  const char *message = text ? PyUnicode_AsUTF8(text) : NULL;
  int found = message && strstr(message, needle) != NULL;
  Py_XDECREF(text);
#if PY_VERSION_HEX >= 0x030C0000
  PyErr_SetRaisedException(exc);
#else
  PyErr_Restore(type, exc, tb);
#endif
  return found;
}

static int output_is(const char *expected) {
  write_file(TMP_IN, expected);
  return files_equal(TMP_IN, TMP_OUT);
}

void test_driver_decodes_every_member(void) {
  TEST_ASSERT_EQUAL_INT(0, run_members("abc|def|ghi|", 1));
  TEST_ASSERT_TRUE(output_is("abcdefghi"));
  TEST_ASSERT_EQUAL_INT(2, stub_reset_calls);
}

void test_driver_ends_cleanly_after_a_single_member(void) {
  TEST_ASSERT_EQUAL_INT(0, run_members("abc|", 1));
  TEST_ASSERT_TRUE(output_is("abc"));
  TEST_ASSERT_EQUAL_INT(0, stub_reset_calls);
}

void test_driver_reads_on_when_a_member_ends_a_chunk(void) {
  // First member fills the read buffer exactly, so the driver has to read again
  static char input[CODEC_CHUNK + 8];
  memset(input, 'a', CODEC_CHUNK - 1);
  strcpy(input + CODEC_CHUNK - 1, "|def|");

  TEST_ASSERT_EQUAL_INT(0, run_members(input, 1));
  TEST_ASSERT_EQUAL_INT(CODEC_CHUNK - 1 + 3, file_size(TMP_OUT));
  TEST_ASSERT_EQUAL_INT(1, stub_reset_calls);
}

void test_driver_ends_cleanly_when_a_member_ends_the_input_on_a_chunk(void) {
  static char input[CODEC_CHUNK + 1];
  memset(input, 'a', CODEC_CHUNK - 1);
  strcpy(input + CODEC_CHUNK - 1, "|");

  TEST_ASSERT_EQUAL_INT(0, run_members(input, 1));
  TEST_ASSERT_EQUAL_INT(CODEC_CHUNK - 1, file_size(TMP_OUT));
  TEST_ASSERT_EQUAL_INT(0, stub_reset_calls);
}

void test_driver_refuses_a_second_member_unless_concatenated(void) {
  TEST_ASSERT_EQUAL_INT(-1, run_members("abc|def|", 0));
  TEST_ASSERT_TRUE(error_says("Invalid data after the end of a stub stream"));
  TEST_ASSERT_TRUE(PyErr_ExceptionMatches(comp_CorruptDataError));
  TEST_ASSERT_EQUAL_INT(0, stub_reset_calls);
  TEST_ASSERT_EQUAL_INT(-1, file_size(TMP_OUT));
}

void test_driver_reports_trailing_data_that_is_not_a_member(void) {
  TEST_ASSERT_EQUAL_INT(-1, run_members("abc|!junk", 1));
  TEST_ASSERT_TRUE(error_says("Invalid data after the end of a stub stream"));
  TEST_ASSERT_TRUE(PyErr_ExceptionMatches(comp_CorruptDataError));
  TEST_ASSERT_EQUAL_INT(-1, file_size(TMP_OUT));
}

void test_driver_reports_a_truncated_later_member(void) {
  TEST_ASSERT_EQUAL_INT(-1, run_members("abc|de", 1));
  TEST_ASSERT_TRUE(error_says("Truncated or incomplete stub stream"));
  TEST_ASSERT_TRUE(PyErr_ExceptionMatches(comp_CorruptDataError));
  TEST_ASSERT_EQUAL_INT(-1, file_size(TMP_OUT));
}

// ---- Ignoring trailing data ----

static int run_members_ignoring(const char *input, int concatenated) {
  write_file(TMP_IN, input);
  CodecParams params = {.level = STUB_MEMBERS,
                        .concatenated = concatenated,
                        .ignore_trailing = 1};
  return codec_run_file(&stub_ops, &params, 1, TMP_IN, OVERWRITE_TO(TMP_OUT),
                        &ctx, "stub run failed");
}

// `action` is a warnings.simplefilter action, e.g. "error" to catch a warning
static void filter_warnings(const char *action) {
  char code[96];
  snprintf(code, sizeof(code), "import warnings; warnings.simplefilter('%s')",
           action);
  TEST_ASSERT_EQUAL_INT(0, PyRun_SimpleString(code));
}

void test_driver_keeps_the_output_before_ignored_trailing_data(void) {
  filter_warnings("ignore");

  TEST_ASSERT_EQUAL_INT(0, run_members_ignoring("abc|def|!junk", 1));
  TEST_ASSERT_TRUE(output_is("abcdef"));
}

void test_driver_warns_with_the_offset_of_ignored_trailing_data(void) {
  filter_warnings("error");

  TEST_ASSERT_EQUAL_INT(-1, run_members_ignoring("abc|def|!junk", 1));
  TEST_ASSERT_TRUE(PyErr_ExceptionMatches(PyExc_UserWarning));
  TEST_ASSERT_TRUE(error_says("from byte 8, after the end of the stub stream"));
  // A warning raised as an error fails the run like any other
  TEST_ASSERT_EQUAL_INT(-1, file_size(TMP_OUT));
}

void test_driver_ignores_a_second_member_unless_concatenated(void) {
  filter_warnings("error");

  TEST_ASSERT_EQUAL_INT(-1, run_members_ignoring("abc|def|", 0));
  TEST_ASSERT_TRUE(error_says("from byte 4"));
  TEST_ASSERT_EQUAL_INT(0, stub_reset_calls);
}

void test_driver_still_fails_a_truncated_member_when_ignoring(void) {
  filter_warnings("error");

  TEST_ASSERT_EQUAL_INT(-1, run_members_ignoring("abc|de", 1));
  TEST_ASSERT_TRUE(error_says("Truncated or incomplete stub stream"));
}

// ---- Streams driven directly ----

static CodecStream *open_writer(FILE **sink) {
  *sink = fopen(TMP_OUT, "wb");
  TEST_ASSERT_NOT_NULL(*sink);
  CodecParams params = {.level = STUB_COPY};
  CodecStream *s = codec_writer_open(&stub_ops, &params, *sink, &ctx);
  TEST_ASSERT_NOT_NULL(s);
  return s;
}

// The sink isn't the stream's to close
static int close_writer(CodecStream *s, int discard, FILE *sink) {
  int rc = codec_writer_close(s, discard);
  fclose(sink);
  return rc;
}

void test_writer_hands_the_engine_whole_chunks(void) {
  FILE *sink;
  CodecStream *s = open_writer(&sink);

  // Pieces that straddle every chunk boundary
  FILE *src = fopen(TEST_INPUT, "rb");
  char piece[1000];
  size_t n;
  while ((n = fread(piece, 1, sizeof(piece), src)) > 0) {
    TEST_ASSERT_EQUAL_INT(0, codec_write(s, piece, n));
  }
  fclose(src);

  TEST_ASSERT_EQUAL_INT(0, close_writer(s, 0, sink));
  TEST_ASSERT_TRUE(files_equal(TEST_INPUT, TMP_OUT));
  // lz4's output bound assumes at most a chunk per call
  TEST_ASSERT_EQUAL_size_t(CODEC_CHUNK, stub_max_in);
}

void test_writer_sends_the_last_data_with_the_finish_flag(void) {
  FILE *sink;
  CodecStream *s = open_writer(&sink);

  TEST_ASSERT_EQUAL_INT(0, codec_write(s, "ab", 2));
  TEST_ASSERT_EQUAL_INT(0, codec_write(s, "cd", 2));
  TEST_ASSERT_EQUAL_INT(0, stub_calls);

  // zstd only records the content size when all the input comes in one call
  TEST_ASSERT_EQUAL_INT(0, close_writer(s, 0, sink));
  TEST_ASSERT_EQUAL_INT(1, stub_calls);
  TEST_ASSERT_EQUAL_INT(1, stub_last_finish);
  TEST_ASSERT_EQUAL_size_t(4, stub_max_in);
}

void test_writer_discards_without_finishing(void) {
  FILE *sink;
  CodecStream *s = open_writer(&sink);

  TEST_ASSERT_EQUAL_INT(0, codec_write(s, "abcd", 4));
  TEST_ASSERT_EQUAL_INT(0, close_writer(s, 1, sink));
  TEST_ASSERT_EQUAL_INT(0, stub_calls);
  TEST_ASSERT_EQUAL_INT(1, stub_end_calls);
  TEST_ASSERT_FALSE(PyErr_Occurred());
}

void test_writer_keeps_returning_a_cancellation(void) {
  FILE *sink;
  CodecStream *s = open_writer(&sink);
  cancel_flag = 1;

  TEST_ASSERT_EQUAL_INT(COMP_CANCELLED, codec_write(s, "ab", 2));
  TEST_ASSERT_EQUAL_INT(COMP_CANCELLED, codec_write(s, "cd", 2));
  TEST_ASSERT_EQUAL_INT(COMP_CANCELLED, close_writer(s, 0, sink));
  TEST_ASSERT_FALSE(PyErr_Occurred());
  TEST_ASSERT_EQUAL_INT(1, stub_end_calls);
}

static CodecStream *open_reader(const char *input, FILE **source) {
  write_file(TMP_IN, input);
  *source = fopen(TMP_IN, "rb");
  TEST_ASSERT_NOT_NULL(*source);
  CodecParams params = {.level = STUB_MEMBERS, .concatenated = 1};
  CodecStream *s = codec_reader_open(&stub_ops, &params, *source, &ctx);
  TEST_ASSERT_NOT_NULL(s);
  return s;
}

void test_reader_serves_small_reads_across_members(void) {
  FILE *source;
  CodecStream *s = open_reader("abc|def|ghi|", &source);

  char out[16] = {0};
  size_t len = 0, got;
  do {
    TEST_ASSERT_EQUAL_INT(0, codec_read(s, out + len, 2, &got));
    TEST_ASSERT_LESS_OR_EQUAL_size_t(2, got);
    len += got;
  } while (got > 0);

  TEST_ASSERT_EQUAL_STRING("abcdefghi", out);
  // The end stays the end
  TEST_ASSERT_EQUAL_INT(0, codec_read(s, out, 2, &got));
  TEST_ASSERT_EQUAL_size_t(0, got);

  TEST_ASSERT_EQUAL_INT(0, codec_reader_close(s));
  fclose(source);
}

void test_reader_raises_its_failure_at_close(void) {
  FILE *source;
  CodecStream *s = open_reader("abc|!junk", &source);

  char out[16];
  size_t got;
  int rc;
  while ((rc = codec_read(s, out, sizeof(out), &got)) == 0 && got > 0) {
  }

  // Read may run without the GIL, so the exception waits for close
  TEST_ASSERT_EQUAL_INT(-1, rc);
  TEST_ASSERT_FALSE(PyErr_Occurred());
  TEST_ASSERT_EQUAL_INT(-1, codec_read(s, out, sizeof(out), &got));

  TEST_ASSERT_EQUAL_INT(-1, codec_reader_close(s));
  TEST_ASSERT_TRUE(error_says("Invalid data after the end of a stub stream"));
  TEST_ASSERT_EQUAL_INT(1, stub_end_calls);
  fclose(source);
}

// ---- Without a context ----

void test_driver_tolerates_a_null_context(void) {
  CodecParams params = {.level = STUB_COPY};

  TEST_ASSERT_EQUAL_INT(0, codec_run_file(&stub_ops, &params, 0, TEST_INPUT,
                                          OVERWRITE_TO(TMP_OUT), NULL,
                                          "stub run failed"));
  TEST_ASSERT_TRUE(files_equal(TEST_INPUT, TMP_OUT));
}

// ---- Closing the output ----

#if defined(__APPLE__)
static int refuse_write(void *cookie, const char *buf, int size) {
  (void)cookie;
  (void)buf;
  (void)size;
  errno = ENOSPC;
  return -1;
}
#else
static ssize_t refuse_write(void *cookie, const char *buf, size_t size) {
  (void)cookie;
  (void)buf;
  (void)size;
  errno = ENOSPC;
  return -1;
}
#endif

// Simulates a full disk stream that fails to flush at close
static FILE *full_disk_stream(void) {
#if defined(__APPLE__)
  return funopen(NULL, NULL, refuse_write, NULL, NULL);
#else
  cookie_io_functions_t io = {.write = refuse_write};
  return fopencookie(NULL, "w", io);
#endif
}

void test_finish_reports_an_output_that_fails_to_flush(void) {
  FILE *out = full_disk_stream();
  TEST_ASSERT_NOT_NULL(out);
  fputs("buffered", out);
  write_file(TMP_OUT, "partial"); // Stands in for the half-written file

  TEST_ASSERT_EQUAL_INT(-1, codec_finish_file(0, NULL, out, TMP_OUT, NULL));
  TEST_ASSERT_TRUE(PyErr_ExceptionMatches(PyExc_OSError));
  TEST_ASSERT_TRUE(error_says("No space left on device"));
  TEST_ASSERT_EQUAL_INT(-1, file_size(TMP_OUT));
}

void test_finish_keeps_an_earlier_failure_over_a_close_failure(void) {
  FILE *out = full_disk_stream();
  TEST_ASSERT_NOT_NULL(out);
  fputs("buffered", out);
  PyErr_SetString(comp_BackendError, "the real failure");

  TEST_ASSERT_EQUAL_INT(-1, codec_finish_file(-1, NULL, out, TMP_OUT, NULL));
  TEST_ASSERT_TRUE(error_says("the real failure"));
}
