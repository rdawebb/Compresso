#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "common.h"

// Forward declarations
const CBackend *get_zlib_backend(void);
const CBackend *get_bzip2_backend(void);
const CBackend *get_lzma_backend(void);
const CBackend *get_zstd_backend(void);
const CBackend *get_lz4_backend(void);
const CBackend *get_snappy_backend(void);

// Snappy helper
size_t snappy_decompressed_size(const unsigned char *input, size_t input_size);

// Error handling helper
static void
set_backend_error(const CBackend *backend, const char *operation, const char *detail)
{
    const char *name = backend && backend->name ? backend->name : "backend";
    if (detail && detail[0] != '\0') {
        PyErr_Format(
            comp_BackendError,
            "%s %s failed: %s",
            name, operation, detail
        );
    } else {
        PyErr_Format(
            comp_BackendError,
            "%s %s failed",
            name, operation
        );
    }
}


// ---- Backend Registry ----

static const CBackend *backends[] = {
    & (CBackend){0}, // Placeholder for index 0
};

static const CBackend *backend_list[] = {
};

static const CBackend *registered_backends[8];
static size_t num_registered_backends = 0;

static void
register_backend(const CBackend *b) 
{
    if (!b) {
        return;
    }
    if (!b->is_available || !b->compress_buffer || !b->decompress_buffer) {
        return;
    }
    if (!b->is_available()) {
        return;
    }
    if (num_registered_backends < sizeof(registered_backends) / sizeof(registered_backends[0])) {
        registered_backends[num_registered_backends++] = b;
    }    
}

static int backends_init = 0;

static void
init_backends(void) 
{
    if (backends_init) return;
    backends_init = 1;

    register_backend(get_zlib_backend());
    register_backend(get_bzip2_backend());
    register_backend(get_lzma_backend());
    register_backend(get_zstd_backend());
    register_backend(get_lz4_backend());
    register_backend(get_snappy_backend());
}


// ---- Backend Strategy & Lookup ----

Strategy strategy_from_string(const char *str) 
{
    if (!str) return STRAT_BALANCED;
    if (strcmp(str, "fast") == 0) return STRAT_FAST;
    if (strcmp(str, "max_ratio") == 0) return STRAT_MAX_RATIO;
    return STRAT_BALANCED;
}

const CBackend *find_backend_by_name(const char *name) 
{
    if (!name) return NULL;
    init_backends();
    for (size_t i = 0; i < num_registered_backends; i++) {
        if (strcmp(registered_backends[i]->name, name) == 0) {
            return registered_backends[i];
        }
    }
    return NULL;
}

const CBackend *find_backend_by_id(uint8_t id) 
{
    init_backends();
    for (size_t i = 0; i < num_registered_backends; i++) {
        if (registered_backends[i]->id == id) {
            return registered_backends[i];
        }
    }
    return NULL;
}

static const CBackend *
choose_backend(Strategy strat) 
{
    init_backends();

    const CBackend *zlib    = NULL;
    const CBackend *bzip2   = NULL;
    const CBackend *lzma    = NULL;
    const CBackend *zstd    = NULL;
    const CBackend *lz4     = NULL;
    const CBackend *snappy  = NULL;

    for (size_t i = 0; i < num_registered_backends; i++) {
        const CBackend *b = registered_backends[i];
        switch (b->id) {
            case ALGO_ZLIB:     zlib    = b; break;
            case ALGO_BZIP2:    bzip2   = b; break;
            case ALGO_LZMA:     lzma    = b; break;
            case ALGO_ZSTD:     zstd    = b; break;
            case ALGO_LZ4:      lz4     = b; break;
            case ALGO_SNAPPY:   snappy  = b; break;
            default: break;
        }
    }
    
    switch (strat) {
        case STRAT_FAST:
            // speed priority
            if (lz4)    return lz4;
            if (snappy) return snappy;
            if (zstd)   return zstd;
            if (zlib)   return zlib;
            if (lzma)   return lzma;
            if (bzip2)  return bzip2;
            break;
        case STRAT_MAX_RATIO:
            // compression ratio priority
            if (lzma)   return lzma;
            if (zstd)   return zstd;
            if (bzip2)  return bzip2;
            if (zlib)   return zlib;
            if (lz4)    return lz4;
            if (snappy) return snappy;
            break;
        case STRAT_BALANCED:
        default:
            if (zstd)   return zstd;
            if (zlib)   return zlib;
            if (lzma)   return lzma;
            if (bzip2)  return bzip2;
            if (lz4)    return lz4;
            if (snappy) return snappy;
            break;
    }

    return NULL;
}


// ---- I/O Helpers ----

