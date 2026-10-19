#ifndef COMMON_H
#define COMMON_H

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "archives.h"
#include "codec/codec.h"
#include "context.h"
#include "fsutil.h"
#include "le.h"
#include "levels.h"
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

// Closes both streams, and on failure or cancellation unlinks the half-written
// output; raises `failure_message` as `errors->backend` unless cancelled or
// pending exception; pass NULL when every failing path sets its own; returns
// `err`, or -1 with OSError when 0 but the output couldn't be flushed or closed
int codec_finish_file(int err, FILE *input, FILE *output,
                      const char *output_path, const CompErrors *errors,
                      const char *failure_message);

// ---- Outputs ----
// Every file output is written to a temp beside its path and committed only on
// success, so a failure never touches an existing file

// Applies `out`'s scheme before any work: an early ERROR or SKIP, and refuses
// to OVERWRITE `src_path` itself (NULL for many inputs); returns 0 to proceed,
// 1 to skip (`actual` filled), or -1 with an exception set
int output_check(const char *src_path, const OutputTarget *out);

// Opens the temp file for `out` into `temp` (at least FS_PATH_MAX bytes);
// NULL with an exception set
FILE *output_open(const OutputTarget *out, char *temp);

// Commits the closed `temp` under `out`'s scheme, filling `actual`; removes
// `temp` if it can't; returns 0, or -1 with an exception set
int output_commit(const char *temp, const OutputTarget *out);

// codec_finish_file on the temp, then commits it under `out`'s scheme, filling
// `actual`; the temp never survives; `output` may be NULL if never opened
int output_finish(int err, FILE *input, FILE *output, const char *temp,
                  const OutputTarget *out, const CompErrors *errors,
                  const char *failure_message);

// ---- Header ----

#define C_MAGIC "COMP"
#define C_MAGIC_LEN 4

// Size on disk: CHeader is never written or read directly
#define C_HEADER_SIZE 16

// The payload embeds its codec's own content checksum, which the decoder
// verifies from the frame itself; informational, as older files lack it
#define C_FLAG_CHECKSUMMED 0x01

typedef struct {
  uint8_t magic[C_MAGIC_LEN];
  uint8_t version;
  uint8_t algo;
  uint8_t level;
  uint8_t flags;
  uint64_t orig_size;
} CHeader;

// Defines the on-disk layout
static inline void c_header_pack(const CHeader *header,
                                 uint8_t buf[C_HEADER_SIZE]) {
  memcpy(buf, header->magic, C_MAGIC_LEN);
  buf[4] = header->version;
  buf[5] = header->algo;
  buf[6] = header->level;
  buf[7] = header->flags;
  put_le(buf + 8, header->orig_size, 8);
}

static inline void c_header_unpack(const uint8_t buf[C_HEADER_SIZE],
                                   CHeader *header) {
  memcpy(header->magic, buf, C_MAGIC_LEN);
  header->version = buf[4];
  header->algo = buf[5];
  header->level = buf[6];
  header->flags = buf[7];
  header->orig_size = get_le(buf + 8, 8);
}

// ---- Algorithms ----

typedef enum {
  ALGO_NONE = 0,
  ALGO_ZLIB = 1,
  ALGO_BZIP2 = 2,
  ALGO_LZMA = 3,
  ALGO_ZSTD = 4,
  ALGO_LZ4 = 5,
  ALGO_SNAPPY = 6
} AlgoID;

// ---- Backend Interface ----

typedef struct CBackend {
  const char *name;
  uint8_t id;
  LevelRange levels;

  // The engine, and the params it runs with each way; an `enc` checksum is
  // recorded as C_FLAG_CHECKSUMMED
  const CodecOps *(*engine)(void);
  CodecParams enc, dec;
} CBackend;

// Ends with a row whose name is NULL
extern const CBackend BACKENDS[];

// Return 0, -1 or COMP_CANCELLED; `max_ratio` is set for that strategy
int backend_compress(const CBackend *backend, FILE *src, FILE *dst, int level,
                     int max_ratio, CoreContext *ctx);

// Decoding must produce exactly `orig_size` bytes
int backend_decompress(const CBackend *backend, FILE *src, FILE *dst,
                       uint64_t orig_size, CoreContext *ctx);

// ---- Strategy ----

typedef enum {
  STRAT_UNKNOWN = -1, // A name strategy_from_string doesn't recognise
  STRAT_BALANCED = 0,
  STRAT_FAST = 1,
  STRAT_MAX_RATIO = 2,
  STRAT_COUNT // Not a strategy
} Strategy;

extern const char *const STRATEGY_NAMES[STRAT_COUNT];

// ---- Backend Registry ----

const CBackend *choose_backend(Strategy strat);

// ---- Exception Objects ----

// Kept in the module's state, so each module object raises its own
struct CompErrors {
  PyObject *error;
  PyObject *header;
  PyObject *backend;
  PyObject *corrupt_data;
  PyObject *extraction_policy;
  PyObject *incomplete_extraction;
  PyObject *cancelled;
  PyObject *trailing_data_warning;
};

// ---- Helpers ----

// NULL and "" mean balanced; anything unrecognised is STRAT_UNKNOWN
Strategy strategy_from_string(const char *str);
AlgoID algo_from_string(const char *str);

const CBackend *find_backend_by_id(uint8_t id);

PyObject *get_capabilities(void);

void *safe_malloc(size_t size);

// `items` (`*cap` elements of `size` bytes) with room for one more after the
// first `count`, doubling `*cap` when full; NULL with MemoryError set leaves
// `items` as it was
void *grow(void *items, size_t *cap, size_t count, size_t size);

// ---- Backend Error Helper ----

void set_backend_error(const CompErrors *errors, const CBackend *backend,
                       const char *op, const char *context);

// ---- Public API ----

// `overwrite_existing` follows the same 0-3 scheme as create_archive's
// parameter of the same name; on success the path actually written (which
// RENAME may have changed) is copied into `out_actual_path`
int compress_file(const char *src_path, const char *dst_path, AlgoID algo,
                  Strategy strategy, int level, int overwrite_existing,
                  char *out_actual_path, size_t out_actual_path_size,
                  CoreContext *ctx);

// Takes the overwrite scheme and reports the path written as compress_file
int decompress_file(const char *src_path, const char *dst_path, AlgoID algo,
                    int overwrite_existing, char *out_actual_path,
                    size_t out_actual_path_size, CoreContext *ctx);

#endif // COMMON_H
