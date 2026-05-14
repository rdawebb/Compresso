#include "unity.h"
#include "../../src/compresso/csrc/archives.h"
#include <string.h>

// Forward declaration of the function we're testing
Format detect_format_from_magic_bytes(const unsigned char *magic, size_t size);

void setUp(void) {
    // Set up code if needed
}

void tearDown(void) {
    // Clean up code if needed
}

void test_detect_gzip_format(void) {
    unsigned char gzip_magic[] = {0x1f, 0x8b, 0x08};
    TEST_ASSERT_EQUAL(FORMAT_GZIP, detect_format_from_magic_bytes(gzip_magic, 3));
}

void test_detect_bzip2_format(void) {
    unsigned char bzip2_magic[] = {'B', 'Z', 'h', '9'};
    TEST_ASSERT_EQUAL(FORMAT_BZIP2, detect_format_from_magic_bytes(bzip2_magic, 4));
}

void test_detect_xz_format(void) {
    unsigned char xz_magic[] = {0xfd, 0x37, 0x7a, 0x58, 0x5a, 0x00};
    TEST_ASSERT_EQUAL(FORMAT_XZ, detect_format_from_magic_bytes(xz_magic, 6));
}

void test_detect_zstd_format(void) {
    unsigned char zstd_magic[] = {0x28, 0xb5, 0x2f, 0xfd};
    TEST_ASSERT_EQUAL(FORMAT_ZSTD, detect_format_from_magic_bytes(zstd_magic, 4));
}

void test_detect_lz4_format(void) {
    unsigned char lz4_magic[] = {0x04, 0x22, 0x4d, 0x18};
    TEST_ASSERT_EQUAL(FORMAT_LZ4, detect_format_from_magic_bytes(lz4_magic, 4));
}

void test_detect_zip_format(void) {
    unsigned char zip_magic[] = {'P', 'K', 0x03, 0x04};
    TEST_ASSERT_EQUAL(FORMAT_ZIP, detect_format_from_magic_bytes(zip_magic, 4));
}

void test_detect_compresso_format(void) {
    unsigned char comp_magic[] = {'C', 'O', 'M', 'P'};
    TEST_ASSERT_EQUAL(FORMAT_COMPRESSO, detect_format_from_magic_bytes(comp_magic, 4));
}

void test_detect_7z_format(void) {
    unsigned char sz_magic[] = {'7', 'z', 0xbc, 0xaf, 0x27, 0x1c};
    TEST_ASSERT_EQUAL(FORMAT_7Z, detect_format_from_magic_bytes(sz_magic, 6));
}

void test_detect_with_insufficient_data(void) {
    unsigned char small_buffer[] = {0x1f};
    TEST_ASSERT_EQUAL(FORMAT_UNKNOWN, detect_format_from_magic_bytes(small_buffer, 1));
}

void test_detect_with_null_buffer(void) {
    TEST_ASSERT_EQUAL(FORMAT_UNKNOWN, detect_format_from_magic_bytes(NULL, 10));
}

void test_detect_with_zero_size(void) {
    unsigned char buffer[] = {0x1f, 0x8b};
    TEST_ASSERT_EQUAL(FORMAT_UNKNOWN, detect_format_from_magic_bytes(buffer, 0));
}

void test_detect_unknown_format(void) {
    unsigned char unknown[] = {0xff, 0xff, 0xff, 0xff};
    TEST_ASSERT_EQUAL(FORMAT_UNKNOWN, detect_format_from_magic_bytes(unknown, 4));
}
