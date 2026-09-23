#define PY_SSIZE_T_CLEAN
#include "codec.h"
#include <zstd.h>

typedef struct {
  ZSTD_CCtx *cctx;
  ZSTD_DCtx *dctx;
} ZstdState;

static int zstd_level_from_generic(int level) {
  if (level < ZSTD_minCLevel())
    return ZSTD_CLEVEL_DEFAULT;
  if (level > ZSTD_maxCLevel())
    return ZSTD_maxCLevel();
  return level;
}

static int zstd_begin(void *state, const CodecParams *params, int decompress) {
  ZstdState *s = (ZstdState *)state;

  if (decompress) {
    s->dctx = ZSTD_createDCtx();
    return s->dctx && !ZSTD_isError(ZSTD_DCtx_reset(
                          s->dctx, ZSTD_reset_session_and_parameters))
               ? 0
               : -1;
  }

  s->cctx = ZSTD_createCCtx();
  if (!s->cctx) {
    return -1;
  }

  int zlevel = (params->level >= 0) ? zstd_level_from_generic(params->level)
                                    : ZSTD_CLEVEL_DEFAULT;
  if (ZSTD_isError(
          ZSTD_CCtx_setParameter(s->cctx, ZSTD_c_compressionLevel, zlevel))) {
    return -1;
  }

  // The standalone containers embed an XXH64 content checksum
  if (params->checksum &&
      ZSTD_isError(ZSTD_CCtx_setParameter(s->cctx, ZSTD_c_checksumFlag, 1))) {
    return -1;
  }

  return 0;
}

static int zstd_process(void *state, CodecBuf *buf, int finish) {
  ZstdState *s = (ZstdState *)state;

  ZSTD_inBuffer in = {buf->next_in, buf->avail_in, 0};
  ZSTD_outBuffer out = {buf->next_out, buf->avail_out, 0};

  size_t r;
  if (s->dctx) {
    r = ZSTD_decompressStream(s->dctx, &out, &in);
  } else {
    r = ZSTD_compressStream2(s->cctx, &out, &in,
                             finish ? ZSTD_e_end : ZSTD_e_continue);
  }

  if (ZSTD_isError(r)) {
    return CODEC_ERR;
  }

  buf->next_in += in.pos;
  buf->avail_in -= in.pos;
  buf->next_out += out.pos;
  buf->avail_out -= out.pos;

  if (s->dctx) {
    // r == 0 ends a frame, not necessarily the file: .zst members concatenate,
    // so only an exhausted input ends the stream
    return (r == 0 && buf->avail_in == 0 && finish) ? CODEC_DONE : CODEC_MORE;
  }

  return (finish && r == 0) ? CODEC_DONE : CODEC_MORE;
}

static void zstd_end(void *state) {
  ZstdState *s = (ZstdState *)state;
  ZSTD_freeCCtx(s->cctx);
  ZSTD_freeDCtx(s->dctx);
}

static const CodecOps zstd_ops = {
    .name = "zstd",
    .state_size = sizeof(ZstdState),
    .begin = zstd_begin,
    .process = zstd_process,
    .end = zstd_end,
};

const CodecOps *codec_zstd_ops(void) { return &zstd_ops; }
