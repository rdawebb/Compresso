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
    ENTRY_SYMLINK = 2
} EntryType;


// ---- Archive Entry Metadata ----

typedef struct {
  char *path;             // Relative path within the archive
  EntryType type;         // Type of the entry (file, dir, symlink)
  uint64_t size;          // Uncompressed size (0 for directories)
  time_t mtime;           // Modified time
  uint32_t mode;          // Unix permissions
  char *symlink_target;   // Target of the symlink (if applicable)
  void *internal_data;    // Backend-specific data
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
    void* (*create_writer)(const char *output_path, int compression_level);
    int (*add_entry)(void *writer, const ArchiveEntry *entry, FILE *data);
    int (*close_writer)(void *writer);

    // Reading (Extracting Archives)
    void* (*create_reader)(const char *input_path);
    int (*get_entry_count)(void *reader);
    int (*get_next_entry)(void *reader, ArchiveEntry *entry);
    int (*extract_entry_data)(void *reader, FILE *output);
    int (*skip_entry_data)(void *reader);
    int (*reset_reader)(void *reader);
    int (*close_reader)(void *reader);
} CArchive;


// ---- Archive Registry ----

const CArchive *find_archive_by_name(const char *name);
const CArchive *find_archive_by_id(uint8_t id);
PyObject* get_archive_capabilities(void);


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
    FORMAT_TAR = 20,

    // Combined formats
    FORMAT_TAR_GZ = 30,
    FORMAT_TAR_BZ2 = 31,
    FORMAT_TAR_XZ = 32,
    FORMAT_TAR_ZST = 33,
    FORMAT_TAR_LZ4 = 34
} Format;

Format detect_format_from_magic_bytes(const unsigned char *magic, size_t size);
Format detect_format_from_path(const char *path);
Format detect_format_from_extension(const char *path);

const char* format_name(Format format);
int format_is_archive(Format format);
int format_is_combined(Format format);

Format format_get_archive(Format format);
Format format_get_compression(Format format);

const char* format_name_string(Format format);
Format format_from_name(const char *name);


// ---- Operation Modes ----

typedef enum {
    MODE_SINGLE_FILE = 0,
    MODE_ARCHIVE = 1
} OperationMode;

OperationMode get_operation_mode(Format format);


// ---- High-Level Operations ----

int create_archive(const char *output_path, Format format,
                   const char **input_paths, size_t num_paths,
                   int compression_level);

int extract_archive(const char *archive_path, const char *output_dir,
                    const char **files, size_t num_files);

PyObject* list_archive_contents(const char *archive_path);

int convert_archive_format(const char *input_path, const char *output_path,
                           Format new_format, int compression_level);

#endif // ARCHIVE_H
