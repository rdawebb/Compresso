#define PY_SSIZE_T_CLEAN
#include "common.h"
#include <Python.h>
#include <string.h>

// ---- Backend Strategy ----

const char *const STRATEGY_NAMES[STRAT_COUNT] = {
    [STRAT_BALANCED] = "balanced",
    [STRAT_FAST] = "fast",
    [STRAT_MAX_RATIO] = "max_ratio",
};

Strategy strategy_from_string(const char *str) {
  if (!str || str[0] == '\0')
    return STRAT_BALANCED;
  for (int s = 0; s < STRAT_COUNT; s++)
    if (strcmp(str, STRATEGY_NAMES[s]) == 0)
      return (Strategy)s;
  return STRAT_UNKNOWN;
}

AlgoID algo_from_string(const char *str) {
  if (!str)
    return ALGO_NONE;
  for (const CBackend *b = BACKENDS; b->name; b++)
    if (strcmp(str, b->name) == 0)
      return (AlgoID)b->id;
  return ALGO_NONE;
}

const char *get_default_backend_for_strategy(Strategy strat) {
  const CBackend *b = choose_backend(strat);
  return b ? b->name : NULL;
}
