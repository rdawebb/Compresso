#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdio.h>
#include <string.h>
#include <ctype.h>
#include "archives.h"


// ---- Magic Byte Constants ----

// Compression formats
#define MAGIC_GZIP_1 0x1f
#define MAGIC_GZIP_2 0x8b

#define MAGIC_BZIP2_1 'B'
#define MAGIC_BZIP2_2 'Z'

static const unsigned char MAGIC_XZ[] = {0xFD, 0x37, 0x7A, 0x58, 0x5A, 0x00};
static const unsigned char MAGIC_ZSTD[] = {0x28, 0xB5, 0x2F, 0xFD};
static const unsigned char MAGIC_LZ4[] = {0x04, 0x22, 0x4D, 0x18};

// Archive formats
#define MAGIC_ZIP_1 'P'
#define MAGIC_ZIP_2 'K'
#define MAGIC_ZIP_3 0x03
#define MAGIC_ZIP_4 0x04

static const unsigned char MAGIC_7Z[] = {'7', 'z', 0xBC, 0xAF, 0x27, 0x1C};

// Compresso format
#define MAGIC_COMP_1 'C'
#define MAGIC_COMP_2 'O'
#define MAGIC_COMP_3 'M'
#define MAGIC_COMP_4 'P'

// TAR: 'ustar' at offset 257
#define TAR_MAGIC_OFFSET 257
static const char TAR_MAGIC[] = "ustar";


// ---- Format Detection Functions ----

Format detect_format_from_magic_bytes(const unsigned char *magic, size_t size)
{
    if (!magic || size < 2) {
        return FORMAT_UNKNOWN;
    }

    // Gzip (only needs 2 bytes)
    if (magic[0] == MAGIC_GZIP_1 &&
        magic[1] == MAGIC_GZIP_2) {
        return FORMAT_GZIP;
    }

    // Bzip2 (only needs 2 bytes)
    if (magic[0] == MAGIC_BZIP2_1 &&
        magic[1] == MAGIC_BZIP2_2) {
        return FORMAT_BZIP2;
    }

    if (size < 4) {
        return FORMAT_UNKNOWN;
    }

    // Compresso
    if (magic[0] == MAGIC_COMP_1 &&
        magic[1] == MAGIC_COMP_2 &&
        magic[2] == MAGIC_COMP_3 &&
        magic[3] == MAGIC_COMP_4) {
        return FORMAT_COMPRESSO;
    }

    // XZ
    if (size >= 6 && memcmp(magic, MAGIC_XZ, 6) == 0) {
        return FORMAT_XZ;
    }

    // Zstd
    if (size >= 4 && memcmp(magic, MAGIC_ZSTD, 4) == 0) {
        return FORMAT_ZSTD;
    }

    // LZ4
    if (size >= 4 && memcmp(magic, MAGIC_LZ4, 4) == 0) {
        return FORMAT_LZ4;
    }

    // ZIP
    if (magic[0] == MAGIC_ZIP_1 &&
        magic[1] == MAGIC_ZIP_2 &&
        magic[2] == MAGIC_ZIP_3 &&
        magic[3] == MAGIC_ZIP_4) {
        return FORMAT_ZIP;
    }

    // 7z
    if (size >= 6 && memcmp(magic, MAGIC_7Z, 6) == 0) {
        return FORMAT_7Z;
    }

    return FORMAT_UNKNOWN;
}

Format detect_format_from_path(const char *path)
{
    if (!path) {
        return FORMAT_UNKNOWN;
    }

    FILE *f = fopen(path, "rb");
    if (!f) {
        return FORMAT_UNKNOWN;
    }

    // Magic bytes detection
    unsigned char magic[512];
    size_t bytes_read = fread(magic, 1, sizeof(magic), f);
    if (bytes_read < 4) {
        fclose(f);
        return FORMAT_UNKNOWN;
    }

    Format format = detect_format_from_magic_bytes(magic, bytes_read);
    if (format != FORMAT_UNKNOWN) {
        fclose(f);

        // Check for combined formats
        if (format == FORMAT_GZIP || format == FORMAT_BZIP2 || format == FORMAT_XZ || format == FORMAT_ZSTD || format == FORMAT_LZ4) {
            const char *ext = strrchr(path, '.');
            if (ext && ext > path + 4) {
                // Check for tar combined formats
                const char *prev = ext - 1;
                while (prev > path && *prev != '.') prev--;

                if (prev > path && strncmp(prev, ".tar", 4) == 0) {
                    if (format == FORMAT_GZIP) return FORMAT_TAR_GZ;
                    if (format == FORMAT_BZIP2) return FORMAT_TAR_BZ2;
                    if (format == FORMAT_XZ) return FORMAT_TAR_XZ;
                    if (format == FORMAT_ZSTD) return FORMAT_TAR_ZST;
                    if (format == FORMAT_LZ4) return FORMAT_TAR_LZ4;
                }
            }
        }
        return format;
    }
    // Check for TAR format
    if (bytes_read >= TAR_MAGIC_OFFSET + 5) {
        if (memcmp(magic + TAR_MAGIC_OFFSET, TAR_MAGIC, 5) == 0) {
            fclose(f);
            return FORMAT_TAR;
        }
    }
    fclose(f);

    // If magic bytes detection fails, try extension-based detection
    return detect_format_from_extension(path);
}

