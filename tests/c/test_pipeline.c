#include "unity.h"
#include "../../src/compresso/csrc/archives.h"
#include <string.h>

void setUp(void) {}
void tearDown(void) {}

// ---- pipeline_from_name ----

void test_name_combined_dotted(void) {
    CompressionPipeline p = pipeline_from_name("tar.gz", 5);
    TEST_ASSERT_EQUAL(ARCHIVE_TAR, p.archive);
    TEST_ASSERT_EQUAL(FORMAT_GZIP, p.codec);
    TEST_ASSERT_EQUAL_INT(5, p.compression_level);
}

void test_name_combined_shorthand(void) {
    CompressionPipeline p = pipeline_from_name("tzst", -1);
    TEST_ASSERT_EQUAL(ARCHIVE_TAR, p.archive);
    TEST_ASSERT_EQUAL(FORMAT_ZSTD, p.codec);
}

void test_name_plain_tar(void) {
    CompressionPipeline p = pipeline_from_name("tar", -1);
    TEST_ASSERT_EQUAL(ARCHIVE_TAR, p.archive);
    TEST_ASSERT_EQUAL(FORMAT_UNKNOWN, p.codec);
}

void test_name_zip(void) {
    CompressionPipeline p = pipeline_from_name("zip", -1);
    TEST_ASSERT_EQUAL(ARCHIVE_ZIP, p.archive);
    TEST_ASSERT_EQUAL(FORMAT_UNKNOWN, p.codec);
}

void test_name_standalone_codec(void) {
    CompressionPipeline p = pipeline_from_name("gzip", -1);
    TEST_ASSERT_EQUAL(ARCHIVE_NONE, p.archive);
    TEST_ASSERT_EQUAL(FORMAT_GZIP, p.codec);
}

void test_name_unknown(void) {
    CompressionPipeline p = pipeline_from_name("nonsense", -1);
    TEST_ASSERT_EQUAL(ARCHIVE_NONE, p.archive);
    TEST_ASSERT_EQUAL(FORMAT_UNKNOWN, p.codec);
}

// ---- archive_id_from_format ----

void test_archive_id_from_format(void) {
    TEST_ASSERT_EQUAL(ARCHIVE_TAR, archive_id_from_format(FORMAT_TAR));
    TEST_ASSERT_EQUAL(ARCHIVE_ZIP, archive_id_from_format(FORMAT_ZIP));
    TEST_ASSERT_EQUAL(ARCHIVE_NONE, archive_id_from_format(FORMAT_GZIP));
    TEST_ASSERT_EQUAL(ARCHIVE_NONE, archive_id_from_format(FORMAT_UNKNOWN));
}

// ---- pipeline_display_name ----

void test_display_name_combined(void) {
    CompressionPipeline p = {ARCHIVE_TAR, FORMAT_GZIP, -1};
    char buf[32];
    pipeline_display_name(&p, buf, sizeof(buf));
    TEST_ASSERT_EQUAL_STRING("tar.gz", buf);
}

void test_display_name_plain_tar(void) {
    CompressionPipeline p = {ARCHIVE_TAR, FORMAT_UNKNOWN, -1};
    char buf[32];
    pipeline_display_name(&p, buf, sizeof(buf));
    TEST_ASSERT_EQUAL_STRING("tar", buf);
}

void test_display_name_standalone(void) {
    CompressionPipeline p = {ARCHIVE_NONE, FORMAT_ZSTD, -1};
    char buf[32];
    pipeline_display_name(&p, buf, sizeof(buf));
    TEST_ASSERT_EQUAL_STRING("zstd", buf);
}

// ---- pipeline round-trip: name -> display name ----

void test_name_roundtrip(void) {
    const char *names[] = {"tar.gz", "tar.bz2", "tar.xz", "tar.zst", "tar.lz4"};
    for (size_t i = 0; i < sizeof(names) / sizeof(names[0]); i++) {
        CompressionPipeline p = pipeline_from_name(names[i], -1);
        char buf[32];
        pipeline_display_name(&p, buf, sizeof(buf));
        TEST_ASSERT_EQUAL_STRING(names[i], buf);
    }
}

// ---- pipeline_is_valid ----

void test_valid_tar_with_codec(void) {
    CompressionPipeline p = {ARCHIVE_TAR, FORMAT_GZIP, -1};
    TEST_ASSERT_TRUE(pipeline_is_valid(&p));
}

void test_valid_plain_tar(void) {
    CompressionPipeline p = {ARCHIVE_TAR, FORMAT_UNKNOWN, -1};
    TEST_ASSERT_TRUE(pipeline_is_valid(&p));
}

void test_valid_zip(void) {
    CompressionPipeline p = {ARCHIVE_ZIP, FORMAT_UNKNOWN, -1};
    TEST_ASSERT_TRUE(pipeline_is_valid(&p));
}

void test_valid_standalone_codec(void) {
    CompressionPipeline p = {ARCHIVE_NONE, FORMAT_ZSTD, -1};
    TEST_ASSERT_TRUE(pipeline_is_valid(&p));
}

void test_invalid_empty_pipeline(void) {
    CompressionPipeline p = {ARCHIVE_NONE, FORMAT_UNKNOWN, -1};
    TEST_ASSERT_FALSE(pipeline_is_valid(&p));
}

void test_invalid_zip_with_codec(void) {
    // ZIP compresses internally; an external codec stage is not allowed.
    CompressionPipeline p = {ARCHIVE_ZIP, FORMAT_GZIP, -1};
    TEST_ASSERT_FALSE(pipeline_is_valid(&p));
}

void test_invalid_unresolvable_codec(void) {
    // FORMAT_ZIP is not a standalone codec, so it cannot be a codec stage.
    CompressionPipeline p = {ARCHIVE_TAR, FORMAT_ZIP, -1};
    TEST_ASSERT_FALSE(pipeline_is_valid(&p));
}

void test_invalid_null(void) {
    TEST_ASSERT_FALSE(pipeline_is_valid(NULL));
}
