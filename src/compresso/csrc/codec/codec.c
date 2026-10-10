#define PY_SSIZE_T_CLEAN
#include "codec.h"
#include "../common.h"
#include "../fsutil.h"
#include <Python.h>
#include <stdlib.h>
#include <string.h>

// Where a failed run failed, so the message is chosen with the GIL back
typedef enum {
  FAIL_NONE = 0,
  FAIL_READ,
  FAIL_WRITE,
  FAIL_CODEC,
  FAIL_TRUNCATED,
  FAIL_TRAILING,
  FAIL_TOO_LONG,
  FAIL_TOO_SHORT,
} FailKind;

static const char *codec_label(const CodecOps *ops, const CodecParams *params) {
  return (params && params->label) ? params->label : ops->name;
}

static void set_failure(const CodecOps *ops, const CodecParams *params,
                        void *state, FailKind kind, int decompress) {
  if (PyErr_Occurred()) {
    return; // An engine that raised its own is more specific
  }

  switch (kind) {
  case FAIL_READ:
    PyErr_SetString(PyExc_IOError, "Error reading input file");
    return;
  case FAIL_WRITE:
    PyErr_SetString(PyExc_IOError, "Error writing output file");
    return;
  case FAIL_TRUNCATED:
    PyErr_Format(decompress ? comp_CorruptDataError : comp_BackendError,
                 "Truncated or incomplete %s stream", codec_label(ops, params));
    return;
  case FAIL_TRAILING:
    PyErr_Format(comp_CorruptDataError,
                 "Invalid data after the end of a %s stream",
                 codec_label(ops, params));
    return;
  case FAIL_TOO_LONG:
    PyErr_Format(comp_CorruptDataError,
                 "Decoded %s data exceeds its recorded size of %llu bytes",
                 codec_label(ops, params),
                 (unsigned long long)params->orig_size);
    return;
  case FAIL_TOO_SHORT:
    PyErr_Format(comp_CorruptDataError,
                 "Decoded %s data is shorter than its recorded size of %llu "
                 "bytes",
                 codec_label(ops, params),
                 (unsigned long long)params->orig_size);
    return;
  case FAIL_CODEC: {
    int corrupt = 0;
    const char *message = ops->describe
                              ? ops->describe(state, codec_label(ops, params),
                                              decompress, &corrupt)
                              : NULL;
    if (message) {
      PyErr_SetString(corrupt ? comp_CorruptDataError : comp_BackendError,
                      message);
    }
    return; // No describe: the caller applies its own fallback
  }
  case FAIL_NONE:
    return;
  }
}

struct CodecStream {
  const CodecOps *ops;
  CodecParams params;
  void *state;
  int decompress;
  FILE *file; // The writer's sink, or the reader's source
  CoreContext *ctx;

  // Writer gathers pushes into whole blocks: lz4's output buffer is sized for
  // max CODEC_CHUNK; zstd needs a finish flag with the last block
  unsigned char *in_buf;
  unsigned char *out_buf;
  size_t out_size;
  CodecBuf buf;

  int err; // 0, -1 or COMP_CANCELLED
  FailKind fail;
  int finish;
  int done;

  // Reader: decoded bytes in out_buf not yet handed out
  size_t out_pos;
  size_t out_len;
  // Reader: the last call filled out_buf, so drain the engine before reading
  int draining;
  // Reader: the stall check's tallies, kept across the calls a drain spans
  size_t pass_before_in;
  size_t pass_produced;

  // Reader: set once a member ends, until the input left over is resolved
  int at_boundary;
  int members;
  size_t member_out;
  uint64_t total_out;
  uint64_t read_total;
  uint64_t member_start; // Input offset of the member being decoded
  int trailing_ignored;
};

static int stream_fail(CodecStream *s, FailKind fail) {
  s->err = -1;
  s->fail = fail;
  return -1;
}

static int stream_close(CodecStream *s) {
  int err = s->err;
  // Before end(), which releases what describe() reads from
  if (err == -1) {
    set_failure(s->ops, &s->params, s->state, s->fail, s->decompress);
  }

  s->ops->end(s->state);

  free(s->state);
  free(s->in_buf);
  free(s->out_buf);
  free(s);
  return err;
}

static CodecStream *stream_open(const CodecOps *ops, const CodecParams *params,
                                int decompress, FILE *file, CoreContext *ctx) {
  CodecStream *s = (CodecStream *)calloc(1, sizeof(*s));
  if (!s) {
    PyErr_NoMemory();
    return NULL;
  }
  s->ops = ops;
  s->params = *params;
  s->decompress = decompress;
  s->file = file;
  s->ctx = ctx;
  s->out_size = ops->out_chunk ? ops->out_chunk : CODEC_CHUNK;

  // A stateless engine is allowed, so a NULL state isn't a failure by itself
  if (ops->state_size && !(s->state = calloc(1, ops->state_size))) {
    PyErr_NoMemory();
    free(s);
    return NULL;
  }
  s->in_buf = (unsigned char *)safe_malloc(CODEC_CHUNK);
  s->out_buf = (unsigned char *)safe_malloc(s->out_size);
  if (!s->in_buf || !s->out_buf) {
    free(s->state);
    free(s->in_buf);
    free(s->out_buf);
    free(s);
    return NULL;
  }

  if (ops->begin(s->state, &s->params, decompress, ctx) != 0) {
    stream_fail(s, FAIL_CODEC);
    stream_close(s);
    return NULL;
  }
  return s;
}

