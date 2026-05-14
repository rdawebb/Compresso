#ifndef COMMON_H
#define COMMON_H

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include "archives.h"
#include "standalone.h"


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

#if defined(__STDC_VERSION__) && __STDC_VERSION__ >= 201112L
_Static_assert(sizeof(CHeader) == 16,
"CHeader must be exactly 16 bytes with no padding");
#endif


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


// ---- Backend Getters ----

const CBackend *get_zlib_backend(void);
const CBackend *get_bzip2_backend(void);
const CBackend *get_lzma_backend(void);
const CBackend *get_zstd_backend(void);
const CBackend *get_lz4_backend(void);
const CBackend *get_snappy_backend(void);


// ---- Snappy Helper ----

size_t snappy_decompressed_size(const unsigned char *input, size_t input_size);


// ---- Internal Helper ----

const CBackend *choose_backend(Strategy strat);


// ---- Exception Objects ----

extern PyObject *comp_Error;
extern PyObject *comp_HeaderError;
extern PyObject *comp_BackendError;


// ---- Helpers ----

Strategy strategy_from_string(const char *str);
AlgoID algo_from_string(const char *str);

const CBackend *find_backend_by_name(const char *name);
const CBackend *find_backend_by_id(uint8_t id);

PyObject *get_capabilities(void);

#define MAX_FILE_SIZE (10ULL * 1024 * 1024 * 1024) // 10 GB
#define MAX_DECOMPRESSED_SIZE (10ULL * 1024 * 1024 * 1024) // 10 GB
#define MAX_COMPRESSED_SIZE (12ULL * 1024 * 1024 * 1024) // 12 GB

static inline int validate_size(uint64_t size, uint64_t max_size, const char *name) {
    if (size == 0) {
        PyErr_Format(PyExc_ValueError, "%s is zero", name);
        return -1;
    }
    if (size > max_size) {
        PyErr_Format(PyExc_ValueError, "%s (%llu bytes) exceeds maximum size (%llu bytes)",
                     name, (unsigned long long)size, (unsigned long long)max_size);
        return -1;
    }
    return 0;
}

static inline void* safe_malloc(size_t size) {
    if (size == 0) {
        PyErr_SetString(PyExc_ValueError, "Cannot allocate zero bytes");
        return NULL;
    }
    if (size > SIZE_MAX / 2) {
        PyErr_Format(PyExc_MemoryError, "Allocation size (%zu bytes) is too large", size);
        return NULL;
    }

    void *ptr = malloc(size);
    if (!ptr) {
        PyErr_NoMemory();
    }
    return ptr;
}


// ---- Public API ----

int compress_file(const char *src_path, const char *dst_path,
                  AlgoID algo, Strategy strategy,
                  int level);

int decompress_file(const char *src_path, const char *dst_path,
                    AlgoID algo);

const char *get_default_backend_for_strategy(Strategy strat);


#endif // COMMON_H
