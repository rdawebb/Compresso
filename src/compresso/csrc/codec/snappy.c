#include "codec.h"
#include "crc32c.h"
#include <snappy-c.h>
#include <stdio.h>
#include <string.h>

// Snappy's framing format: each chunk is a type byte and an LE24 length, and
// each data chunk holds a masked CRC-32C of at most 64 KB of decoded data
// https://github.com/google/snappy/blob/main/framing_format.txt

enum {
  CHUNK_COMPRESSED = 0x00,
  CHUNK_UNCOMPRESSED = 0x01,
  CHUNK_SKIPPABLE = 0x80, // 0x80-0xFD reserved, 0xFE padding: all skipped
  CHUNK_STREAM_ID = 0xFF,
};

static const unsigned char STREAM_ID[10] = {0xFF, 0x06, 0x00, 0x00, 's',
                                            'N',  'a',  'P',  'p',  'Y'};

// snappy_max_compressed_length(CODEC_CHUNK), which is a function, not a
// constant; a compressed chunk any longer is rejected
#define MAX_COMPRESSED (32 + CODEC_CHUNK + CODEC_CHUNK / 6)
#define MAX_BODY (4 + MAX_COMPRESSED)

typedef struct {
  int decompress;
  int started; // Encoder: stream identifier written; decoder: one read
  const char *error;
  char message[128];

  // Encoder: the input chunk being gathered; decoder: the decoded chunk
  unsigned char raw[CODEC_CHUNK];
  size_t raw_len;

  // Encoder: the chunk being written out; decoder: the chunk being read in,
  // header first
  unsigned char framed[4 + MAX_BODY];
  size_t framed_len;
  size_t skip; // Decoder: bytes of a skippable chunk still to discard

  // What is still to be written out of `raw` or `framed`
  const unsigned char *pending;
  size_t pending_len;
} SnappyState;

static uint32_t masked_crc(const unsigned char *p, size_t n) {
  uint32_t crc = crc32c(0, p, n);
  return ((crc >> 15) | (crc << 17)) + 0xA282EAD8u;
}

static void put_le(unsigned char *p, uint32_t v, int bytes) {
  for (int i = 0; i < bytes; i++)
    p[i] = (unsigned char)(v >> (8 * i));
}

static uint32_t get_le(const unsigned char *p, int bytes) {
  uint32_t v = 0;
  for (int i = 0; i < bytes; i++)
    v |= (uint32_t)p[i] << (8 * i);
  return v;
}

static void drain(SnappyState *s, CodecBuf *buf) {
  if (!s->pending_len)
    return; // `pending` may still be NULL
  size_t n = s->pending_len < buf->avail_out ? s->pending_len : buf->avail_out;
  memcpy(buf->next_out, s->pending, n);
  buf->next_out += n;
  buf->avail_out -= n;
  s->pending += n;
  s->pending_len -= n;
}

static int snappy_begin(void *state, const CodecParams *params, int decompress,
                        CoreContext *ctx) {
  (void)params; // Snappy has no levels, and the framing always checksums
  (void)ctx;
  SnappyState *s = (SnappyState *)state;
  assert(MAX_COMPRESSED == snappy_max_compressed_length(CODEC_CHUNK));
  s->decompress = decompress;
  return 0;
}

// Frames `raw` as one chunk in `framed`, uncompressed unless snappy saves at
// least an eighth, as the reference encoders do
static int encode_chunk(SnappyState *s) {
  unsigned char *body = s->framed + 8;
  size_t len = MAX_COMPRESSED;
  if (snappy_compress((const char *)s->raw, s->raw_len, (char *)body, &len) !=
      SNAPPY_OK) {
    s->error = "snappy_compress failed";
    return -1;
  }

  int type = CHUNK_COMPRESSED;
  if (len >= s->raw_len - s->raw_len / 8) {
    type = CHUNK_UNCOMPRESSED;
    len = s->raw_len;
    memcpy(body, s->raw, len);
  }

  s->framed[0] = (unsigned char)type;
  put_le(s->framed + 1, (uint32_t)(4 + len), 3);
  put_le(s->framed + 4, masked_crc(s->raw, s->raw_len), 4);
  s->pending = s->framed;
  s->pending_len = 8 + len;
  s->raw_len = 0;
  return 0;
}

static int encode(SnappyState *s, CodecBuf *buf, int finish) {
  if (!s->started) {
    s->pending = STREAM_ID;
    s->pending_len = sizeof(STREAM_ID);
    s->started = 1;
  }

  for (;;) {
    drain(s, buf);
    if (s->pending_len)
      return CODEC_MORE; // Output full

    size_t n = sizeof(s->raw) - s->raw_len;
    if (n > buf->avail_in)
      n = buf->avail_in;
    memcpy(s->raw + s->raw_len, buf->next_in, n);
    s->raw_len += n;
    buf->next_in += n;
    buf->avail_in -= n;

    int last = finish && buf->avail_in == 0;
    if (s->raw_len == sizeof(s->raw) || (last && s->raw_len)) {
      if (encode_chunk(s) != 0)
        return CODEC_ERR;
    } else {
      return last ? CODEC_DONE : CODEC_MORE;
    }
  }
}

