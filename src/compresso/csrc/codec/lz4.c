#define PY_SSIZE_T_CLEAN
#include "../levels.h"
#include "codec.h"
#include <lz4frame.h>
#include <stdio.h>
#include <string.h>

// LZ4F_compressUpdate needs room for every block a call completes: with the
// default 64 KB blocks, at most one per 64 KB of input, each with a 4-byte
// header, which this covers
#define LZ4_OUT_CHUNK (CODEC_CHUNK + CODEC_CHUNK / 255 + 16)

typedef struct {
  LZ4F_compressionContext_t cctx;
  LZ4F_decompressionContext_t dctx;
  LZ4F_preferences_t prefs;
  int header_written;
  size_t code; // The failing call's result; 0 when begin() failed
  char message[128];
} LZ4State;

static int lz4_begin(void *state, const CodecParams *params, int decompress,
                     CoreContext *ctx) {
  (void)ctx;
  LZ4State *s = (LZ4State *)state;

  if (decompress) {
    return LZ4F_isError(LZ4F_createDecompressionContext(&s->dctx, LZ4F_VERSION))
               ? -1
               : 0;
  }

  if (LZ4F_isError(LZ4F_createCompressionContext(&s->cctx, LZ4F_VERSION))) {
    return -1;
  }

  memset(&s->prefs, 0, sizeof(s->prefs));
  assert(level_in_range((LevelRange)LEVELS_LZ4, params->level));
  s->prefs.compressionLevel = params->level < 0 ? 0 : params->level;

  // The standalone container embeds an xxHash content checksum
  if (params->checksum) {
    s->prefs.frameInfo.contentChecksumFlag = LZ4F_contentChecksumEnabled;
  }

  return 0;
}

static int lz4_process(void *state, CodecBuf *buf, int finish) {
  LZ4State *s = (LZ4State *)state;

  if (s->dctx) {
    size_t produced = buf->avail_out;
    size_t consumed = buf->avail_in;

    size_t r = LZ4F_decompress(s->dctx, buf->next_out, &produced, buf->next_in,
                               &consumed, NULL);
    if (LZ4F_isError(r)) {
      s->code = r;
      return CODEC_ERR;
    }

    buf->next_in += consumed;
    buf->avail_in -= consumed;
    buf->next_out += produced;
    buf->avail_out -= produced;

    // A skippable frame decodes to nothing and ends like any other
    return r == 0 ? CODEC_STREAM_END : CODEC_MORE;
  }

  // The header goes out on its own, leaving a full buffer for the chunk that
  // follows: LZ4F_compressUpdate needs the whole bound available at once
  if (!s->header_written) {
    size_t n =
        LZ4F_compressBegin(s->cctx, buf->next_out, buf->avail_out, &s->prefs);
    if (LZ4F_isError(n)) {
      s->code = n;
      return CODEC_ERR;
    }

    buf->next_out += n;
    buf->avail_out -= n;
    s->header_written = 1;
    return CODEC_MORE;
  }

  if (buf->avail_in > 0) {
    size_t n = LZ4F_compressUpdate(s->cctx, buf->next_out, buf->avail_out,
                                   buf->next_in, buf->avail_in, NULL);
    if (LZ4F_isError(n)) {
      s->code = n;
      return CODEC_ERR;
    }

    buf->next_in += buf->avail_in;
    buf->avail_in = 0;
    buf->next_out += n;
    buf->avail_out -= n;
    return CODEC_MORE;
  }

  if (finish) {
    size_t n = LZ4F_compressEnd(s->cctx, buf->next_out, buf->avail_out, NULL);
    if (LZ4F_isError(n)) {
      s->code = n;
      return CODEC_ERR;
    }

    buf->next_out += n;
    buf->avail_out -= n;
    return CODEC_DONE;
  }

  return CODEC_MORE;
}

static int lz4_reset(void *state) {
  LZ4State *s = (LZ4State *)state;
  LZ4F_resetDecompressionContext(s->dctx);
  return 0;
}

static void lz4_end(void *state) {
  LZ4State *s = (LZ4State *)state;
  if (s->cctx) {
    LZ4F_freeCompressionContext(s->cctx);
  }
  if (s->dctx) {
    LZ4F_freeDecompressionContext(s->dctx);
  }
}

static const char *lz4_describe(void *state, const char *label, int decompress,
                                int *corrupt) {
  LZ4State *s = (LZ4State *)state;
  const char *op = decompress ? "decompression" : "compression";

  if (!s->code) {
    *corrupt = 0;
    snprintf(s->message, sizeof(s->message), "%s %s failed", label, op);
    return s->message;
  }

  // Error codes are only public under LZ4F_STATIC_LINKING_ONLY
  const char *name = LZ4F_getErrorName(s->code);
  *corrupt = decompress && strcmp(name, "ERROR_allocation_failed") != 0;
  snprintf(s->message, sizeof(s->message), "%s %s failed: %s", label, op, name);
  return s->message;
}

static const CodecOps lz4_ops = {
    .name = "lz4",
    .state_size = sizeof(LZ4State),
    .out_chunk = LZ4_OUT_CHUNK,
    .begin = lz4_begin,
    .process = lz4_process,
    .reset = lz4_reset,
    .end = lz4_end,
    .describe = lz4_describe,
};

const CodecOps *codec_lz4_ops(void) { return &lz4_ops; }
