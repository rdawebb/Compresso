#ifndef ARCHIVE_OPS_H
#define ARCHIVE_OPS_H

#include "../archives.h"
#include <stdio.h>

// Shared by the archive sources

// Release an entry's owned strings and clear it for the next iteration
void entry_reset(ArchiveEntry *e);

// Copies an entry's data from `read(handle, buffer, CODEC_CHUNK)` into
// `output` with the GIL released, as CArchive.extract_entry_data; returns 0,
// 1 if `read` failed (for the caller to raise), or a negative code with an
// exception set
int copy_entry_data(int64_t (*read)(void *, void *, size_t), void *handle,
                    void *buffer, FILE *output, uint64_t max_bytes,
                    uint64_t *bytes_written, const CompErrors *errors,
                    CoreContext *ctx);

// Opens `archive_path` and detects its pipeline and archive backend
FILE *open_archive_source(const char *archive_path, const CArchive **archive,
                          CompressionPipeline *pipe, CoreContext *ctx);

CodecStream *open_codec_source(const FormatDesc *fmt, FILE *src,
                               CoreContext *ctx);

// `drain` reads past libarchive's stop at the end-of-archive records, so the
// stream's end checks (checksum, trailing data) still run
int close_codec_source(CodecStream *stream, FILE *src, int drain);

#endif // ARCHIVE_OPS_H
