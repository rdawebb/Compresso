#define PY_SSIZE_T_CLEAN
#include "../levels.h"
#include "codec.h"
#include <stdio.h>
#include <zstd.h>
#include <zstd_errors.h>

typedef struct {
  ZSTD_CCtx *cctx;
  ZSTD_DCtx *dctx;
  size_t code; // The failing call's result; 0 when begin() failed
  char message[128];
} ZstdState;

static int zstd_begin(void *state, const CodecParams *params, int decompress,
                      CoreContext *ctx) {
  (void)ctx;
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

  assert(level_in_range((LevelRange)LEVELS_ZSTD, params->level));
  int zlevel = params->level < 0 ? ZSTD_CLEVEL_DEFAULT : params->level;
  if (ZSTD_isError(
          ZSTD_CCtx_setParameter(s->cctx, ZSTD_c_compressionLevel, zlevel))) {
    return -1;
  }

  // The standalone containers embed an XXH64 content checksum
  if (params->checksum &&
      ZSTD_isError(ZSTD_CCtx_setParameter(s->cctx, ZSTD_c_checksumFlag, 1))) {
    return -1;
  }

  // Any worker count writes the same bytes, but 0 writes others; a libzstd
  // built without threads has an upper bound of 0
  ZSTD_bounds workers = ZSTD_cParam_getBounds(ZSTD_c_nbWorkers);
  if (!ZSTD_isError(workers.error) && workers.upperBound > 0) {
    uint32_t n = codec_threads(params);
    if (n > (uint32_t)workers.upperBound) {
      n = (uint32_t)workers.upperBound;
    }
    if (ZSTD_isError(
            ZSTD_CCtx_setParameter(s->cctx, ZSTD_c_nbWorkers, (int)n))) {
      return -1;
    }
  }

  // 1 is ZSTD_ps_enable, which zstd.h only declares for static linking
  if (params->max_ratio &&
      ZSTD_isError(ZSTD_CCtx_setParameter(
          s->cctx, ZSTD_c_enableLongDistanceMatching, 1))) {
    return -1;
  }

  if (params->exact_size &&
      ZSTD_isError(ZSTD_CCtx_setPledgedSrcSize(s->cctx, params->orig_size))) {
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
    s->code = r;
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

static const char *zstd_describe(void *state, const char *label, int decompress,
                                 int *corrupt) {
  ZstdState *s = (ZstdState *)state;
  const char *op = decompress ? "decompression" : "compression";

  if (!s->code) {
    *corrupt = 0;
    snprintf(s->message, sizeof(s->message), "%s %s failed", label, op);
    return s->message;
  }

  *corrupt =
      decompress && ZSTD_getErrorCode(s->code) != ZSTD_error_memory_allocation;
  snprintf(s->message, sizeof(s->message), "%s %s failed: %s", label, op,
           ZSTD_getErrorName(s->code));
  return s->message;
}

static const CodecOps zstd_ops = {
    .name = "zstd",
    .state_size = sizeof(ZstdState),
    .begin = zstd_begin,
    .process = zstd_process,
    .end = zstd_end,
    .describe = zstd_describe,
};

const CodecOps *codec_zstd_ops(void) { return &zstd_ops; }
