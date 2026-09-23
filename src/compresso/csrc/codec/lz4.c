#define PY_SSIZE_T_CLEAN
#include "codec.h"
#include <lz4frame.h>
#include <string.h>

// LZ4F_compressUpdate consumes a whole chunk in one call, so the output buffer
// has to hold that chunk's worst case rather than a chunk's worth
#define LZ4_OUT_CHUNK (CODEC_CHUNK + CODEC_CHUNK / 255 + 16)

typedef struct {
  LZ4F_compressionContext_t cctx;
  LZ4F_decompressionContext_t dctx;
  LZ4F_preferences_t prefs;
  int header_written;
} LZ4State;

static int lz4_begin(void *state, const CodecParams *params, int decompress) {
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
      return CODEC_ERR;
    }

    buf->next_in += consumed;
    buf->avail_in -= consumed;
    buf->next_out += produced;
    buf->avail_out -= produced;

    // Anything past the first frame is ignored
    return r == 0 ? CODEC_DONE : CODEC_MORE;
  }

  // The header goes out on its own, leaving a full buffer for the chunk that
  // follows: LZ4F_compressUpdate needs the whole bound available at once
  if (!s->header_written) {
    size_t n =
        LZ4F_compressBegin(s->cctx, buf->next_out, buf->avail_out, &s->prefs);
    if (LZ4F_isError(n)) {
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
      return CODEC_ERR;
    }

    buf->next_out += n;
    buf->avail_out -= n;
    return CODEC_DONE;
  }

  return CODEC_MORE;
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

static const CodecOps lz4_ops = {
    .name = "lz4",
    .state_size = sizeof(LZ4State),
    .out_chunk = LZ4_OUT_CHUNK,
    .begin = lz4_begin,
    .process = lz4_process,
    .end = lz4_end,
};

const CodecOps *codec_lz4_ops(void) { return &lz4_ops; }
