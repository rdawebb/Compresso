#define PY_SSIZE_T_CLEAN
#include "codec.h"
#include <bzlib.h>

typedef struct {
  bz_stream strm;
  int code;
  int started;
  int decompress;
} BzipState;

static int bzip2_block_size_from_level(int level) {
  if (level <= 0)
    return 9; // Default: max compression
  if (level > 9)
    return 9;
  return level;
}

static int bzip2_begin(void *state, const CodecParams *params, int decompress) {
  BzipState *s = (BzipState *)state;
  s->decompress = decompress;

  // Verbosity 0, and the recommended workFactor of 30
  s->code = decompress
                ? BZ2_bzDecompressInit(&s->strm, 0, 0)
                : BZ2_bzCompressInit(&s->strm,
                                     bzip2_block_size_from_level(params->level),
                                     0, 30);

  if (s->code != BZ_OK) {
    return -1;
  }

  s->started = 1;
  return 0;
}

static int bzip2_process(void *state, CodecBuf *buf, int finish) {
  BzipState *s = (BzipState *)state;

  s->strm.next_in = (char *)(uintptr_t)buf->next_in;
  s->strm.avail_in = (unsigned int)buf->avail_in;
  s->strm.next_out = (char *)buf->next_out;
  s->strm.avail_out = (unsigned int)buf->avail_out;

  int r = s->decompress ? BZ2_bzDecompress(&s->strm)
                        : BZ2_bzCompress(&s->strm, finish ? BZ_FINISH : BZ_RUN);

  buf->next_in = (const unsigned char *)s->strm.next_in;
  buf->avail_in = s->strm.avail_in;
  buf->next_out = (unsigned char *)s->strm.next_out;
  buf->avail_out = s->strm.avail_out;

  if (r == BZ_STREAM_END) {
    return CODEC_DONE;
  }
  if (r != BZ_OK && r != BZ_RUN_OK && r != BZ_FINISH_OK) {
    s->code = r;
    return CODEC_ERR;
  }

  return CODEC_MORE;
}

static void bzip2_end(void *state) {
  BzipState *s = (BzipState *)state;
  if (!s->started) {
    return;
  }

  if (s->decompress) {
    BZ2_bzDecompressEnd(&s->strm);
  } else {
    BZ2_bzCompressEnd(&s->strm);
  }
}

static const char *bzip2_describe(void *state, int decompress) {
  BzipState *s = (BzipState *)state;

  if (!decompress) {
    return "bzip2 compression failed";
  }

  return (s->code == BZ_DATA_ERROR || s->code == BZ_DATA_ERROR_MAGIC)
             ? "bzip2 data error: corrupted or invalid compressed data"
             : "bzip2 decompression failed";
}

static const CodecOps bzip2_ops = {
    .name = "bzip2",
    .state_size = sizeof(BzipState),
    .begin = bzip2_begin,
    .process = bzip2_process,
    .end = bzip2_end,
    .describe = bzip2_describe,
};

const CodecOps *codec_bzip2_ops(void) { return &bzip2_ops; }
