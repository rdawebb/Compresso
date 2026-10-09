// Makes Unity define TEST_RANGE; the runner generator expands each one
#define UNITY_SUPPORT_TEST_CASES

#include "codec/crc32c.h"
#include "unity.h"
#include <string.h>

// RFC 3720 B.4's 32-byte vectors, each byte `first + i * step`
static const struct {
  const char *name;
  unsigned char first;
  int step;
  uint32_t expected;
} VECTORS[] = {
    {"zeros", 0x00, 0, 0x8A9136AA},
    {"ones", 0xFF, 0, 0x62A8AB43},
    {"incrementing", 0x00, 1, 0x46DD794E},
    {"decrementing", 0x1F, -1, 0x113FDB5C},
};

_Static_assert(sizeof(VECTORS) / sizeof(VECTORS[0]) == 4, "update TEST_RANGE");

void setUp(void) { crc32c_init(); }
void tearDown(void) {}

TEST_RANGE([ 0, 3, 1 ])
void test_rfc3720_vector(int index) {
  unsigned char buf[32];
  for (int i = 0; i < 32; i++)
    buf[i] = (unsigned char)(VECTORS[index].first + i * VECTORS[index].step);
  TEST_ASSERT_EQUAL_HEX32_MESSAGE(VECTORS[index].expected, crc32c(0, buf, 32),
                                  VECTORS[index].name);
  TEST_ASSERT_EQUAL_HEX32_MESSAGE(VECTORS[index].expected,
                                  crc32c_portable(0, buf, 32),
                                  VECTORS[index].name);
}

void test_check_value(void) {
  TEST_ASSERT_EQUAL_HEX32(0xE3069283, crc32c(0, "123456789", 9));
}

void test_empty_input_leaves_the_crc_unchanged(void) {
  TEST_ASSERT_EQUAL_HEX32(0, crc32c(0, "", 0));
  TEST_ASSERT_EQUAL_HEX32(0xE3069283, crc32c(0xE3069283, "", 0));
}

// Every split, length and alignment the 8-byte steps and their byte tails
// can meet, against the portable path
void test_every_split_matches_the_whole(void) {
  unsigned char data[64 + 8];
  for (size_t i = 0; i < sizeof(data); i++)
    data[i] = (unsigned char)(i * 167 + 13);

  for (size_t offset = 0; offset < 8; offset++)
    for (size_t len = 0; len <= 64; len++) {
      uint32_t whole = crc32c_portable(0, data + offset, len);
      TEST_ASSERT_EQUAL_HEX32(whole, crc32c(0, data + offset, len));
      for (size_t split = 0; split <= len; split++) {
        uint32_t crc = crc32c(0, data + offset, split);
        TEST_ASSERT_EQUAL_HEX32(
            whole, crc32c(crc, data + offset + split, len - split));
      }
    }
}
