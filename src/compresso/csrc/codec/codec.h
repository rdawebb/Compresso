#ifndef CODEC_H
#define CODEC_H

// One chunk loop shared by both framings: `backends.c` wraps these in
// the `.comp` header, `standalone.c` in each real-world container

#include "../context.h"
#include "../fsutil.h"
#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

#define CODEC_CHUNK 65536 // 64KB

typedef enum {
  CODEC_MORE = 0,
  CODEC_DONE = 1, // Stream complete; nothing further to write
  // Decoding only: one member ended, and any input left over is either the
  // next member (see CodecParams.concatenated) or an error
  CODEC_STREAM_END = 2,
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
  int level;           // -1 for the library default; already validated
  int checksum;        // Embed the codec's own integrity check
  int extreme;         // lzma: LZMA_PRESET_EXTREME
  int wrap;            // deflate: a CodecWrap
  int concatenated;    // The container allows members back to back
  int ignore_trailing; // Trailing data ends decoding with a TrailingDataWarning
  int exact_size;      // Decoding must produce exactly orig_size bytes
  uint64_t orig_size;

  // Codec's name in an error message; NULL uses CodecOps.name
  const char *label;
} CodecParams;

typedef struct CodecOps {
  const char *name;

  // A zeroed block of this size, which every callback casts to its own struct
  size_t state_size;

  // 0 means CODEC_CHUNK; lz4 needs its frame bound, which exceeds the input
  size_t out_chunk;

  // GIL held; `ctx` (NULL-tolerant) outlives the run, so an engine may keep it
  // to ctx_log from process()
  int (*begin)(void *state, const CodecParams *params, int decompress,
               CoreContext *ctx);

  // GIL released; `finish` is set once the input is exhausted, which is the
  // signal to flush; returns a CodecStatus
  int (*process)(void *state, CodecBuf *buf, int finish);

  // GIL released; readies a decoder that returned CODEC_STREAM_END for the
  // next member; NULL for an engine that never returns it
  int (*reset)(void *state);

  // GIL held, on every path including failure and cancellation
  void (*end)(void *state);

  // GIL re-acquired, so a code captured inside the loop can become an
  // exception; `label` is the codec's name; sets *corrupt when the input caused
  // a decoding failure; NULL leaves the message to the caller
  const char *(*describe)(void *state, const char *label, int decompress,
                          int *corrupt);
} CodecOps;

// A codec run driven a call at a time: a writer compresses what is pushed to
// `sink`, a reader decompresses `source` as it is pulled; open and close need
// the GIL; write and read don't; `params` is copied; `ctx` is NULL-tolerant
typedef struct CodecStream CodecStream;

// NULL with a Python exception set on failure
CodecStream *codec_writer_open(const CodecOps *ops, const CodecParams *params,
                               FILE *sink, CoreContext *ctx);

// Returns 0, -1 or COMP_CANCELLED; a failure sticks, and close sets the
// exception
int codec_write(CodecStream *s, const void *data, size_t n);

// Finishes the stream unless `discard`, then frees `s`; returns 0, -1 with a
// Python exception set, or COMP_CANCELLED
int codec_writer_close(CodecStream *s, int discard);

CodecStream *codec_reader_open(const CodecOps *ops, const CodecParams *params,
                               FILE *source, CoreContext *ctx);

// Decodes up to `n` bytes into `buf`; *got is 0 only at the end of the stream;
// returns as codec_write
int codec_read(CodecStream *s, void *buf, size_t n, size_t *got);

// Frees `s`; returns as codec_writer_close, also failing if the trailing data
// warning is raised as an error
int codec_reader_close(CodecStream *s);

// `ctx` is NULL-tolerant; returns 0, -1 with a Python exception set, or
// COMP_CANCELLED
int codec_run_stream(const CodecOps *ops, const CodecParams *params,
                     int decompress, FILE *src, FILE *dst, CoreContext *ctx);

// codec_run_stream from a path into `out`, through output_check, output_open
// and output_finish, to which `failure_message` is passed
int codec_run_file(const CodecOps *ops, const CodecParams *params,
                   int decompress, const char *input_path,
                   const OutputTarget *out, CoreContext *ctx,
                   const char *failure_message);

// codec_run_file over `input`, already open on `input_path` (NULL to open it)
int codec_run_source(const CodecOps *ops, const CodecParams *params,
                     int decompress, const char *input_path, FILE *input,
                     const OutputTarget *out, CoreContext *ctx,
                     const char *failure_message);

// ---- Engines ----

const CodecOps *codec_zstd_ops(void);
const CodecOps *codec_lz4_ops(void);
const CodecOps *codec_lzma_ops(void);
const CodecOps *codec_bzip2_ops(void);
const CodecOps *codec_zlib_ops(void);
const CodecOps *codec_snappy_ops(void);

#endif // CODEC_H
