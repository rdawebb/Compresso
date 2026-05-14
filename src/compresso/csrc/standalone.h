#ifndef STANDALONE_H
#define STANDALONE_H

#include <stdio.h>
#include <stdint.h>
#include "archives.h"

typedef struct {
    Format format;
    const char *name;
    const char *extension;  // Primary extension
    
    // Compress a file to standalone format
    int (*compress_file)(const char *input_path, 
                        const char *output_path, 
                        int level);
    
    // Decompress a standalone format file
    int (*decompress_file)(const char *input_path, 
                          const char *output_path);
    
    // Get original filename from compressed file, or NULL if not stored
    char* (*get_original_name)(const char *compressed_path);
    
    // Check if file is this format
    int (*is_format)(const unsigned char *magic, size_t size);
    
} StandaloneFormat;

// Get standalone format handlers
const StandaloneFormat* get_gzip_format(void);
const StandaloneFormat* get_bzip2_format(void);
const StandaloneFormat* get_xz_format(void);
const StandaloneFormat* get_zstd_format(void);
const StandaloneFormat* get_lz4_format(void);

const StandaloneFormat* find_standalone_format(Format format);

#endif // STANDALONE_H
