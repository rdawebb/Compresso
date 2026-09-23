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
    PyErr_Format(comp_BackendError, "Truncated or incomplete %s stream",
                 codec_label(ops, params));
    return;
  case FAIL_CODEC: {
    const char *message =
        ops->describe ? ops->describe(state, codec_label(ops, params), decompress)
                      : NULL;
    if (message) {
      PyErr_SetString(comp_BackendError, message);
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

  // A stateless engine is allowed, but safe_malloc rejects a zero size
  void *state = ops->state_size ? safe_malloc(ops->state_size) : NULL;
  unsigned char *in_buf = (unsigned char *)safe_malloc(CODEC_CHUNK);
  unsigned char *out_buf = (unsigned char *)safe_malloc(out_size);
  if ((ops->state_size && !state) || !in_buf || !out_buf) {
    free(state);
    free(in_buf);
    free(out_buf);
    return -1;
  }
  memset(state, 0, ops->state_size);

  if (ops->begin(state, params, decompress) != 0) {
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
      if (feof(src)) {
        finish = 1;
      }

      int advance = ctx_advance(ctx, nread);
      if (advance != 0) {
        err = advance;
        break;
      }
    }

    size_t before_in = buf.avail_in;
    size_t produced_pass = 0;

    // Keep pumping while the engine fills the output buffer, since a full
    // buffer means it may have more waiting
    do {
      buf.next_out = out_buf;
      buf.avail_out = out_size;

      int status = ops->process(state, &buf, finish);
      if (status == CODEC_ERR) {
        err = -1;
        fail = FAIL_CODEC;
        break;
      }

      size_t produced = out_size - buf.avail_out;
      produced_pass += produced;

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
    } while (buf.avail_out == 0);

    if (err || done) {
      break;
    }

    // The engine neither consumed nor produced, so another pass would do the
    // same: the input ran out before the stream ended, or the engine stalled
    if (buf.avail_in == before_in && produced_pass == 0) {
      err = -1;
      fail = finish ? FAIL_TRUNCATED : FAIL_CODEC;
      break;
    }
  }

  Py_END_ALLOW_THREADS

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
                   const char *output_path, CoreContext *ctx,
                   const char *failure_message) {
  FILE *input = fs_fopen(input_path, "rb");
  if (!input) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, input_path);
    return -1;
  }

  ctx_begin_stage_stream(ctx, input);

  FILE *output = fs_fopen(output_path, "wb");
  if (!output) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, output_path);
    fclose(input);
    return -1;
  }

  int err = codec_run_stream(ops, params, decompress, input, output, ctx);

  return codec_finish_file(err, input, output, output_path, failure_message);
}
