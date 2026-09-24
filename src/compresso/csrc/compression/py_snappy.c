#define PY_SSIZE_T_CLEAN
#define SNAPPY_CHUNK 65536 // 64KB
#include "../common.h"
#include <Python.h>
#include <snappy-c.h>

static int snappy_is_available(void) {
  return 1; // snappy is always available if this code is compiled
}

// ---- Helpers ----

static void write_u32_le(uint32_t value, unsigned char buffer[4]) {
  buffer[0] = (unsigned char)(value & 0xFF);
  buffer[1] = (unsigned char)((value >> 8) & 0xFF);
  buffer[2] = (unsigned char)((value >> 16) & 0xFF);
  buffer[3] = (unsigned char)((value >> 24) & 0xFF);
}

static uint32_t read_u32_le(const unsigned char buffer[4]) {
  return (uint32_t)buffer[0] | ((uint32_t)buffer[1] << 8) |
         ((uint32_t)buffer[2] << 16) | ((uint32_t)buffer[3] << 24);
}

// ---- Stream Compression/Decompression ----

static int snappy_compress_stream(FILE *src, FILE *dst, int level,
                                  CoreContext *ctx) {
  (void)level; // snappy ignores compression level

  size_t max_comp_len = snappy_max_compressed_length(SNAPPY_CHUNK);

  char *input_buffer = (char *)safe_malloc(SNAPPY_CHUNK);
  char *comp_buffer = (char *)safe_malloc(max_comp_len);
  if (!input_buffer || !comp_buffer) {
    free(input_buffer);
    free(comp_buffer);
    return -1; // memory allocation failure
  }

  int return_code = 0;

  Py_BEGIN_ALLOW_THREADS

      for (;;) {
    size_t nread = fread(input_buffer, 1, SNAPPY_CHUNK, src);
    if (ferror(src)) {
      return_code = -1; // read error
      break;
    }

    if (nread == 0) {
      break; // end of file
    }

    int advance = ctx_advance(ctx, nread);
    if (advance != 0) {
      return_code = advance;
      break;
    }

    if (nread > UINT32_MAX) {
      return_code = -1; // chunk size overflow
      break;
    }

    size_t comp_len = max_comp_len;
    snappy_status status =
        snappy_compress(input_buffer, nread, comp_buffer, &comp_len);
    if (status != SNAPPY_OK) {
      return_code = -1; // compression error
      break;
    }

    if (comp_len > UINT32_MAX) {
      return_code = -1; // size overflow
      break;
    }

    unsigned char header[8];
    write_u32_le((uint32_t)nread, header);        // original size
    write_u32_le((uint32_t)comp_len, header + 4); // compressed size

    if (fwrite(header, 1, 8, dst) != 8 || ferror(dst)) {
      return_code = -1; // write error
      break;
    }

    if (fwrite(comp_buffer, 1, comp_len, dst) != comp_len || ferror(dst)) {
      return_code = -1; // write error
      break;
    }
  }

  Py_END_ALLOW_THREADS

      free(input_buffer);
  free(comp_buffer);
  return return_code;
}

static int snappy_decompress_stream(FILE *src, FILE *dst, uint64_t orig_size,
                                    CoreContext *ctx) {
  (void)orig_size; // unused parameter

  size_t max_comp_len = snappy_max_compressed_length(SNAPPY_CHUNK);

  char *comp_buffer = (char *)safe_malloc(max_comp_len);
  char *output_buffer = (char *)safe_malloc(SNAPPY_CHUNK);
  if (!comp_buffer || !output_buffer) {
    free(comp_buffer);
    free(output_buffer);
    return -1; // memory allocation failure
  }

  int return_code = 0;

  Py_BEGIN_ALLOW_THREADS

      for (;;) {
    unsigned char header[8];
    size_t got = fread(header, 1, 8, src);

    if (got == 0) {
      if (feof(src)) {
        break; // end of file
      }
      return_code = -1; // read error
      break;
    }

    if (got != 8) {
      return_code = -1; // incomplete header
      break;
    }

    uint32_t orig_len = read_u32_le(header);
    uint32_t comp_len = read_u32_le(header + 4);

    if (orig_len == 0 && comp_len == 0) {
      break;
    }

    if (orig_len > SNAPPY_CHUNK || comp_len > max_comp_len) {
      return_code = -1; // size too large
      break;
    }

    size_t read_bytes = fread(comp_buffer, 1, comp_len, src);
    if (read_bytes != comp_len || ferror(src)) {
      return_code = -1; // read error
      break;
    }

    // Counts the framing header too, so progress tracks the compressed input
    int advance = ctx_advance(ctx, read_bytes + 8);
    if (advance != 0) {
      return_code = advance;
      break;
    }

    size_t output_len = orig_len;
    snappy_status status =
        snappy_uncompress(comp_buffer, comp_len, output_buffer, &output_len);

    if (status != SNAPPY_OK || output_len != orig_len) {
      return_code = -1; // decompression error
      break;
    }

    if (fwrite(output_buffer, 1, output_len, dst) != output_len ||
        ferror(dst)) {
      return_code = -1; // write error
      break;
    }
  }

  Py_END_ALLOW_THREADS

      free(comp_buffer);
  free(output_buffer);
  return return_code;
}

// ---- Backend Definition ----

static const CBackend snappy_backend = {
    .name = "snappy",
    .id = ALGO_SNAPPY,
    .levels = LEVELS_NONE,
    .is_available = snappy_is_available,
    .compress_stream = snappy_compress_stream,
    .decompress_stream = snappy_decompress_stream,
};

const CBackend *get_snappy_backend(void) { return &snappy_backend; }
