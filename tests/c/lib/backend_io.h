// In-memory wrappers around a CBackend's stream ops for the backend tests

#ifndef BACKEND_IO_H
#define BACKEND_IO_H

#include "../../../src/compresso/csrc/common.h"
#include <stddef.h>
#include <stdint.h>

// Read a whole fixture into a malloc'd buffer, or NULL on failure
unsigned char *read_fixture(const char *path, size_t *out_size);

// Run `input` through the backend's stream op via tmpfile()s with a NULL
// context; returns 0 with *output malloc'd (non-NULL even when empty), or
// non-zero with *output NULL
int stream_compress_bytes(const CBackend *backend, const unsigned char *input,
                          size_t input_size, int level, unsigned char **output,
                          size_t *output_size);
int stream_decompress_bytes(const CBackend *backend, const unsigned char *input,
                            size_t input_size, uint64_t orig_size,
                            unsigned char **output, size_t *output_size);

// Compress then decompress `input`, asserting the bytes survive unchanged
void assert_stream_round_trip(const CBackend *backend,
                              const unsigned char *input, size_t input_size,
                              int level);

#endif // BACKEND_IO_H
