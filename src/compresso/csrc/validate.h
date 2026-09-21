#ifndef VALIDATE_H
#define VALIDATE_H

#include "common.h"

// Native-side invariant checks, run once at the Python/C boundary
int validate_compression_request(AlgoID algo, Strategy strategy, int level,
                                 const CompressionPipeline *pipeline);

// Checks `overwrite_existing` is one of the four values (see fs_resolve_conflict)
int validate_overwrite_arg(int overwrite_existing);

#endif // VALIDATE_H
