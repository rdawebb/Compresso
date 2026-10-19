#ifndef LE_H
#define LE_H

#include <stdint.h>

// Every on-disk format here (gzip per RFC 1952, the .comp header, snappy's
// framing) stores multi-byte fields little-endian, whatever the host order

static inline void put_le(unsigned char *p, uint64_t v, int bytes) {
  for (int i = 0; i < bytes; i++)
    p[i] = (unsigned char)(v >> (8 * i));
}

static inline uint64_t get_le(const unsigned char *p, int bytes) {
  uint64_t v = 0;
  for (int i = 0; i < bytes; i++)
    v |= (uint64_t)p[i] << (8 * i);
  return v;
}

#endif // LE_H
