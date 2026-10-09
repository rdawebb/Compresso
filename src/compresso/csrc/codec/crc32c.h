#ifndef CRC32C_H
#define CRC32C_H

#include <stddef.h>
#include <stdint.h>

// Builds the table and picks the fastest path this CPU supports; call once,
// before any other thread can call crc32c
void crc32c_init(void);

// CRC-32C (Castagnoli) of `buf`, continuing from `crc`, which is 0 to start
uint32_t crc32c(uint32_t crc, const void *buf, size_t len);

// The table-driven path crc32c falls back to, which the tests compare with
// the hardware one
uint32_t crc32c_portable(uint32_t crc, const void *buf, size_t len);

#endif // CRC32C_H
