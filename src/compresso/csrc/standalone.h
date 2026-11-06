#ifndef STANDALONE_H
#define STANDALONE_H

#include "context.h" // CoreContext
#include "formats.h"
#include "fsutil.h" // OutputTarget
#include <stdio.h>

// The KIND_CONTAINER row for `format`, or NULL
const FormatDesc *find_standalone_format(Format format);

// Compress a file to `fmt`'s container through `out`; returns 0 (including a
// SKIP), -1 with an exception set, or COMP_CANCELLED
int standalone_compress(const FormatDesc *fmt, const char *input_path,
                        const OutputTarget *out, int level, CoreContext *ctx);

// Decompress a `fmt` container through `out`; `input` is already open on
// `input_path` (NULL to open it)
int standalone_decompress(const FormatDesc *fmt, const char *input_path,
                          FILE *input, const OutputTarget *out,
                          CoreContext *ctx);

// The original filename a gzip header stores, or NULL
char *gzip_original_name(const char *compressed_path);

// The uncompressed size an xz index, the zstd or lz4 frame headers, or the
// snappy block headers of `f` state, without decoding; -1 if they don't state
// one, a zstd or lz4 frame written from a stream, or a file those structures
// don't fully describe
int standalone_content_size(FILE *f, Format format, uint64_t *size);

#endif // STANDALONE_H
