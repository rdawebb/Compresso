#ifndef ARCHIVE_H
#define ARCHIVE_H

#define PY_SSIZE_T_CLEAN
#include "context.h"
#include "formats.h"
#include "levels.h"
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
  ENTRY_SPECIAL = 3,
  ENTRY_HARDLINK = 4 // Shares the data of an earlier entry; no data of its own
} EntryType;

// ---- Archive Entry Metadata ----

typedef struct {
  char *path;          // Relative path within the archive
  EntryType type;      // Type of the entry (file, dir, symlink)
  uint64_t size;       // Uncompressed size (0 for directories)
  time_t mtime;        // Modified time
  uint32_t mode;       // Unix permissions
  char *link_target;   // A symlink's target, or for a hardlink the archive
                       // path of the entry it shares data with
  void *internal_data; // Backend-specific data

  // Per-entry compression detail, which only a container that compresses each
  // entry separately has; zero means "not recorded"
  int has_compression_detail;
  uint64_t compressed_size; // Stored size of this entry's data
  uint32_t crc;             // CRC-32 of the uncompressed data
  uint16_t method;          // The container's own compression method code
} ArchiveEntry;

// ---- Archive Backend Interface ----

typedef struct CArchive {
  const char *name;
  uint8_t id;
  LevelRange levels; // Applies when no external codec stage is present

  // Capability checks
  int (*is_available)(void);
  int (*supports_compression)(void);
  int (*supports_streaming)(void);

  // Writing (Creating Archives)
  void *(*create_writer)(const char *output_path, int compression_level);

  // `ctx` is NULL-tolerant and covers this entry's data only
  // `source_path` is the entry's original filesystem path, valid for the
  // duration of this call only
  int (*add_entry)(void *writer, const ArchiveEntry *entry, FILE *data,
                   const char *source_path, CoreContext *ctx);

  // Some backends defer the real work to here, so this takes a context too;
  // `discard` abandons a failed archive, and raises nothing, so the failure
  // that caused it stays the one reported
  int (*close_writer)(void *writer, CoreContext *ctx, int discard);

  // Reading (Extracting Archives)
  void *(*create_reader)(const char *input_path);
  // `ctx` (NULL-tolerant) only receives warnings about a still-usable entry
  int (*get_next_entry)(void *reader, ArchiveEntry *entry, CoreContext *ctx);

  // Writes the current entry's data to `output`, refusing to write more than
  // `max_bytes` (UINT64_MAX for no limit) and reporting the byte count through
  // `bytes_written` when it is non-NULL
  int (*extract_entry_data)(void *reader, FILE *output, uint64_t max_bytes,
                            uint64_t *bytes_written, CoreContext *ctx);
  int (*skip_entry_data)(void *reader);
  // `discard` releases a reader whose pass already failed, raising nothing
  int (*close_reader)(void *reader, int discard);
} CArchive;

// ---- Extraction Policy ----

typedef struct {
  int allow_symlinks; // 0 = deny (default), 1 = allow, 2 = rewrite to regular
                      // files
  int overwrite_existing;   // 0 = error, 1 = skip, 2 = overwrite, 3 = rename
  int preserve_permissions; // 1 = restore mode bits, 0 = apply umask

  // 1 = restore them exactly, setuid/setgid/sticky included; 0 = drop those
  // and apply the umask (default), as tar does for a non-root user
  int exact_permissions;
  int preserve_timestamps; // 1 = restore mtime, 0 = use current time

  // 1 = also restore a directory's mode and mtime when it already exists, 0 =
  // only on directories the extraction creates (default); never in SKIP mode,
  // which leaves existing paths alone
  int overwrite_dir_metadata;
  uint32_t max_depth;      // maximum recursive nesting depth (0 = unlimited,
                           // recommend 32)
  uint64_t max_total_size; // maximum total extracted bytes (0 = unlimited)
} ExtractionPolicy;

ExtractionPolicy extraction_policy_default(void); // returns safe defaults

PyObject *get_archive_capabilities(void);

// ---- Format Detection ----

Format detect_format_from_path(const char *path);

// Opens `path` to read and detects its format as detect_format_from_path does;
// returns the stream rewound to the start, or NULL with OSError set
FILE *open_source(const char *path, Format *format);

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

// The pipeline for a file already detected as `f`; `path`'s extension says
// whether a standalone codec wraps a tar
CompressionPipeline pipeline_from_format(Format f, const char *path);

// Compose a pipeline's display name into buf
void pipeline_display_name(const CompressionPipeline *p, char *buf,
                           size_t buflen);

// Return non-zero if the pipeline is a supported combination
int pipeline_is_valid(const CompressionPipeline *p);

// ---- High-Level Operations ----

// `overwrite_existing` follows the same 0-3 scheme as
// `ExtractionPolicy.overwrite_existing`; on success, the path actually written
// (which RENAME may have changed) is copied into `out_actual_path`
int create_archive(const char *output_path, const CompressionPipeline *pipeline,
                   const char **input_paths, size_t num_paths,
                   int overwrite_existing, char *out_actual_path,
                   size_t out_actual_path_size, CoreContext *ctx);

// Entries are validated against `policy` (NULL = extraction_policy_default())
// in a first pass over the archive
int extract_archive(const char *archive_path, const char *output_dir,
                    const char **files, size_t num_files,
                    const ExtractionPolicy *policy, CoreContext *ctx);

// `ctx` (NULL-tolerant) carries signal checks and warnings; there is no
// progress to report
PyObject *list_archive_contents(const char *archive_path, CoreContext *ctx);

#endif // ARCHIVE_H
