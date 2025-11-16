#define PY_SSIZE_T_CLEAN
#define ZSTD_CHUNK 65536 // 64KB
#include <Python.h>
#include <zstd.h>
#include "common.h"

static int
zstd_is_available(void) {
    return 1; // zstd is always available if this code is compiled
}

static size_t
zstd_max_compressed_size(size_t input_size) {
    return ZSTD_compressBound(input_size);
}

static int
zstd_level_from_generic(int level) {
    if (level < ZSTD_minCLevel()) return ZSTD_CLEVEL_DEFAULT;
    if (level > ZSTD_maxCLevel()) return ZSTD_maxCLevel();
    return level;
}


// ---- Buffer Compression/Decompression ----

static int
zstd_compress_buffer(const unsigned char *input, size_t input_size,
                     unsigned char *output, size_t *output_capacity,
                     int level, size_t *output_size)
{
    int zlevel = (level >= 0) ? zstd_level_from_generic(level)
                              : ZSTD_CLEVEL_DEFAULT;
    
    int ret;
    Py_BEGIN_ALLOW_THREADS
    ret = ZSTD_compress(output, *output_capacity, input, input_size, zlevel);
    Py_END_ALLOW_THREADS

    if (ZSTD_isError(ret)) {
        return -1; // compression failed
    }

    *output_size = ret;
    return 0; // success
}

static int
zstd_decompress_buffer(const unsigned char *input, size_t input_size,
                       unsigned char *output, size_t *output_capacity,
                       size_t *output_size)
{
    int ret;
    Py_BEGIN_ALLOW_THREADS
    ret = ZSTD_decompress(output, *output_capacity, input, input_size);
    Py_END_ALLOW_THREADS

    if (ZSTD_isError(ret)) {
        return -1; // decompression failed
    }

    *output_size = ret;
    return 0; // success
}


// ---- Stream Compression/Decompression ----

static int
zstd_compress_stream(FILE *src, FILE *dst, int level)
{
    int zlevel = (level >= 0) ? zstd_level_from_generic(level)
                              : ZSTD_CLEVEL_DEFAULT;
                
    ZSTD_CStream *cstream = ZSTD_createCStream();
    if (!cstream) return -1; // memory allocation failure

    size_t ret = ZSTD_initCStream(cstream, zlevel);
    if (ZSTD_isError(ret)) {
        ZSTD_freeCStream(cstream);
        return -1; // initialisation failure
    }

    unsigned char input[ZSTD_CHUNK];
    unsigned char output[ZSTD_CHUNK];

    int err = 0;
    Py_BEGIN_ALLOW_THREADS

    for (;;) {
      size_t read = fread(input, 1, ZSTD_CHUNK, src);
      if (ferror(src)) {
          err = -1;
          break;
      }

      int last_chunk = feof(src);

      ZSTD_inBuffer inbuf = { input, read, 0 };

      while (inbuf.pos < inbuf.size || last_chunk) {
          ZSTD_outBuffer outbuf = { output, ZSTD_CHUNK, 0 };
          ZSTD_EndDirective mode = last_chunk ? ZSTD_e_end : ZSTD_e_continue;

          size_t r = ZSTD_compressStream2(cstream, &outbuf, &inbuf, mode);
          if (ZSTD_isError(r)) {
              err = -1;
              break;
          }

          if (outbuf.pos > 0) {
              if (fwrite(output, 1, outbuf.pos, dst) != outbuf.pos || ferror(dst)) {
                  err = -1;
                  break;
              }
          }

          if (!last_chunk && inbuf.pos == inbuf.size && outbuf.pos == 0) {
              break; // need more input
          }

          if (last_chunk && r == 0) {
              break; // finished
          }
      }

      if (err || last_chunk) {
          break;
      }
    }

    Py_END_ALLOW_THREADS

    ZSTD_freeCStream(cstream);

    return err ? -1 : 0; // success or failure
}

static int
zstd_decompress_stream(FILE *src, FILE *dst, uint64_t orig_size)
{
    (void)orig_size; // unused

    ZSTD_DStream *dstream = ZSTD_createDStream();
    if (!dstream) return -1; // memory allocation failure

    size_t ret = ZSTD_initDStream(dstream);
    if (ZSTD_isError(ret)) {
        ZSTD_freeDStream(dstream);
        return -1; // initialisation failure
    }

    unsigned char input[ZSTD_CHUNK];
    unsigned char output[ZSTD_CHUNK];

    int err = 0;
    Py_BEGIN_ALLOW_THREADS

    size_t input_size = 0;
    size_t input_pos = 0;

    for (;;) {
        if (input_pos == input_size) {
            input_size = fread(input, 1, ZSTD_CHUNK, src);
            if (ferror(src)) {
                err = -1;
                break;
            }
            input_pos = 0;
            
            if (input_size == 0) {
                break; // end of input
            }
        }

        ZSTD_inBuffer inbuf = { input + input_pos, input_size - input_pos, 0 };
        ZSTD_outBuffer outbuf = { output, ZSTD_CHUNK, 0 };

        size_t r = ZSTD_decompressStream(dstream, &outbuf, &inbuf);
        if (ZSTD_isError(r)) {
            err = -1;
            break;
        }

        input_pos += inbuf.pos;

        if (outbuf.pos > 0) {
            if (fwrite(output, 1, outbuf.pos, dst) != outbuf.pos || ferror(dst)) {
                err = -1;
                break;
            }
        }

        if (r == 0 && input_pos == input_size && feof(src)) {
            break; // finished
        }
    }

    Py_END_ALLOW_THREADS
    ZSTD_freeDStream(dstream);

    return err ? -1 : 0; // success or failure
}


// ---- Backend Definition ----

static const CBackend zstd_backend = {
    .name = "zstd",
    .id = ALGO_ZSTD,
    .is_available = zstd_is_available,
    .max_compressed_size = zstd_max_compressed_size,
    .compress_buffer = zstd_compress_buffer,
    .decompress_buffer = zstd_decompress_buffer,
    .compress_stream = zstd_compress_stream,
    .decompress_stream = zstd_decompress_stream,
};

const CBackend *get_zstd_backend(void) {
    return &zstd_backend;
}