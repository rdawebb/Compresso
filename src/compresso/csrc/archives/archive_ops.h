#ifndef ARCHIVE_OPS_H
#define ARCHIVE_OPS_H

#include "../archives.h"
#include <stdio.h>

// Shared by archive_ops.c and extract.c

// Release an entry's owned strings and clear it for the next iteration
void entry_reset(ArchiveEntry *e);

// Whether any component of `path` is exactly ".."
int path_has_parent_segment(const char *path);

// Opens `archive_path` and detects its pipeline and archive backend
FILE *open_archive_source(const char *archive_path, const CArchive **archive,
                          CompressionPipeline *pipe, CoreContext *ctx);

CodecStream *open_codec_source(const FormatDesc *fmt, FILE *src,
                               CoreContext *ctx);

// `drain` reads past libarchive's stop at the end-of-archive records, so the
// stream's end checks (checksum, trailing data) still run
int close_codec_source(CodecStream *stream, FILE *src, int drain);

#endif // ARCHIVE_OPS_H
