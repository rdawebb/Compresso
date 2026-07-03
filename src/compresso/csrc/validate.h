#ifndef VALIDATE_H
#define VALIDATE_H

#include "common.h"

// Native-side invariant checks, run once at the Python/C boundary
int validate_compression_request(AlgoID algo, Strategy strategy, int level,
                                 const CompressionPipeline *pipeline);

#endif // VALIDATE_H
