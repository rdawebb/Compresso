// Makes Unity define TEST_RANGE; the runner generator expands each one
#define UNITY_SUPPORT_TEST_CASES

#include "archives.h"
#include "fsutil.h"
#include "unity.h"
#include <stdio.h>

#define COUNT(array) (sizeof(array) / sizeof((array)[0]))

static const struct {
  const char *name;
  int level;
  CompressionPipeline expected;
} FROM_NAME[] = {
    {"tar.gz", 5, {ARCHIVE_TAR, FORMAT_GZIP, 5}},
    {"tzst", -1, {ARCHIVE_TAR, FORMAT_ZSTD, -1}}, // Combined shorthand
    {"tar", -1, {ARCHIVE_TAR, FORMAT_UNKNOWN, -1}},
    {"zip", -1, {ARCHIVE_ZIP, FORMAT_UNKNOWN, -1}},
    {"gzip", -1, {ARCHIVE_NONE, FORMAT_GZIP, -1}},
    {"nonsense", -1, {ARCHIVE_NONE, FORMAT_UNKNOWN, -1}},
};

static const struct {
  CompressionPipeline pipeline;
  const char *expected;
} DISPLAY_NAMES[] = {
    {{ARCHIVE_TAR, FORMAT_GZIP, -1}, "tar.gz"},
    {{ARCHIVE_TAR, FORMAT_UNKNOWN, -1}, "tar"},
    {{ARCHIVE_NONE, FORMAT_ZSTD, -1}, "zstd"},
};

static const char *const COMBINED_NAMES[] = {"tar.gz", "tar.bz2", "tar.xz",
                                             "tar.zst", "tar.lz4"};

static const struct {
  const char *label;
  CompressionPipeline pipeline;
  int valid;
} VALIDITY[] = {
    {"tar with a codec", {ARCHIVE_TAR, FORMAT_GZIP, -1}, 1},
    {"plain tar", {ARCHIVE_TAR, FORMAT_UNKNOWN, -1}, 1},
    {"zip", {ARCHIVE_ZIP, FORMAT_UNKNOWN, -1}, 1},
    {"standalone codec", {ARCHIVE_NONE, FORMAT_ZSTD, -1}, 1},
    {"empty pipeline", {ARCHIVE_NONE, FORMAT_UNKNOWN, -1}, 0},
    // ZIP compresses internally, so an external codec stage is not allowed
    {"zip with a codec", {ARCHIVE_ZIP, FORMAT_GZIP, -1}, 0},
    // FORMAT_ZIP is not a standalone codec, so it cannot be a codec stage
    {"unresolvable codec", {ARCHIVE_TAR, FORMAT_ZIP, -1}, 0},
};

// Each TEST_RANGE below must span exactly its table's rows
_Static_assert(COUNT(FROM_NAME) == 6, "update TEST_RANGE");
_Static_assert(COUNT(DISPLAY_NAMES) == 3, "update TEST_RANGE");
_Static_assert(COUNT(COMBINED_NAMES) == 5, "update TEST_RANGE");
_Static_assert(COUNT(VALIDITY) == 7, "update TEST_RANGE");

void setUp(void) {}
void tearDown(void) {
  remove("tmp_pipe.tar.gz/inner");
  remove("tmp_pipe.tar.gz");
}

TEST_RANGE([ 0, 5, 1 ])
void test_pipeline_from_name(int index) {
  CompressionPipeline p =
      pipeline_from_name(FROM_NAME[index].name, FROM_NAME[index].level);

  TEST_ASSERT_EQUAL_MESSAGE(FROM_NAME[index].expected.archive, p.archive,
                            FROM_NAME[index].name);
  TEST_ASSERT_EQUAL_MESSAGE(FROM_NAME[index].expected.codec, p.codec,
                            FROM_NAME[index].name);
  TEST_ASSERT_EQUAL_INT_MESSAGE(FROM_NAME[index].expected.compression_level,
                                p.compression_level, FROM_NAME[index].name);
}

void test_archive_id_from_format(void) {
  TEST_ASSERT_EQUAL(ARCHIVE_TAR, archive_id_from_format(FORMAT_TAR));
  TEST_ASSERT_EQUAL(ARCHIVE_ZIP, archive_id_from_format(FORMAT_ZIP));
  TEST_ASSERT_EQUAL(ARCHIVE_NONE, archive_id_from_format(FORMAT_GZIP));
  TEST_ASSERT_EQUAL(ARCHIVE_NONE, archive_id_from_format(FORMAT_UNKNOWN));
}

TEST_RANGE([ 0, 2, 1 ])
void test_pipeline_display_name(int index) {
  char buf[32];
  pipeline_display_name(&DISPLAY_NAMES[index].pipeline, buf, sizeof(buf));

  TEST_ASSERT_EQUAL_STRING(DISPLAY_NAMES[index].expected, buf);
}

TEST_RANGE([ 0, 4, 1 ])
void test_combined_name_round_trips_through_display_name(int index) {
  CompressionPipeline p = pipeline_from_name(COMBINED_NAMES[index], -1);
  char buf[32];
  pipeline_display_name(&p, buf, sizeof(buf));

  TEST_ASSERT_EQUAL_STRING(COMBINED_NAMES[index], buf);
}

TEST_RANGE([ 0, 6, 1 ])
void test_pipeline_is_valid(int index) {
  TEST_ASSERT_EQUAL_INT_MESSAGE(
      VALIDITY[index].valid, pipeline_is_valid(&VALIDITY[index].pipeline) != 0,
      VALIDITY[index].label);
}

void test_null_pipeline_is_invalid(void) {
  TEST_ASSERT_FALSE(pipeline_is_valid(NULL));
}

// ---- detect_pipeline_from_path ----

// A full 10-byte header: detection reads nothing from a file under 4 bytes
static void write_gzip_magic(const char *path) {
  static const unsigned char header[10] = {0x1f, 0x8b, 0x08};
  FILE *f = fopen(path, "wb");
  TEST_ASSERT_NOT_NULL(f);
  fwrite(header, 1, sizeof(header), f);
  fclose(f);
}

void test_tar_shorthand_needs_it_in_the_file_name(void) {
  write_gzip_magic("tmp_pipe.tar.gz");

  CompressionPipeline p = detect_pipeline_from_path("tmp_pipe.tar.gz");
  TEST_ASSERT_EQUAL(ARCHIVE_TAR, p.archive);
  TEST_ASSERT_EQUAL(FORMAT_GZIP, p.codec);
}

void test_tar_shorthand_in_a_directory_name_is_ignored(void) {
  TEST_ASSERT_EQUAL_INT(0, fs_mkdir_p("tmp_pipe.tar.gz", 0755));
  write_gzip_magic("tmp_pipe.tar.gz/inner");

  CompressionPipeline p = detect_pipeline_from_path("tmp_pipe.tar.gz/inner");
  TEST_ASSERT_EQUAL(ARCHIVE_NONE, p.archive);
  TEST_ASSERT_EQUAL(FORMAT_GZIP, p.codec);
}
