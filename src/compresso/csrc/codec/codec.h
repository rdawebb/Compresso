#ifndef CODEC_H
#define CODEC_H

// One chunk loop shared by both framings: `compression/py_*.c` wraps these in
// the `.comp` header, `standalone/*.c` in each real-world container

#include "../context.h"
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

#define CODEC_CHUNK 65536 // 64KB

typedef enum {
  CODEC_MORE = 0,
  CODEC_DONE = 1, // Stream complete; nothing further to write
  CODEC_ERR = -1,
} CodecStatus;

typedef enum {
  CODEC_WRAP_ZLIB = 0,
  CODEC_WRAP_GZIP = 1,
} CodecWrap;

// Advanced in place across calls, as zlib's z_stream is
typedef struct {
  const unsigned char *next_in;
  size_t avail_in;
  unsigned char *next_out;
  size_t avail_out;
} CodecBuf;

// The standalone containers embed integrity checks `.comp` does not, and
// `.comp`'s lzma asks for an extreme preset `.xz` does not
typedef struct {
  int level;    // -1 for the library default
  int checksum; // Embed, or verify, the codec's own integrity check
  int extreme;  // lzma: LZMA_PRESET_EXTREME
  int wrap;     // deflate: a CodecWrap
  uint64_t orig_size;
} CodecParams;

typedef struct CodecOps {
  const char *name;

  // A zeroed block of this size, which every callback casts to its own struct
  size_t state_size;

  // 0 means CODEC_CHUNK; lz4 needs its frame bound, which exceeds the input
  size_t out_chunk;

  // GIL held
  int (*begin)(void *state, const CodecParams *params, int decompress);

  // GIL released; `finish` is set once the input is exhausted, which is the
  // signal to flush; returns a CodecStatus
  int (*process)(void *state, CodecBuf *buf, int finish);

  // GIL held, on every path including failure and cancellation
  void (*end)(void *state);

  // GIL re-acquired, so a code captured inside the loop can become an
  // exception; NULL leaves the message to the caller
  const char *(*describe)(void *state, int decompress);
} CodecOps;

// `ctx` is NULL-tolerant; returns 0, -1 with a Python exception set, or
// COMP_CANCELLED
int codec_run_stream(const CodecOps *ops, const CodecParams *params,
                     int decompress, FILE *src, FILE *dst, CoreContext *ctx);

// codec_run_stream against two paths, closing and (on failure) unlinking
// through codec_finish_file, to which `failure_message` is passed
int codec_run_file(const CodecOps *ops, const CodecParams *params,
                   int decompress, const char *input_path,
                   const char *output_path, CoreContext *ctx,
                   const char *failure_message);

// ---- Engines ----

const CodecOps *codec_zstd_ops(void);
const CodecOps *codec_lz4_ops(void);

#endif // CODEC_H
