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
    if (!b) return;
    if (!b->is_available || !b->compress_buffer || !b->decompress_buffer) return;
    if (!b->is_available()) return;
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
    
    const CBackend *zlib = NULL;
    const CBackend *bzip2 = NULL;
    const CBackend *lzma = NULL;

    for (size_t i = 0; i < num_registered_backends; i++) {
        const CBackend *b = registered_backends[i];
        if (b->id == ALGO_ZLIB) zlib = b;
        if (b->id == ALGO_BZIP2) bzip2 = b;
        if (b->id == ALGO_LZMA) lzma = b;
    }
    
    switch (strat) {
        case STRAT_FAST:
            if (zlib) return zlib;
            if (lzma) return lzma;
            if (bzip2) return bzip2;
            break;
        case STRAT_MAX_RATIO:
            if (lzma) return lzma;
            if (bzip2) return bzip2;
            if (zlib) return zlib;
            break;
        case STRAT_BALANCED:
        default:
            if (zlib) return zlib;
            if (lzma) return lzma;
            if (bzip2) return bzip2;
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

    if (algo_name) {
        backend = find_backend_by_name(algo_name);
        if (!backend) {
            PyErr_SetString(PyExc_ValueError, "Specified compression algorithm not available");
            return -1;
        }
    } else {
        Strategy strat = strategy_from_string(strategy_name);
        backend = choose_backend(strat);
        if (!backend) {
            PyErr_SetString(PyExc_RuntimeError, "No available compression backend found");
            return -1;
        }
    }

    size_t input_size = 0;
    unsigned char *input_buffer = read_file_to_memory(src_path, &input_size);
    if (!input_buffer) {
        return -1; // Error already set
    }

    size_t header_size = sizeof(CHeader);
    size_t max_payload = backend->max_compressed_size(input_size);
    size_t output_capacity = header_size + max_payload;

    unsigned char *output_buffer = (unsigned char *)malloc(output_capacity);
    if (!output_buffer) {
        free(input_buffer);
        PyErr_NoMemory();
        return -1;
    }
    
    CHeader *header = (CHeader *)output_buffer;
    memcpy(header->magic, C_MAGIC, C_MAGIC_LEN);
    header->version = 1;
    header->algo = backend->id;
    header->level = (uint8_t)((level >= 0 && level <= 254) ? level : 255);
    header->flags = 0;
    header->orig_size = (uint64_t)input_size;

    unsigned char *payload = output_buffer + header_size;
    size_t payload_size = 0;

    if (backend->compress_buffer(input_buffer, input_size,
                                 payload, &max_payload,
                                 level, &payload_size) != 0) 
    {
        free(input_buffer);
        free(output_buffer);
        PyErr_SetString(PyExc_RuntimeError, "Compression failed in backend");
        return -1;
    }

    free(input_buffer);

    size_t total_size = header_size + payload_size;
    if (write_memory_to_file(dst_path, output_buffer, total_size) != 0) {
        free(output_buffer);
        return -1; // Error already set
    }

    free(output_buffer);
    return 0; // Success
}

int decompress_file(const char *src_path, const char *dst_path,
                    const char *algo_name)
{
    init_backends();

    size_t input_size = 0;
    unsigned char *input_buffer = read_file_to_memory(src_path, &input_size);
    if (!input_buffer) {
        return -1; // Error already set
    }

    if (input_size < sizeof(CHeader)) {
        free(input_buffer);
        PyErr_SetString(PyExc_ValueError, "Input file too small to be valid");
        return -1;
    }

    CHeader *header = (CHeader *)input_buffer;
    if (memcmp(header->magic, C_MAGIC, C_MAGIC_LEN) != 0) {
        free(input_buffer);
        PyErr_SetString(PyExc_ValueError, "Invalid file magic number");
        return -1;
    }

    if (header->version != 1) {
        free(input_buffer);
        PyErr_SetString(PyExc_ValueError, "Unsupported file version");
        return -1;
    }

    const CBackend *backend = NULL;

    if (algo_name) {
        backend = find_backend_by_name(algo_name);
        if (!backend) {
            free(input_buffer);
            PyErr_SetString(PyExc_ValueError, "Specified decompression algorithm not available or mismatched");
            return -1;
        }
    } else {
        backend = find_backend_by_id(header->algo);
        if (!backend) {
            free(input_buffer);
            PyErr_SetString(PyExc_ValueError, "No available decompression backend found for file");
            return -1;
        }
    }

    size_t orig_size = (size_t)header->orig_size;
    if (orig_size == 0) {
        free(input_buffer);
        PyErr_SetString(PyExc_ValueError, "Invalid original size in header");
        return -1;
    }

    size_t header_size = sizeof(CHeader);
    const unsigned char *payload = input_buffer + header_size;
    size_t payload_size = input_size - header_size;
    size_t output_capacity = (size_t)orig_size;

    unsigned char *output_buffer = (unsigned char *)malloc(output_capacity);
    if (!output_buffer) {
        free(input_buffer);
        PyErr_NoMemory();
        return -1;
    }

    size_t output_size = 0;
    if (backend->decompress_buffer(payload, payload_size,
                                   output_buffer, &orig_size,
                                   &output_size) != 0 || output_size != output_capacity)
    {
        free(output_buffer);
        free(input_buffer);
        PyErr_SetString(PyExc_RuntimeError, "Decompression failed in backend");
        return -1;
    }

    free(input_buffer);

    if (write_memory_to_file(dst_path, output_buffer, output_size) != 0) {
        free(output_buffer);
        return -1; // Error already set
    }

    free(output_buffer);
    return 0; // Success
}