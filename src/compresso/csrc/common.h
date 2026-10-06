#ifndef COMMON_H
#define COMMON_H

#define PY_SSIZE_T_CLEAN
#include "archives.h"
#include "context.h"
#include "fsutil.h"
#include "levels.h"
#include <Python.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

// ---- Compiler Portability ----

// Wrapper macros for compiler-specific attribute names
#if defined(_MSC_VER)
#define UNUSED
#define PACKED
#define PACKED_BEGIN __pragma(pack(push, 1))
#define PACKED_END __pragma(pack(pop))
#else
#define UNUSED __attribute__((unused))
#define PACKED __attribute__((packed))
#define PACKED_BEGIN
#define PACKED_END
#endif

// Checked size_t addition: stores a + b in *out, returning 1 if it overflowed
// MSVC has no __builtin_add_overflow; unsigned wraparound is well defined, so
// the fallback detects it by testing the wrapped sum against an operand
static inline int add_overflow_size(size_t a, size_t b, size_t *out) {
#if defined(_MSC_VER)
  *out = a + b;
  return *out < a;
#else
  return __builtin_add_overflow(a, b, out);
#endif
}

// ---- Byte Order ----

// Both on-disk formats are little-endian by definition (RFC 1952 for gzip, the
// Compresso header below), so multi-byte fields go through these rather than
// being copied from a struct in host order

static inline void write_le16(uint8_t *buf, uint16_t val) {
  buf[0] = (uint8_t)(val & 0xFF);
  buf[1] = (uint8_t)((val >> 8) & 0xFF);
}

static inline uint16_t read_le16(const uint8_t *buf) {
  return (uint16_t)((uint16_t)buf[0] | ((uint16_t)buf[1] << 8));
}

static inline void write_le32(uint8_t *buf, uint32_t val) {
  buf[0] = (uint8_t)(val & 0xFF);
  buf[1] = (uint8_t)((val >> 8) & 0xFF);
  buf[2] = (uint8_t)((val >> 16) & 0xFF);
  buf[3] = (uint8_t)((val >> 24) & 0xFF);
}

static inline uint32_t read_le32(const uint8_t *buf) {
  return (uint32_t)buf[0] | ((uint32_t)buf[1] << 8) | ((uint32_t)buf[2] << 16) |
         ((uint32_t)buf[3] << 24);
}

static inline void write_le64(uint8_t *buf, uint64_t val) {
  for (int i = 0; i < 8; i++)
    buf[i] = (uint8_t)((val >> (i * 8)) & 0xFF);
}

static inline uint64_t read_le64(const uint8_t *buf) {
  uint64_t val = 0;
  for (int i = 0; i < 8; i++)
    val |= (uint64_t)buf[i] << (i * 8);
  return val;
}

// Closes both streams, and on failure or cancellation unlinks the half-written
// output; sets `failure_message` unless cancelled or pending exception; pass
// NULL when every failing path sets its own; returns `err`, or -1 with an
// OSError when it was 0 but the output couldn't be flushed or closed
int codec_finish_file(int err, FILE *input, FILE *output,
                      const char *output_path, const char *failure_message);

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
                  const OutputTarget *out, const char *failure_message);

// ---- Header ----

#define C_MAGIC "COMP"
#define C_MAGIC_LEN 4

// Size on disk: CHeader is never written or read directly
#define C_HEADER_SIZE 16

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
  write_le64(buf + 8, header->orig_size);
}

static inline void c_header_unpack(const uint8_t buf[C_HEADER_SIZE],
                                   CHeader *header) {
  memcpy(header->magic, buf, C_MAGIC_LEN);
  header->version = buf[4];
  header->algo = buf[5];
  header->level = buf[6];
  header->flags = buf[7];
  header->orig_size = read_le64(buf + 8);
}

// ---- Algorithms ----

typedef enum {
  ALGO_NONE = 0,
  ALGO_ZLIB = 1,
  ALGO_BZIP2 = 2,
  ALGO_LZMA = 3,
  ALGO_ZSTD = 4,
  ALGO_LZ4 = 5,
  ALGO_SNAPPY = 6,
  ALGO_ZIP = 7
} AlgoID;

// ---- Backend Interface ----

typedef struct CBackend {
  const char *name;
  uint8_t id;
  LevelRange levels;

  int (*is_available)(void);

  // `ctx` is NULL-tolerant: NULL means no progress reporting or cancellation
  int (*compress_stream)(FILE *src, FILE *dst, int level, CoreContext *ctx);
  int (*decompress_stream)(FILE *src, FILE *dst, uint64_t orig_size,
                           CoreContext *ctx);
} CBackend;

// ---- Strategy ----

typedef enum {
  STRAT_UNKNOWN = -1, // A name strategy_from_string doesn't recognise
  STRAT_BALANCED = 0,
  STRAT_FAST = 1,
  STRAT_MAX_RATIO = 2,
} Strategy;

// ---- Backend Registry ----

void init_backends(void);
const CBackend *choose_backend(Strategy strat);

// ---- Backend Getters ----

const CBackend *get_zlib_backend(void);
const CBackend *get_bzip2_backend(void);
const CBackend *get_lzma_backend(void);
const CBackend *get_zstd_backend(void);
const CBackend *get_lz4_backend(void);
const CBackend *get_snappy_backend(void);

// ---- Exception Objects ----

extern PyObject *comp_Error;
extern PyObject *comp_HeaderError;
extern PyObject *comp_BackendError;
extern PyObject *comp_CorruptDataError;
extern PyObject *comp_ExtractionPolicyError;
extern PyObject *comp_Cancelled;
extern PyObject *comp_TrailingDataWarning;

// ---- Helpers ----

// NULL and "" mean balanced; anything unrecognised is STRAT_UNKNOWN
Strategy strategy_from_string(const char *str);
AlgoID algo_from_string(const char *str);

const CBackend *find_backend_by_name(const char *name);
const CBackend *find_backend_by_id(uint8_t id);

PyObject *get_capabilities(void);

void *safe_malloc(size_t size);

// Raises the errno-mapped OSError if `path` cannot be opened and read
int check_source_readable(const char *path);

// ---- Backend Error Helper ----

void set_backend_error(const CBackend *backend, const char *op,
                       const char *context);

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

const char *get_default_backend_for_strategy(Strategy strat);

#endif // COMMON_H
