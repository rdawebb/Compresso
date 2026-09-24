#ifndef VALIDATE_H
#define VALIDATE_H

#include "common.h"

// Native-side invariant checks, run once at the Python/C boundary

// Checks `level` is -1 or within `levels`, naming `name` in the error
int validate_level(const char *name, LevelRange levels, int level);

// Checks `level` against the backend `algo` (or `strategy`, with no algo)
// resolves to, or against `pipeline`'s codec stage or container when given
int validate_compression_request(AlgoID algo, Strategy strategy, int level,
                                 const CompressionPipeline *pipeline);

// Checks `overwrite_existing` is one of the four values (see fs_resolve_conflict)
int validate_overwrite_arg(int overwrite_existing);

#endif // VALIDATE_H
