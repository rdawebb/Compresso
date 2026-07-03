#ifndef ARCHIVE_H
#define ARCHIVE_H

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <time.h>

// ---- Archive Entry Types ----

typedef enum {
  ENTRY_FILE = 0,
  ENTRY_DIR = 1,
  ENTRY_SYMLINK = 2,
  ENTRY_SPECIAL = 3
} EntryType;

// ---- Archive Entry Metadata ----

typedef struct {
  char *path;           // Relative path within the archive
  EntryType type;       // Type of the entry (file, dir, symlink)
  uint64_t size;        // Uncompressed size (0 for directories)
  time_t mtime;         // Modified time
  uint32_t mode;        // Unix permissions
  char *symlink_target; // Target of the symlink (if applicable)
  void *internal_data;  // Backend-specific data
} ArchiveEntry;

// ---- Archive Backend Interface ----

typedef struct CArchive {
  const char *name;
  uint8_t id;

  // Capability checks
  int (*is_available)(void);
  int (*supports_compression)(void);
  int (*requires_external_compression)(void);
  int (*supports_streaming)(void);

  // Writing (Creating Archives)
  void *(*create_writer)(const char *output_path, int compression_level);
  int (*add_entry)(void *writer, const ArchiveEntry *entry, FILE *data);
  int (*close_writer)(void *writer);

  // Reading (Extracting Archives)
  void *(*create_reader)(const char *input_path);
  int (*get_entry_count)(void *reader);
  int (*get_next_entry)(void *reader, ArchiveEntry *entry);
  int (*extract_entry_data)(void *reader, FILE *output);
  int (*skip_entry_data)(void *reader);
  int (*reset_reader)(void *reader);
  int (*close_reader)(void *reader);
} CArchive;

// ---- Extraction Policy ----

typedef struct {
  int allow_symlinks; // 0 = deny (default), 1 = allow, 2 = rewrite to regular
                      // files
  int allow_absolute_paths; // always 0; field exists for documentation/future
                            // use
  int overwrite_existing;   // 0 = error, 1 = skip, 2 = overwrite
  int allow_special_files;  // 0 = reject device nodes, FIFOs, sockets (default)
  int preserve_permissions; // 1 = restore mode bits, 0 = apply umask
  int preserve_timestamps;  // 1 = restore mtime, 0 = use current time
  uint32_t max_depth;       // maximum recursive nesting depth (0 = unlimited,
                            // recommend 32)
  uint64_t max_total_size;  // maximum total extracted bytes (0 = unlimited)
} ExtractionPolicy;

ExtractionPolicy extraction_policy_default(void); // returns safe defaults

// ---- Archive Registry ----

const CArchive *find_archive_by_name(const char *name);
const CArchive *find_archive_by_id(uint8_t id);
PyObject *get_archive_capabilities(void);

// ---- Archive Backend Getters ----

const CArchive *get_tar_archive(void);
const CArchive *get_zip_archive(void);

// ---- Archive IDs ----

typedef enum {
  ARCHIVE_NONE = 0,
  ARCHIVE_TAR = 1,
  ARCHIVE_ZIP = 2,
  ARCHIVE_7Z = 3
} ArchiveID;

// ---- Format Detection ----

typedef enum {
  FORMAT_UNKNOWN = 0,

  // Single-file formats
  FORMAT_COMPRESSO = 1,
  FORMAT_GZIP = 2,
  FORMAT_BZIP2 = 3,
  FORMAT_XZ = 4,
  FORMAT_ZSTD = 5,
  FORMAT_LZ4 = 6,

  // Multi-file formats with built-in compression
  FORMAT_ZIP = 10,
  FORMAT_7Z = 11,

  // Multi-file formats without built-in compression
  FORMAT_TAR = 20
} Format;

Format detect_format_from_magic_bytes(const unsigned char *magic, size_t size);
Format detect_format_from_path(const char *path);
Format detect_format_from_extension(const char *path);

int format_is_archive(Format format);

const char *format_name_string(Format format);
Format format_from_name(const char *name);

// ---- Operation Modes ----

typedef enum { MODE_SINGLE_FILE = 0, MODE_ARCHIVE = 1 } OperationMode;

OperationMode get_operation_mode(Format format);

// ---- Compression Pipeline ----

typedef struct {
  ArchiveID archive;     // ARCHIVE_NONE for standalone compression
  Format codec;          // FORMAT_UNKNOWN = no codec (plain tar / zip's
                         // built-in); otherwise a standalone codec Format
  int compression_level; // -1 for default
} CompressionPipeline;

// Map an archive Format to its ArchiveID
ArchiveID archive_id_from_format(Format format);

// Parse a format name into a pipeline
CompressionPipeline pipeline_from_name(const char *name, int level);

// Detect an on-disk file's format into a pipeline (level defaults to -1)
CompressionPipeline detect_pipeline_from_path(const char *path);

// Compose a pipeline's display name into buf
void pipeline_display_name(const CompressionPipeline *p, char *buf,
                           size_t buflen);

// Return non-zero if the pipeline is a supported combination
int pipeline_is_valid(const CompressionPipeline *p);

// ---- High-Level Operations ----

int create_archive(const char *output_path, const CompressionPipeline *pipeline,
                   const char **input_paths, size_t num_paths);

int extract_archive(const char *archive_path, const char *output_dir,
                    const char **files, size_t num_files);

PyObject *list_archive_contents(const char *archive_path);

int convert_archive_format(const char *input_path, const char *output_path,
                           Format new_format, int compression_level);

#endif // ARCHIVE_H
