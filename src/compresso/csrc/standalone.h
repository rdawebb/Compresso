#ifndef STANDALONE_H
#define STANDALONE_H

#include "archives.h" // Format
#include "context.h"  // CoreContext
#include "fsutil.h"   // OutputTarget
#include "levels.h"
#include <stdint.h>
#include <stdio.h>

typedef struct {
  Format format;
  const char *name;
  const char *extension; // Primary extension
  LevelRange levels;

  // Compress a file to standalone format through `out`; `ctx` is
  // NULL-tolerant; returns 0 (including a SKIP), -1 with an exception set, or
  // COMP_CANCELLED
  int (*compress_file)(const char *input_path, const OutputTarget *out,
                       int level, CoreContext *ctx);

  // Decompress a standalone format file through `out`
  int (*decompress_file)(const char *input_path, const OutputTarget *out,
                         CoreContext *ctx);

  // Get original filename from compressed file, or NULL if not stored
  char *(*get_original_name)(const char *compressed_path);

  // Check if file is this format
  int (*is_format)(const unsigned char *magic, size_t size);

} StandaloneFormat;

// Get standalone format handlers
const StandaloneFormat *get_gzip_format(void);
const StandaloneFormat *get_bzip2_format(void);
const StandaloneFormat *get_xz_format(void);
const StandaloneFormat *get_zstd_format(void);
const StandaloneFormat *get_lz4_format(void);

const StandaloneFormat *find_standalone_format(Format format);

#endif // STANDALONE_H
