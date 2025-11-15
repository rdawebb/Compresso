#ifndef COMMON_H
#define COMMON_H

#define PY_SSIZE_T_CLEAN
#include <Python.h> 
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>


// ---- Header ----

#define C_MAGIC "COMP"
#define C_MAGIC_LEN 4

typedef struct {
    uint8_t magic[C_MAGIC_LEN];
    uint8_t version;
    uint8_t algo;
    uint8_t level;
    uint8_t flags;
    uint64_t orig_size;
} CHeader;


// ---- Algorithms ----

typedef enum {
    ALGO_NONE = 0,
    ALGO_ZLIB = 1,
    ALGO_BZIP2 = 2,
    ALGO_LZMA = 3,
    ALGO_ZSTD = 4,
    ALGO_LZ4 = 5,
    ALGO_SNAPPY = 6,
} AlgoID;


// ---- Backend Interface ----

typedef struct CBackend {
    const char *name;
    uint8_t id;

    int (*is_available)(void);
    size_t (*max_compressed_size)(size_t input_size);

    int (*compress_buffer)(const unsigned char *input, size_t input_size,
                           unsigned char *output, size_t *output_capacity,
                           int level, size_t *output_size);

    int (*decompress_buffer)(const unsigned char *input, size_t input_size,
                             unsigned char *output, size_t *output_capacity,
                             size_t *output_size);

    int (*compress_stream)(FILE *src, FILE *dst, int level);
    int (*decompress_stream)(FILE *src, FILE *dst, uint64_t orig_size);
} CBackend;


// ---- Strategy ----

typedef enum {
    STRAT_BALANCED = 0,
    STRAT_FAST = 1,
    STRAT_MAX_RATIO = 2,
} Strategy;


// Exception Objects
extern PyObject *comp_Error;
extern PyObject *comp_HeaderError;
extern PyObject *comp_BackendError;


// Helpers
Strategy strategy_from_string(const char *str);

const CBackend *find_backend_by_name(const char *name);
const CBackend *find_backend_by_id(uint8_t id);


// Public API
int compress_file(const char *src_path, const char *dst_path,
                  const char *algo_name, const char *strategy_name,
                  int level);

int decompress_file(const char *src_path, const char *dst_path,
                    const char *algo_name);


#endif // COMMON_H