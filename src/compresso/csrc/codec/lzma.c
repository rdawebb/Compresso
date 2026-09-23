#define PY_SSIZE_T_CLEAN
#include "codec.h"
#include <lzma.h>

#define LZMA_DECOMPRESS_MEMLIMIT (512ULL * 1024 * 1024) // 512MB

typedef struct {
  lzma_stream strm;
  lzma_ret code;
  int started; // strm holds an encoder or decoder, so end() must release it
} LzmaState;

// Prefixed throughout: liblzma already exports lzma_end and lzma_code
static uint32_t codec_lzma_preset(int level, int extreme) {
  if (level < 0)
    level = 6;
  if (level > 9)
    level = 9;

  return extreme ? ((uint32_t)level | LZMA_PRESET_EXTREME) : (uint32_t)level;
}

static int codec_lzma_begin(void *state, const CodecParams *params,
                            int decompress) {
  LzmaState *s = (LzmaState *)state;

  // The driver zeroes the state, which is what LZMA_STREAM_INIT amounts to
  s->code =
      decompress
          ? lzma_stream_decoder(&s->strm, LZMA_DECOMPRESS_MEMLIMIT, 0)
          : lzma_easy_encoder(
                &s->strm, codec_lzma_preset(params->level, params->extreme),
                LZMA_CHECK_CRC64);

  if (s->code != LZMA_OK) {
    return -1;
  }

  s->started = 1;
  return 0;
}

static int codec_lzma_process(void *state, CodecBuf *buf, int finish) {
  LzmaState *s = (LzmaState *)state;

  s->strm.next_in = buf->next_in;
  s->strm.avail_in = buf->avail_in;
  s->strm.next_out = buf->next_out;
  s->strm.avail_out = buf->avail_out;

  lzma_ret r = lzma_code(&s->strm, finish ? LZMA_FINISH : LZMA_RUN);

  // lzma_code advances its own pointers, so the window is read back
  buf->next_in = s->strm.next_in;
  buf->avail_in = s->strm.avail_in;
  buf->next_out = s->strm.next_out;
  buf->avail_out = s->strm.avail_out;

  if (r == LZMA_STREAM_END) {
    return CODEC_DONE;
  }
  if (r != LZMA_OK) {
    s->code = r;
    return CODEC_ERR;
  }

  return CODEC_MORE;
}

static void codec_lzma_end(void *state) {
  LzmaState *s = (LzmaState *)state;
  if (s->started) {
    lzma_end(&s->strm);
  }
}

static const char *codec_lzma_describe(void *state, int decompress) {
  LzmaState *s = (LzmaState *)state;

  if (!decompress) {
    return "lzma compression failed";
  }

  switch (s->code) {
  case LZMA_MEMLIMIT_ERROR:
    return "lzma decompression exceeded its 512 MB memory limit";
  case LZMA_FORMAT_ERROR:
    return "lzma format error: invalid compressed data";
  case LZMA_DATA_ERROR:
  case LZMA_BUF_ERROR:
    return "lzma data error: corrupted or truncated compressed data";
  default:
    return "lzma decompression failed";
  }
}

static const CodecOps lzma_ops = {
    .name = "lzma",
    .state_size = sizeof(LzmaState),
    .begin = codec_lzma_begin,
    .process = codec_lzma_process,
    .end = codec_lzma_end,
    .describe = codec_lzma_describe,
};

const CodecOps *codec_lzma_ops(void) { return &lzma_ops; }