Format detect_format_from_extension(const char *path)
{
    if (!path) {
        return FORMAT_UNKNOWN;
    }

    const char *ext = strrchr(path, '.');
    if (!ext) {
        return FORMAT_UNKNOWN;
    }
    ext++; // Skip the dot

    // Convert to lowercase
    char lower_ext[32];
    size_t i;
    for (i = 0; i < sizeof(lower_ext) - 1 && ext[i]; i++) {
        lower_ext[i] = tolower(ext[i]);
    }
    lower_ext[i] = '\0';

    // Single-file formats
    if (strcmp(lower_ext, "gz") == 0) return FORMAT_GZIP;
    if (strcmp(lower_ext, "bz2") == 0) return FORMAT_BZIP2;
    if (strcmp(lower_ext, "xz") == 0) return FORMAT_XZ;
    if (strcmp(lower_ext, "zst") == 0 || strcmp(lower_ext, "zstd") == 0) return FORMAT_ZSTD;
    if (strcmp(lower_ext, "lz4") == 0) return FORMAT_LZ4;
    if (strcmp(lower_ext, "comp") == 0) return FORMAT_COMPRESSO;

    // Archive formats
    if (strcmp(lower_ext, "zip") == 0) return FORMAT_ZIP;
    if (strcmp(lower_ext, "7z") == 0) return FORMAT_7Z;
    if (strcmp(lower_ext, "tar") == 0) return FORMAT_TAR;

    // Combined formats
    if (strcmp(lower_ext, "tgz") == 0) return FORMAT_TAR_GZ;

    const char *prev = ext - 1;
    while (prev > path && *prev != '.') prev--;

    if (prev > path) {
        size_t prev_len = strlen(prev);
        if (prev_len == 3 && strncmp(prev + 1, "tar", 3) == 0) {
            if (strcmp(lower_ext, "gz") == 0) return FORMAT_TAR_GZ;
            if (strcmp(lower_ext, "bz2") == 0) return FORMAT_TAR_BZ2;
            if (strcmp(lower_ext, "xz") == 0) return FORMAT_TAR_XZ;
            if (strcmp(lower_ext, "zst") == 0 || strcmp(lower_ext, "zstd") == 0) return FORMAT_TAR_ZST;
            if (strcmp(lower_ext, "lz4") == 0) return FORMAT_TAR_LZ4;
        }
    }

    return FORMAT_UNKNOWN;
}


// ---- Format Information ----

const char* format_name(Format format)
{
    switch (format) {
        case FORMAT_COMPRESSO: return "compresso";
        case FORMAT_GZIP:      return "gzip";
        case FORMAT_BZIP2:     return "bzip2";
        case FORMAT_XZ:        return "xz";
        case FORMAT_ZSTD:      return "zstd";
        case FORMAT_LZ4:       return "lz4";
        case FORMAT_ZIP:       return "zip";
        case FORMAT_7Z:        return "7z";
        case FORMAT_TAR:       return "tar";
        case FORMAT_TAR_GZ:    return "tar.gz";
        case FORMAT_TAR_BZ2:   return "tar.bz2";
        case FORMAT_TAR_XZ:    return "tar.xz";
        case FORMAT_TAR_ZST:   return "tar.zst";
        case FORMAT_TAR_LZ4:   return "tar.lz4";
        default:               return "unknown";
    }
}