static unsigned char *
read_file_to_memory(const char *path, size_t *out_size)
{
    FILE *f = fopen(path, "rb");
    if (!f) {
        PyErr_SetFromErrnoWithFilename(PyExc_OSError, path);
        return NULL;
    }

    if (fseek(f, 0, SEEK_END) != 0) {
        PyErr_SetFromErrnoWithFilename(PyExc_OSError, path);
        fclose(f);
        return NULL;
    }

    long len = ftell(f);
    if (len < 0) {
        PyErr_SetFromErrnoWithFilename(PyExc_OSError, path);
        fclose(f);
        return NULL;
    }
    rewind(f);

    unsigned char *buffer = (unsigned char *)malloc((size_t)len);
    if (!buffer) {
        PyErr_NoMemory();
        fclose(f);
        return NULL;
    }

    size_t read = fread(buffer, 1, (size_t)len, f);
    if (read != (size_t)len) {
        free(buffer);
        PyErr_SetString(PyExc_IOError, "Failed to read entire file");
        return NULL;
    }

    *out_size = (size_t)len;
    return buffer;
}

static int
write_memory_to_file(const char *path, const unsigned char *data, size_t size)
{
    FILE *f = fopen(path, "wb");
    if (!f) {
        PyErr_SetFromErrnoWithFilename(PyExc_OSError, path);
        return -1;
    }

    size_t written = fwrite(data, 1, size, f);
    int err = ferror(f);
    fclose(f);

    if (err || written != size) {
        PyErr_SetString(PyExc_IOError, "Failed to write entire file");
        return -1;
    }

    return 0;
}


// ---- Public API ----

int compress_file(const char *src_path, const char *dst_path,
                  const char *algo_name, const char *strategy_name,
                  int level)
{
    init_backends();

    const CBackend *backend = NULL;

    if (algo_name && algo_name[0] != '\0') {
        backend = find_backend_by_name(algo_name);
        if (!backend) {
            PyErr_SetString(PyExc_ValueError, "Specified compression algorithm not available");
            return -1;
        }
    } else {
        Strategy strat = strategy_from_string(strategy_name);
        backend = choose_backend(strat);
        if (!backend) {
            PyErr_SetString(comp_Error, "No available compression backend found");
            return -1;
        }
    }

    FILE *src = fopen(src_path, "rb");
    if (!src) {
        PyErr_SetFromErrnoWithFilename(PyExc_OSError, src_path);
        return -1;
    }

    FILE *dst = fopen(dst_path, "wb");
    if (!dst) {
        PyErr_SetFromErrnoWithFilename(PyExc_OSError, dst_path);
        fclose(src);
        return -1;
    }

    if (fseek(src, 0, SEEK_END) != 0) {
        PyErr_SetFromErrnoWithFilename(PyExc_OSError, src_path);
        fclose(src);
        fclose(dst);
        return -1;
    }
    long len = ftell(src);
    if (len < 0) {
        PyErr_SetFromErrnoWithFilename(PyExc_OSError, src_path);
        fclose(src);
        fclose(dst);
        return -1;
    }
    uint64_t orig_size = (uint64_t)len;
    rewind(src);
    
    CHeader header;
    memcpy(header.magic, C_MAGIC, C_MAGIC_LEN);
    header.version = 1;
    header.algo = backend->id;
    header.level = (uint8_t)((level >= 0 && level <= 254) ? level : 255);
    header.flags = 0;
    header.orig_size = orig_size;

    if (fwrite(&header, 1, sizeof(header), dst) != sizeof(header) || ferror(dst)) {
        PyErr_SetString(comp_HeaderError, "Failed to write header to output file");
        fclose(src);
        fclose(dst);
        return -1;
    }

    int return_code = 0;

    if (backend->compress_stream) {
        return_code = backend->compress_stream(src, dst, level);
        if (return_code != 0) {
            set_backend_error(backend, "compression", "streaming compression");
        }
    } else {
        size_t input_size = (size_t)orig_size;
        unsigned char *input_buffer = (unsigned char *)malloc(input_size);
        if (!input_buffer) {
            PyErr_NoMemory();
            return_code = -1;
            goto done;
        }

        size_t read = fread(input_buffer, 1, input_size, src);
        if (read != input_size || ferror(src)) {
            free(input_buffer);
            PyErr_SetString(PyExc_IOError, "Failed to read input file");
            return_code = -1;
            goto done;
        }

        size_t max_payload = backend->max_compressed_size(input_size);
        unsigned char *output_buffer = (unsigned char *)malloc(max_payload);
        if (!output_buffer) {
            free(input_buffer);
            PyErr_NoMemory();
            return_code = -1;
            goto done;
        }

        size_t output_size = 0;
        if (backend->compress_buffer(input_buffer, input_size,
                                     output_buffer, &max_payload,
                                     level, &output_size) != 0)
        {
            free(input_buffer);
            free(output_buffer);
            set_backend_error(backend, "compression", "buffer compression");
            return_code = -1;
            goto done;
        }

        free(input_buffer);

        if (fwrite(output_buffer, 1, output_size, dst) != output_size || ferror(dst)) {
            free(output_buffer);
            PyErr_SetString(PyExc_IOError, "Failed to write compressed data to output file");
            return_code = -1;
            goto done;
        }

        free(output_buffer);
    }

done:
    fclose(src);
    fclose(dst);
    return return_code;
}

