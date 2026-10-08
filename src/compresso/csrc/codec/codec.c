#define PY_SSIZE_T_CLEAN
#include "codec.h"
#include "../common.h"
#include "../fsutil.h"
#include <Python.h>
#include <stdlib.h>

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

int codec_run_stream(const CodecOps *ops, const CodecParams *params,
                     int decompress, FILE *src, FILE *dst, CoreContext *ctx) {
  size_t out_size = ops->out_chunk ? ops->out_chunk : CODEC_CHUNK;

  // A stateless engine is allowed, so a NULL state isn't a failure by itself
  void *state = NULL;
  if (ops->state_size) {
    state = calloc(1, ops->state_size);
    if (!state) {
      PyErr_NoMemory();
      return -1;
    }
  }
  unsigned char *in_buf = (unsigned char *)safe_malloc(CODEC_CHUNK);
  unsigned char *out_buf = (unsigned char *)safe_malloc(out_size);
  if (!in_buf || !out_buf) {
    free(state);
    free(in_buf);
    free(out_buf);
    return -1;
  }

  if (ops->begin(state, params, decompress, ctx) != 0) {
    set_failure(ops, params, state, FAIL_CODEC, decompress);
    ops->end(state);
    free(state);
    free(in_buf);
    free(out_buf);
    return -1;
  }

  CodecBuf buf = {in_buf, 0, out_buf, 0};
  FailKind fail = FAIL_NONE;
  int err = 0;
  int finish = 0;
  int done = 0;

  // Set once a member ends, until the input left over is resolved
  int at_boundary = 0;
  int members = 0;
  size_t member_out = 0;
  uint64_t total_out = 0;
  uint64_t read_total = 0;
  uint64_t member_start = 0; // Input offset of the member being decoded
  int trailing_ignored = 0;

  Py_BEGIN_ALLOW_THREADS

  while (!done) {
    if (buf.avail_in == 0 && !finish) {
      size_t nread = fread(in_buf, 1, CODEC_CHUNK, src);
      if (ferror(src)) {
        err = -1;
        fail = FAIL_READ;
        break;
      }

      buf.next_in = in_buf;
      buf.avail_in = nread;
      read_total += nread;
      if (feof(src)) {
        finish = 1;
      }

      int advance = ctx_advance(ctx, nread);
      if (advance != 0) {
        err = advance;
        break;
      }
    }

    if (at_boundary) {
      if (buf.avail_in == 0) {
        break; // Only reachable at end of input: the last member was complete
      }
      member_start = read_total - buf.avail_in;
      if (!params->concatenated || !ops->reset) {
        if (params->ignore_trailing) {
          trailing_ignored = 1;
        } else {
          err = -1;
          fail = FAIL_TRAILING;
        }
        break;
      }
      if (ops->reset(state) != 0) {
        err = -1;
        fail = FAIL_CODEC;
        break;
      }
      at_boundary = 0;
      member_out = 0;
    }

    size_t before_in = buf.avail_in;
    size_t produced_pass = 0;

    // Full buffer means the engine may have more waiting
    do {
      buf.next_out = out_buf;
      buf.avail_out = out_size;

      int status = ops->process(state, &buf, finish);
      if (status == CODEC_ERR) {
        // Failing before producing anything is a trailing error
        int trailing = members > 0 && member_out == 0;
        if (trailing && params->ignore_trailing) {
          trailing_ignored = 1;
          done = 1;
          break;
        }
        err = -1;
        fail = trailing ? FAIL_TRAILING : FAIL_CODEC;
        break;
      }

      size_t produced = out_size - buf.avail_out;
      produced_pass += produced;
      member_out += produced;

      // Caught before the write, so a bomb stops at its claimed size
      if (params->exact_size && produced > params->orig_size - total_out) {
        err = -1;
        fail = FAIL_TOO_LONG;
        break;
      }
      total_out += produced;

      if (produced > 0 &&
          (fwrite(out_buf, 1, produced, dst) != produced || ferror(dst))) {
        err = -1;
        fail = FAIL_WRITE;
        break;
      }

      if (status == CODEC_DONE) {
        done = 1;
        break;
      }
      if (status == CODEC_STREAM_END) {
        at_boundary = 1;
        members++;
        break;
      }
    } while (buf.avail_out == 0);

    if (err || done) {
      break;
    }
    if (at_boundary) {
      continue; // Whatever input is left is resolved before the next pass
    }

    // The input ran out before the stream ended, or the engine stalled
    if (buf.avail_in == before_in && produced_pass == 0) {
      err = -1;
      fail = finish ? FAIL_TRUNCATED : FAIL_CODEC;
      break;
    }
  }

  if (err == 0 && params->exact_size && total_out != params->orig_size) {
    err = -1;
    fail = FAIL_TOO_SHORT;
  }

  Py_END_ALLOW_THREADS

  // Fails the run instead when warnings are errors
  if (err == 0 && trailing_ignored &&
      PyErr_WarnFormat(comp_TrailingDataWarning, 1,
                       "Ignored trailing data from byte %llu, after the "
                       "end of the %s stream",
                       (unsigned long long)member_start,
                       codec_label(ops, params)) < 0) {
    err = -1;
  }

  // Before end(), which releases what describe() reads from
  if (err == -1) {
    set_failure(ops, params, state, fail, decompress);
  }

  ops->end(state);

  free(state);
  free(in_buf);
  free(out_buf);

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