// ---- Writer ----

CodecStream *codec_writer_open(const CodecOps *ops, const CodecParams *params,
                               FILE *sink, CoreContext *ctx) {
  return stream_open(ops, params, 0, sink, ctx);
}

// Feeds the gathered block to the engine; with `finish`, until the stream ends
static int writer_pump(CodecStream *s, int finish) {
  s->buf.next_in = s->in_buf;
  if (s->done) {
    s->buf.avail_in = 0; // The engine has ended, so later input is dropped
    return 0;
  }

  for (;;) {
    size_t before_in = s->buf.avail_in;
    size_t produced_pass = 0;

    // Full buffer means the engine may have more waiting
    do {
      s->buf.next_out = s->out_buf;
      s->buf.avail_out = s->out_size;

      int status = s->ops->process(s->state, &s->buf, finish);
      if (status == CODEC_ERR) {
        return stream_fail(s, FAIL_CODEC);
      }

      size_t produced = s->out_size - s->buf.avail_out;
      produced_pass += produced;
      if (produced > 0 &&
          (fwrite(s->out_buf, 1, produced, s->file) != produced ||
           ferror(s->file))) {
        return stream_fail(s, FAIL_WRITE);
      }

      if (status == CODEC_DONE) {
        s->done = 1;
        return 0;
      }
    } while (s->buf.avail_out == 0);

    if (!finish && s->buf.avail_in == 0) {
      return 0;
    }

    // The input ran out before the stream ended, or the engine stalled
    if (s->buf.avail_in == before_in && produced_pass == 0) {
      return stream_fail(s, finish ? FAIL_TRUNCATED : FAIL_CODEC);
    }
  }
}

int codec_write(CodecStream *s, const void *data, size_t n) {
  if (s->err) {
    return s->err;
  }

  int advance = ctx_advance(s->ctx, n);
  if (advance != 0) {
    s->err = advance;
    return advance;
  }

  const unsigned char *p = (const unsigned char *)data;
  while (n > 0) {
    size_t room = CODEC_CHUNK - s->buf.avail_in;
    size_t take = n < room ? n : room;
    memcpy(s->in_buf + s->buf.avail_in, p, take);
    s->buf.avail_in += take;
    p += take;
    n -= take;

    if (s->buf.avail_in == CODEC_CHUNK && writer_pump(s, 0) != 0) {
      return s->err;
    }
  }
  return 0;
}

int codec_writer_close(CodecStream *s, int discard) {
  if (!s->err && !discard) {
    Py_BEGIN_ALLOW_THREADS
    writer_pump(s, 1);
    Py_END_ALLOW_THREADS
  }
  return stream_close(s);
}

// ---- Reader ----

CodecStream *codec_reader_open(const CodecOps *ops, const CodecParams *params,
                               FILE *source, CoreContext *ctx) {
  return stream_open(ops, params, 1, source, ctx);
}

static int reader_end(CodecStream *s) {
  s->done = 1;
  if (s->params.exact_size && s->total_out != s->params.orig_size) {
    return stream_fail(s, FAIL_TOO_SHORT);
  }
  return 0;
}

// Runs the engine into out_buf until it produces something or the stream ends
static int reader_fill(CodecStream *s) {
  const CodecParams *params = &s->params;
  s->out_pos = s->out_len = 0;

  while (!s->done) {
    if (!s->draining) {
      if (s->buf.avail_in == 0 && !s->finish) {
        size_t nread = fread(s->in_buf, 1, CODEC_CHUNK, s->file);
        if (ferror(s->file)) {
          return stream_fail(s, FAIL_READ);
        }

        s->buf.next_in = s->in_buf;
        s->buf.avail_in = nread;
        s->read_total += nread;
        if (feof(s->file)) {
          s->finish = 1;
        }

        int advance = ctx_advance(s->ctx, nread);
        if (advance != 0) {
          s->err = advance;
          return advance;
        }
      }

      if (s->at_boundary) {
        if (s->buf.avail_in == 0) {
          // Only reachable at end of input: the last member was complete
          return reader_end(s);
        }
        s->member_start = s->read_total - s->buf.avail_in;
        if (!params->concatenated || !s->ops->reset) {
          if (!params->ignore_trailing) {
            return stream_fail(s, FAIL_TRAILING);
          }
          s->trailing_ignored = 1;
          return reader_end(s);
        }
        if (s->ops->reset(s->state) != 0) {
          return stream_fail(s, FAIL_CODEC);
        }
        s->at_boundary = 0;
        s->member_out = 0;
      }

      s->pass_before_in = s->buf.avail_in;
      s->pass_produced = 0;
    }

    s->buf.next_out = s->out_buf;
    s->buf.avail_out = s->out_size;

    int status = s->ops->process(s->state, &s->buf, s->finish);
    if (status == CODEC_ERR) {
      // Failing before producing anything is a trailing error
      int trailing = s->members > 0 && s->member_out == 0;
      if (trailing && params->ignore_trailing) {
        s->trailing_ignored = 1;
        return reader_end(s);
      }
      return stream_fail(s, trailing ? FAIL_TRAILING : FAIL_CODEC);
    }

    size_t produced = s->out_size - s->buf.avail_out;
    s->pass_produced += produced;
    s->member_out += produced;

    // Caught before it is handed out, so a bomb stops at its claimed size
    if (params->exact_size && produced > params->orig_size - s->total_out) {
      return stream_fail(s, FAIL_TOO_LONG);
    }
    s->total_out += produced;
    s->out_len = produced;
    s->draining = 0;

    if (status == CODEC_DONE) {
      return reader_end(s);
    }
    if (status == CODEC_STREAM_END) {
      // Whatever input is left is resolved before the next pass
      s->at_boundary = 1;
      s->members++;
    } else if (s->buf.avail_out == 0) {
      s->draining = 1;
    } else if (s->buf.avail_in == s->pass_before_in && s->pass_produced == 0) {
      // The input ran out before the stream ended, or the engine stalled
      return stream_fail(s, s->finish ? FAIL_TRUNCATED : FAIL_CODEC);
    }

    if (produced > 0) {
      return 0;
    }
  }
  return 0;
}