int decompress_file(const char *src_path, const char *dst_path,
                    const char *algo_name)
{
    init_backends();

    FILE *src = fopen(src_path, "rb");
    if (!src) {
        PyErr_SetFromErrnoWithFilename(PyExc_OSError, src_path);
        return -1;
    }

    FILE *dst = fopen(dst_path, "wb");
    if (!dst) {
        PyErr_SetFromErrnoWithFilename(PyExc_OSError, dst_path);
        fclose(src);
        return -1;
    }

    CHeader header;
    if (fread(&header, 1, sizeof(header), src) != sizeof(header)) {
        PyErr_SetString(comp_HeaderError, "Failed to read header from input file");
        fclose(src);
        fclose(dst);
        return -1;
    }

    if (memcmp(header.magic, C_MAGIC, C_MAGIC_LEN) != 0) {
        PyErr_SetString(comp_HeaderError, "Invalid file magic number");
        fclose(src);
        fclose(dst);
        return -1;
    }

    if (header.version != 1) {
        PyErr_SetString(comp_HeaderError, "Unsupported file version");
        fclose(src);
        fclose(dst);
        return -1;
    }

    const CBackend *backend = NULL;

    if (algo_name && algo_name[0] != '\0') {
        backend = find_backend_by_name(algo_name);
        if (!backend) {
            PyErr_SetString(PyExc_ValueError, "Specified compression algorithm not available");
            fclose(src);
            fclose(dst);
            return -1;
        }
    } else {
        backend = find_backend_by_id(header.algo);
        if (!backend) {
            PyErr_SetString(comp_HeaderError, "Compression algorithm from file not available");
            fclose(src);
            fclose(dst);
            return -1;
        }
    }
    uint64_t orig_size = header.orig_size;
    if (orig_size == 0) {
        PyErr_SetString(PyExc_ValueError, "Invalid original size in header");
        fclose(src);
        fclose(dst);
        return -1;
    }

    int return_code = 0;

    if (backend->decompress_stream) {
        return_code = backend->decompress_stream(src, dst, orig_size);
        if (return_code != 0) {
            set_backend_error(backend, "decompression", "streaming decompression");
        }
    } else {
        if (fseek(src, 0, SEEK_END) != 0) {
            PyErr_SetFromErrnoWithFilename(PyExc_OSError, src_path);
            return_code = -1;
            goto done;
        }
        long end_pos = ftell(src);
        if (end_pos < 0) {
            PyErr_SetFromErrnoWithFilename(PyExc_OSError, src_path);
            return_code = -1;
            goto done;
        }
        long payload_start = (long)sizeof(CHeader);
        long payload_len = end_pos - payload_start;
        if (payload_len <= 0) {
            PyErr_SetString(PyExc_ValueError, "No compressed data found in file");
            return_code = -1;
            goto done;
        }
        if (fseek(src, payload_start, SEEK_SET) != 0) {
            PyErr_SetFromErrnoWithFilename(PyExc_OSError, src_path);
            return_code = -1;
            goto done;
        }

        size_t comp_size = (size_t)payload_len;
        unsigned char *comp_buffer = (unsigned char *)malloc(comp_size);
        if (!comp_buffer) {
            PyErr_NoMemory();
            return_code = -1;
            goto done;
        }
        size_t read = fread(comp_buffer, 1, comp_size, src);
        if (read != comp_size || ferror(src)) {
            free(comp_buffer);
            PyErr_SetString(PyExc_IOError, "Failed to read compressed data from input file");
            return_code = -1;
            goto done;
        }

        size_t output_capacity;
        if (backend->id == ALGO_SNAPPY) {
            output_capacity = snappy_decompressed_size(comp_buffer, comp_size);
            if (output_capacity == 0) {
                free(comp_buffer);
                PyErr_SetString(PyExc_RuntimeError, "Failed to determine decompressed size for Snappy");
                return_code = -1;
                goto done;
            }
        } else {
            output_capacity = (size_t)orig_size;
        }
        unsigned char *output_buffer = (unsigned char *)malloc(output_capacity);
        if (!output_buffer) {
            free(comp_buffer);
            PyErr_NoMemory();
            return_code = -1;
            goto done;
        }

        size_t output_size = 0;
        if (backend->decompress_buffer(comp_buffer, comp_size,
                                        output_buffer, &output_capacity,
                                        &output_size) != 0 || output_size != output_capacity) {
            free(comp_buffer);
            free(output_buffer);
            set_backend_error(backend, "decompression", "buffer decompression");
            return_code = -1;
            goto done;
        }

        free(comp_buffer);

        if (fwrite(output_buffer, 1, output_size, dst) != output_size || ferror(dst)) {
            free(output_buffer);
            PyErr_SetString(PyExc_IOError, "Failed to write decompressed data to output file");
            return_code = -1;
            goto done;
        }

        free(output_buffer);
    }

done:
    fclose(src);
    fclose(dst);
    return return_code;
}