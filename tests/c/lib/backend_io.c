#include "backend_io.h"
#include "../unity.h"
#include <stdio.h>
#include <stdlib.h>

unsigned char *read_fixture(const char *path, size_t *out_size) {
  FILE *f = fopen(path, "rb");
  if (!f)
    return NULL;

  fseek(f, 0, SEEK_END);
  long size = ftell(f);
  fseek(f, 0, SEEK_SET);

  unsigned char *buffer = malloc(size > 0 ? (size_t)size : 1);
  if (!buffer || size < 0 || fread(buffer, 1, (size_t)size, f) != (size_t)size) {
    free(buffer);
    fclose(f);
    return NULL;
  }

  fclose(f);
  *out_size = (size_t)size;
  return buffer;
}

static unsigned char *drain(FILE *f, size_t *out_size) {
  long size = ftell(f);
  if (size < 0)
    return NULL;
  rewind(f);

  unsigned char *buffer = malloc(size > 0 ? (size_t)size : 1);
  if (!buffer || fread(buffer, 1, (size_t)size, f) != (size_t)size) {
    free(buffer);
    return NULL;
  }

  *out_size = (size_t)size;
  return buffer;
}

// `decompress` selects the op; `level` and `orig_size` go to whichever it is
static int run_stream(const CBackend *backend, int decompress,
                      const unsigned char *input, size_t input_size, int level,
                      uint64_t orig_size, unsigned char **output,
                      size_t *output_size) {
  *output = NULL;
  *output_size = 0;

  FILE *src = tmpfile();
  FILE *dst = tmpfile();
  if (!src || !dst ||
      fwrite(input, 1, input_size, src) != input_size) {
    if (src)
      fclose(src);
    if (dst)
      fclose(dst);
    return -1;
  }
  rewind(src);

  int rc = decompress
               ? backend->decompress_stream(src, dst, orig_size, NULL)
               : backend->compress_stream(src, dst, level, NULL);

  if (rc == 0) {
    *output = drain(dst, output_size);
    if (!*output)
      rc = -1;
  }

  fclose(src);
  fclose(dst);
  return rc;
}

int stream_compress_bytes(const CBackend *backend, const unsigned char *input,
                          size_t input_size, int level, unsigned char **output,
                          size_t *output_size) {
  return run_stream(backend, 0, input, input_size, level, 0, output,
                    output_size);
}

int stream_decompress_bytes(const CBackend *backend, const unsigned char *input,
                            size_t input_size, uint64_t orig_size,
                            unsigned char **output, size_t *output_size) {
  return run_stream(backend, 1, input, input_size, 0, orig_size, output,
                    output_size);
}

void assert_stream_round_trip(const CBackend *backend,
                              const unsigned char *input, size_t input_size,
                              int level) {
  unsigned char *compressed = NULL, *decompressed = NULL;
  size_t compressed_size = 0, decompressed_size = 0;

  TEST_ASSERT_EQUAL_INT(0, stream_compress_bytes(backend, input, input_size,
                                                 level, &compressed,
                                                 &compressed_size));
  TEST_ASSERT_EQUAL_INT(
      0, stream_decompress_bytes(backend, compressed, compressed_size,
                                 input_size, &decompressed,
                                 &decompressed_size));
  TEST_ASSERT_EQUAL_size_t(input_size, decompressed_size);
  if (input_size > 0)
    TEST_ASSERT_EQUAL_MEMORY(input, decompressed, input_size);

  free(compressed);
  free(decompressed);
}