// Checks the complete chunk in `framed` and, for a data chunk, decodes it into
// `raw` for draining; NULL on success, or error message
static const char *decode_chunk(SnappyState *s) {
  int type = s->framed[0];
  const unsigned char *body = s->framed + 4;
  size_t len = s->framed_len - 4;

  if (type == CHUNK_STREAM_ID) {
    s->started = 1;
    // The header is compared: a length other than 6 fails
    return memcmp(s->framed, STREAM_ID, sizeof(STREAM_ID)) == 0
               ? NULL
               : "invalid stream identifier";
  }
  if (len < 4)
    return "data chunk too short";

  const unsigned char *data = body + 4;
  size_t data_len = len - 4;
  if (type == CHUNK_COMPRESSED) {
    size_t out_len;
    if (snappy_uncompressed_length((const char *)data, data_len, &out_len) !=
            SNAPPY_OK ||
        out_len > sizeof(s->raw) ||
        snappy_uncompress((const char *)data, data_len, (char *)s->raw,
                          &out_len) != SNAPPY_OK)
      return "invalid compressed chunk";
    data = s->raw;
    data_len = out_len;
  } else if (data_len > sizeof(s->raw)) {
    return "uncompressed chunk too long";
  }

  if (masked_crc(data, data_len) != get_le(body, 4))
    return "checksum mismatch";
  s->pending = data;
  s->pending_len = data_len;
  return NULL;
}

// Reads one chunk at a time into `framed`, discarding skippable ones as they
// arrive; the stream identifier may recur, as concatenated streams repeat it
static int decode(SnappyState *s, CodecBuf *buf, int finish) {
  for (;;) {
    drain(s, buf);
    if (s->pending_len)
      return CODEC_MORE; // Output full

    if (s->skip) {
      size_t n = s->skip < buf->avail_in ? s->skip : buf->avail_in;
      buf->next_in += n;
      buf->avail_in -= n;
      s->skip -= n;
      if (s->skip)
        return CODEC_MORE;
    }

    // The header's 4 bytes, then the body they announce
    int in_header = s->framed_len < 4;
    size_t want = in_header ? 4 : 4 + get_le(s->framed + 1, 3);
    size_t n = want - s->framed_len;
    if (n > buf->avail_in)
      n = buf->avail_in;
    memcpy(s->framed + s->framed_len, buf->next_in, n);
    s->framed_len += n;
    buf->next_in += n;
    buf->avail_in -= n;

    if (s->framed_len < want) {
      // A partial chunk at the end stalls; driver reports as truncation
      if (finish && buf->avail_in == 0 && s->framed_len == 0)
        return CODEC_DONE;
      return CODEC_MORE;
    }

    if (in_header) {
      int type = s->framed[0];
      size_t len = get_le(s->framed + 1, 3);
      if (!s->started && type != CHUNK_STREAM_ID) {
        s->error = "missing stream identifier";
        return CODEC_ERR;
      }
      if (type >= CHUNK_SKIPPABLE && type != CHUNK_STREAM_ID) {
        s->skip = len;
        s->framed_len = 0;
        continue;
      }
      if (type > CHUNK_UNCOMPRESSED && type != CHUNK_STREAM_ID) {
        s->error = "reserved unskippable chunk";
        return CODEC_ERR;
      }
      if (len > MAX_BODY) {
        s->error = "chunk too long";
        return CODEC_ERR;
      }
      if (len)
        continue; // Read the body
    }

    s->error = decode_chunk(s);
    s->framed_len = 0;
    if (s->error)
      return CODEC_ERR;
  }
}

static int snappy_process(void *state, CodecBuf *buf, int finish) {
  SnappyState *s = (SnappyState *)state;
  return s->decompress ? decode(s, buf, finish) : encode(s, buf, finish);
}

static void snappy_end(void *state) { (void)state; }

static const char *snappy_describe(void *state, const char *label,
                                   int decompress, int *corrupt) {
  SnappyState *s = (SnappyState *)state;
  *corrupt = decompress;
  snprintf(s->message, sizeof(s->message), "%s %s failed: %s", label,
           decompress ? "decompression" : "compression",
           s->error ? s->error : "unknown error");
  return s->message;
}

static const CodecOps snappy_ops = {
    .name = "snappy",
    .state_size = sizeof(SnappyState),
    .begin = snappy_begin,
    .process = snappy_process,
    .end = snappy_end,
    .describe = snappy_describe,
};

const CodecOps *codec_snappy_ops(void) { return &snappy_ops; }
