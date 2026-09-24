#ifndef LEVELS_H
#define LEVELS_H

// The compression levels a codec or container accepts, besides -1 (the
// library's default), which every one of them accepts
typedef struct {
  int min;
  int max; // {-1, -1}: no levels, so only the default
} LevelRange;

#define LEVELS_NONE {-1, -1}
#define LEVELS_ZLIB {0, 9} // zlib, gzip, and zip's deflate
#define LEVELS_BZIP2 {1, 9}
#define LEVELS_LZMA {0, 9}
#define LEVELS_ZSTD {1, 22} // 1 to ZSTD_maxCLevel(); test_zstd.c checks it
#define LEVELS_LZ4 {0, 12}  // 0 to LZ4HC_CLEVEL_MAX; test_lz4.c checks it

static inline int level_range_is_empty(LevelRange r) { return r.min < 0; }

#endif // LEVELS_H