int codec_read(CodecStream *s, void *buf, size_t n, size_t *got) {
  *got = 0;
  if (s->err || n == 0) {
    return s->err;
  }

  if (s->out_pos == s->out_len && reader_fill(s) != 0) {
    return s->err;
  }

  size_t left = s->out_len - s->out_pos;
  *got = n < left ? n : left;
  memcpy(buf, s->out_buf + s->out_pos, *got);
  s->out_pos += *got;
  return 0;
}

int codec_reader_close(CodecStream *s) {
  // Fails the run instead when warnings are errors
  if (!s->err && s->trailing_ignored &&
      PyErr_WarnFormat(comp_TrailingDataWarning, 1,
                       "Ignored trailing data from byte %llu, after the "
                       "end of the %s stream",
                       (unsigned long long)s->member_start,
                       codec_label(s->ops, &s->params)) < 0) {
    s->err = -1;
    s->fail = FAIL_NONE;
  }
  return stream_close(s);
}

// ---- Driver ----

int codec_run_stream(const CodecOps *ops, const CodecParams *params,
                     int decompress, FILE *src, FILE *dst, CoreContext *ctx) {
  CodecStream *s = decompress ? codec_reader_open(ops, params, src, ctx)
                              : codec_writer_open(ops, params, dst, ctx);
  if (!s) {
    return -1;
  }

  unsigned char *chunk = (unsigned char *)safe_malloc(CODEC_CHUNK);
  int err = 0;
  int io_failed = 0;

  Py_BEGIN_ALLOW_THREADS

  if (!chunk) {
    io_failed = 1;
  } else if (decompress) {
    size_t got;
    while ((err = codec_read(s, chunk, CODEC_CHUNK, &got)) == 0 && got > 0) {
      if (fwrite(chunk, 1, got, dst) != got || ferror(dst)) {
        io_failed = 1;
        break;
      }
    }
  } else {
    do {
      size_t nread = fread(chunk, 1, CODEC_CHUNK, src);
      if (ferror(src)) {
        io_failed = 1;
        break;
      }
      err = codec_write(s, chunk, nread);
    } while (err == 0 && !feof(src));
  }

  Py_END_ALLOW_THREADS

  err = decompress ? codec_reader_close(s) : codec_writer_close(s, io_failed);
  if (err == 0 && io_failed) {
    if (chunk) {
      PyErr_SetString(PyExc_IOError, decompress ? "Error writing output file"
                                                : "Error reading input file");
    }
    err = -1;
  }

  free(chunk);
  return err; // 0, -1, or COMP_CANCELLED
}

int codec_run_file(const CodecOps *ops, const CodecParams *params,
                   int decompress, const char *input_path,
                   const OutputTarget *out, CoreContext *ctx,
                   const char *failure_message) {
  return codec_run_source(ops, params, decompress, input_path, NULL, out, ctx,
                          failure_message);
}

int codec_run_source(const CodecOps *ops, const CodecParams *params,
                     int decompress, const char *input_path, FILE *input,
                     const OutputTarget *out, CoreContext *ctx,
                     const char *failure_message) {
  int checked = output_check(input_path, out);
  if (checked != 0) {
    if (input) {
      fclose(input);
    }
    return checked < 0 ? -1 : 0;
  }

  if (!input && !(input = fs_fopen(input_path, "rb"))) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, input_path);
    return -1;
  }

  ctx_begin_stage_stream(ctx, input);

  char temp[FS_PATH_MAX];
  FILE *output = output_open(out, temp);
  if (!output) {
    fclose(input);
    return -1;
  }

  int err = codec_run_stream(ops, params, decompress, input, output, ctx);

  return output_finish(err, input, output, temp, out, failure_message);
}
