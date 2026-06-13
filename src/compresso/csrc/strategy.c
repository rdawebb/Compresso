#define PY_SSIZE_T_CLEAN
#include "common.h"
#include <Python.h>
#include <string.h>

// ---- Backend Strategy ----

Strategy strategy_from_string(const char *str) {
  if (!str)
    return STRAT_BALANCED;
  if (strcmp(str, "fast") == 0)
    return STRAT_FAST;
  if (strcmp(str, "max_ratio") == 0)
    return STRAT_MAX_RATIO;
  return STRAT_BALANCED;
}

AlgoID algo_from_string(const char *str) {
  if (!str || str[0] == '\0')
    return ALGO_NONE;
  if (strcmp(str, "zlib") == 0)
    return ALGO_ZLIB;
  if (strcmp(str, "bzip2") == 0)
    return ALGO_BZIP2;
  if (strcmp(str, "lzma") == 0)
    return ALGO_LZMA;
  if (strcmp(str, "zstd") == 0)
    return ALGO_ZSTD;
  if (strcmp(str, "lz4") == 0)
    return ALGO_LZ4;
  if (strcmp(str, "snappy") == 0)
    return ALGO_SNAPPY;
  return ALGO_NONE;
}

const char *get_default_backend_for_strategy(Strategy strat) {
  const CBackend *b = choose_backend(strat);
  return b ? b->name : NULL;
}
