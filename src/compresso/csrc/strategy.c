#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <string.h>
#include "common.h"


// ---- Backend Strategy ----

Strategy strategy_from_string(const char *str)
{
    if (!str) return STRAT_BALANCED;
    if (strcmp(str, "fast") == 0) return STRAT_FAST;
    if (strcmp(str, "max_ratio") == 0) return STRAT_MAX_RATIO;
    return STRAT_BALANCED;
}

AlgoID algo_from_string(const char *str)
{
    if (!str || str[0] == '\0') return ALGO_NONE;
    if (strcmp(str, "zlib") == 0) return ALGO_ZLIB;
    if (strcmp(str, "bzip2") == 0) return ALGO_BZIP2;
    if (strcmp(str, "lzma") == 0) return ALGO_LZMA;
    if (strcmp(str, "zstd") == 0) return ALGO_ZSTD;
    if (strcmp(str, "lz4") == 0) return ALGO_LZ4;
    if (strcmp(str, "snappy") == 0) return ALGO_SNAPPY;
    return ALGO_NONE;
}

static const CBackend *
choose_backend(Strategy strat)
{
    init_backends();

    const CBackend *zlib    = NULL;
    const CBackend *bzip2   = NULL;
    const CBackend *lzma    = NULL;
    const CBackend *zstd    = NULL;
    const CBackend *lz4     = NULL;
    const CBackend *snappy  = NULL;

    for (size_t i = 0; i < num_registered_backends; i++) {
        const CBackend *b = registered_backends[i];
        switch (b->id) {
            case ALGO_ZLIB:     zlib    = b; break;
            case ALGO_BZIP2:    bzip2   = b; break;
            case ALGO_LZMA:     lzma    = b; break;
            case ALGO_ZSTD:     zstd    = b; break;
            case ALGO_LZ4:      lz4     = b; break;
            case ALGO_SNAPPY:   snappy  = b; break;
            default: break;
        }
    }

    switch (strat) {
        case STRAT_FAST:
            // speed priority
            if (lz4)    return lz4;
            if (snappy) return snappy;
            if (zstd)   return zstd;
            if (zlib)   return zlib;
            if (lzma)   return lzma;
            if (bzip2)  return bzip2;
            break;
        case STRAT_MAX_RATIO:
            // compression ratio priority
            if (lzma)   return lzma;
            if (zstd)   return zstd;
            if (bzip2)  return bzip2;
            if (zlib)   return zlib;
            if (lz4)    return lz4;
            if (snappy) return snappy;
            break;
        case STRAT_BALANCED:
        default:
            if (zstd)   return zstd;
            if (zlib)   return zlib;
            if (lzma)   return lzma;
            if (bzip2)  return bzip2;
            if (lz4)    return lz4;
            if (snappy) return snappy;
            break;
    }

    return NULL;
}

const char *
get_default_backend_for_strategy(Strategy strat)
{
    const CBackend *b = choose_backend(strat);
    return b ? b->name : NULL;
}
