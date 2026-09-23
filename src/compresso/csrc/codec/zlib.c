#define PY_SSIZE_T_CLEAN
#include "codec.h"
#include <stdio.h>
#include <zlib.h>

typedef struct {
  z_stream strm;
  gz_header header;
  char message[128];
  int code;
  int started;
  int decompress;
} ZlibState;

// Same deflate engine either way; the window bits pick the container zlib
// wraps around it
static int zlib_window_bits(int wrap) {
  return wrap == CODEC_WRAP_GZIP ? (16 | MAX_WBITS) : MAX_WBITS;
}

static int zlib_begin(void *state, const CodecParams *params, int decompress) {
  ZlibState *s = (ZlibState *)state;
  s->decompress = decompress;

  int bits = zlib_window_bits(params->wrap);

  if (decompress) {
    s->code = inflateInit2(&s->strm, bits);
    if (s->code != Z_OK) {
      return -1;
    }
    s->started = 1;
    return 0;
  }

  int level = (params->level >= 0 && params->level <= 9)
                  ? params->level
                  : Z_DEFAULT_COMPRESSION;

  s->code =
      deflateInit2(&s->strm, level, Z_DEFLATED, bits, 8, Z_DEFAULT_STRATEGY);
  if (s->code != Z_OK) {
    return -1;
  }
  s->started = 1;

  if (params->wrap == CODEC_WRAP_GZIP) {
    // Pinned rather than left to zlib: OS_CODE varies by build platform, and
    // a zeroed MTIME is what makes the output reproducible
    s->header.time = 0;
    s->header.os = 3; // Unix
    if (deflateSetHeader(&s->strm, &s->header) != Z_OK) {
      return -1;
    }
  }

  return 0;
}

static int zlib_process(void *state, CodecBuf *buf, int finish) {
  ZlibState *s = (ZlibState *)state;

  s->strm.next_in = (Bytef *)(uintptr_t)buf->next_in;
  s->strm.avail_in = (uInt)buf->avail_in;
  s->strm.next_out = (Bytef *)buf->next_out;
  s->strm.avail_out = (uInt)buf->avail_out;

  int r = s->decompress ? inflate(&s->strm, Z_NO_FLUSH)
                        : deflate(&s->strm, finish ? Z_FINISH : Z_NO_FLUSH);

  buf->next_in = (const unsigned char *)s->strm.next_in;
  buf->avail_in = s->strm.avail_in;
  buf->next_out = (unsigned char *)s->strm.next_out;
  buf->avail_out = s->strm.avail_out;

  if (r == Z_STREAM_END) {
    return CODEC_DONE;
  }

  // Z_BUF_ERROR only reports that no progress was possible on this call, which
  // the driver's own stall check turns into an error if it persists
  if (r != Z_OK && r != Z_BUF_ERROR) {
    s->code = r;
    return CODEC_ERR;
  }

  return CODEC_MORE;
}

static void zlib_end(void *state) {
  ZlibState *s = (ZlibState *)state;
  if (!s->started) {
    return;
  }

  if (s->decompress) {
    inflateEnd(&s->strm);
  } else {
    deflateEnd(&s->strm);
  }
}

static const char *zlib_describe(void *state, int decompress) {
  ZlibState *s = (ZlibState *)state;
  const char *op = decompress ? "decompression" : "compression";

  if (s->strm.msg) {
    snprintf(s->message, sizeof(s->message), "zlib %s failed: %s", op,
             s->strm.msg);
  } else {
    snprintf(s->message, sizeof(s->message), "zlib %s failed", op);
  }

  return s->message;
}

static const CodecOps zlib_ops = {
    .name = "zlib",
    .state_size = sizeof(ZlibState),
    .begin = zlib_begin,
    .process = zlib_process,
    .end = zlib_end,
    .describe = zlib_describe,
};

const CodecOps *codec_zlib_ops(void) { return &zlib_ops; }
