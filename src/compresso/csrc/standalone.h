#ifndef STANDALONE_H
#define STANDALONE_H

#include "context.h" // CoreContext
#include "formats.h"
#include "fsutil.h" // OutputTarget
#include <stdio.h>

// The KIND_CONTAINER row for `format`, or NULL
const FormatDesc *find_standalone_format(Format format);

// Compress a file to `fmt`'s container through `out`; `ctx` is NULL-tolerant;
// returns 0 (including a SKIP), -1 with an exception set, or COMP_CANCELLED
int standalone_compress(const FormatDesc *fmt, const char *input_path,
                        const OutputTarget *out, int level, CoreContext *ctx);

// Decompress a `fmt` container through `out`; `input` is already open on
// `input_path` (NULL to open it)
int standalone_decompress(const FormatDesc *fmt, const char *input_path,
                          FILE *input, const OutputTarget *out,
                          CoreContext *ctx);

// The original filename a gzip header stores, or NULL
char *gzip_original_name(const char *compressed_path);

#endif // STANDALONE_H