int format_is_archive(Format format)
{
    switch (format) {
        case FORMAT_ZIP:
        case FORMAT_7Z:
        case FORMAT_TAR:
        case FORMAT_TAR_GZ:
        case FORMAT_TAR_BZ2:
        case FORMAT_TAR_XZ:
        case FORMAT_TAR_ZST:
        case FORMAT_TAR_LZ4:
            return 1;
        default:
            return 0;
    }
}

int format_is_combined(Format format)
{
    switch (format) {
        case FORMAT_TAR_GZ:
        case FORMAT_TAR_BZ2:
        case FORMAT_TAR_XZ:
        case FORMAT_TAR_ZST:
        case FORMAT_TAR_LZ4:
            return 1;
        default:
            return 0;
    }
}

Format format_get_archive(Format format)
{
    switch (format) {
        case FORMAT_TAR_GZ:
        case FORMAT_TAR_BZ2:
        case FORMAT_TAR_XZ:
        case FORMAT_TAR_ZST:
        case FORMAT_TAR_LZ4:
            return FORMAT_TAR;
        default:
            return FORMAT_UNKNOWN;
    }
}

Format format_get_compression(Format format)
{
    switch (format) {
        case FORMAT_TAR_GZ:   return FORMAT_GZIP;
        case FORMAT_TAR_BZ2:  return FORMAT_BZIP2;
        case FORMAT_TAR_XZ:   return FORMAT_XZ;
        case FORMAT_TAR_ZST:  return FORMAT_ZSTD;
        case FORMAT_TAR_LZ4:  return FORMAT_LZ4;
        default:              return FORMAT_UNKNOWN;
    }
}

const char* format_name_string(Format format)
{
    switch (format) {
        case FORMAT_COMPRESSO: return "compresso";
        case FORMAT_GZIP:      return "gzip";
        case FORMAT_BZIP2:     return "bzip2";
        case FORMAT_XZ:        return "xz";
        case FORMAT_ZSTD:      return "zstd";
        case FORMAT_LZ4:       return "lz4";
        case FORMAT_ZIP:       return "zip";
        case FORMAT_7Z:        return "7z";
        case FORMAT_TAR:       return "tar";
        case FORMAT_TAR_GZ:    return "tar.gz";
        case FORMAT_TAR_BZ2:   return "tar.bz2";
        case FORMAT_TAR_XZ:    return "tar.xz";
        case FORMAT_TAR_ZST:   return "tar.zst";
        case FORMAT_TAR_LZ4:   return "tar.lz4";
        default:               return "unknown";
    }
}

Format format_from_name(const char *name)
{
    if (!name) return FORMAT_UNKNOWN;

    if (strcmp(name, "compresso") == 0) return FORMAT_COMPRESSO;
    if (strcmp(name, "gzip") == 0 || strcmp(name, "gz") == 0) return FORMAT_GZIP;
    if (strcmp(name, "bzip2") == 0 || strcmp(name, "bz2") == 0) return FORMAT_BZIP2;
    if (strcmp(name, "xz") == 0 || strcmp(name, "lzma") == 0) return FORMAT_XZ;
    if (strcmp(name, "zstd") == 0 || strcmp(name, "zst") == 0) return FORMAT_ZSTD;
    if (strcmp(name, "lz4") == 0) return FORMAT_LZ4;
    if (strcmp(name, "zip") == 0) return FORMAT_ZIP;
    if (strcmp(name, "7z") == 0) return FORMAT_7Z;
    if (strcmp(name, "tar") == 0) return FORMAT_TAR;
    if (strcmp(name, "tar.gz") == 0 || strcmp(name, "tgz") == 0) return FORMAT_TAR_GZ;
    if (strcmp(name, "tar.bz2") == 0 || strcmp(name, "tbz2") == 0) return FORMAT_TAR_BZ2;
    if (strcmp(name, "tar.xz") == 0 || strcmp(name, "txz") == 0) return FORMAT_TAR_XZ;
    if (strcmp(name, "tar.zst") == 0 || strcmp(name, "tzst") == 0) return FORMAT_TAR_ZST;
    if (strcmp(name, "tar.lz4") == 0 || strcmp(name, "tlz4") == 0) return FORMAT_TAR_LZ4;

    return FORMAT_UNKNOWN;
}


// ---- Operation Mode ----

OperationMode get_operation_mode(Format format)
{
    if (format_is_archive(format)) {
        return MODE_ARCHIVE;
    } else {
        return MODE_SINGLE_FILE;
    }
}
