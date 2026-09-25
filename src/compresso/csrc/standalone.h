#ifndef STANDALONE_H
#define STANDALONE_H

#include "archives.h" // Format
#include "context.h"  // CoreContext
#include "levels.h"
#include <stdint.h>
#include <stdio.h>

typedef struct {
  Format format;
  const char *name;
  const char *extension; // Primary extension
  LevelRange levels;

  // Compress a file to standalone format; `ctx` is NULL-tolerant
  int (*compress_file)(const char *input_path, const char *output_path,
                       int level, CoreContext *ctx);

  // Decompress a standalone format file
  int (*decompress_file)(const char *input_path, const char *output_path,
                         CoreContext *ctx);

  // Get original filename from compressed file, or NULL if not stored
  char *(*get_original_name)(const char *compressed_path);

  // Check if file is this format
  int (*is_format)(const unsigned char *magic, size_t size);

} StandaloneFormat;

// Compress `input_path` into `output_path` via `fmt`, applying
// `overwrite_existing` to the destination; on success (including SKIP), copies
// the path actually written (which RENAME may have changed) into
// `out_actual_path`; returns 0 on success, -1 on error (PyErr set)
int compress_standalone_file(const StandaloneFormat *fmt,
                             const char *input_path, const char *output_path,
                             int level, int overwrite_existing,
                             char *out_actual_path, size_t out_actual_path_size,
                             CoreContext *ctx);

// Get standalone format handlers
const StandaloneFormat *get_gzip_format(void);
const StandaloneFormat *get_bzip2_format(void);
const StandaloneFormat *get_xz_format(void);
const StandaloneFormat *get_zstd_format(void);
const StandaloneFormat *get_lz4_format(void);

const StandaloneFormat *find_standalone_format(Format format);

#endif // STANDALONE_H
