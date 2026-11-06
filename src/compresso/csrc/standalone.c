#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "standalone.h"
#include "common.h"
#include <lz4frame.h>
#include <lzma.h>
#include <snappy-c.h>
#include <stdio.h>
#include <string.h>
#include <zstd.h>

const FormatDesc *find_standalone_format(Format format) {
  const FormatDesc *desc = format_by_id(format);
  return desc && desc->kind == KIND_CONTAINER ? desc : NULL;
}

int standalone_compress(const FormatDesc *fmt, const char *input_path,
                        const OutputTarget *out, int level, CoreContext *ctx) {
  CodecParams params = fmt->enc;
  params.level = level;
  params.threads = ctx ? ctx->threads : 0;
  char failure[64];
  snprintf(failure, sizeof(failure), "%s compression failed", fmt->name);
  return codec_run_source(fmt->engine(), &params, 0, input_path, NULL, out, ctx,
                          failure);
}

int standalone_decompress(const FormatDesc *fmt, const char *input_path,
                          FILE *input, const OutputTarget *out,
                          CoreContext *ctx) {
  char failure[64];
  snprintf(failure, sizeof(failure), "%s decompression failed", fmt->name);
  CodecParams params = fmt->dec;
  params.threads = ctx ? ctx->threads : 0;
  return codec_run_source(fmt->engine(), &params, 1, input_path, input, out,
                          ctx, failure);
}

// GZIP header (RFC 1952): a fixed 10-byte record, addressed by byte offset
// rather than declared as a struct so that padding can't reach the file
#define GZIP_HEADER_SIZE 10
#define GZIP_OFF_FLAGS 3

#define FEXTRA 0x04
#define FNAME 0x08

char *gzip_original_name(const char *compressed_path) {
  FILE *f = fs_fopen(compressed_path, "rb");
  if (!f)
    return NULL;

  uint8_t header[GZIP_HEADER_SIZE];
  if (fread(header, sizeof(header), 1, f) != 1) {
    fclose(f);
    return NULL;
  }

  uint8_t flags = header[GZIP_OFF_FLAGS];

  if (header[0] != 0x1f || header[1] != 0x8b || !(flags & FNAME)) {
    fclose(f);
    return NULL;
  }

  if (flags & FEXTRA) {
    uint8_t xlen_buf[2];
    if (fread(xlen_buf, sizeof(xlen_buf), 1, f) != 1 ||
        fseek(f, (long)get_le(xlen_buf, 2), SEEK_CUR) != 0) {
      fclose(f);
      return NULL;
    }
  }

  // gzip's own limit (MAX_PATH_LEN); a name cut short by EOF (which also
  // catches an xlen running past the end) or by the limit is ignored
  char name_buf[1024];
  size_t i = 0;
  int c;
  while ((c = fgetc(f)) > 0 && i < sizeof(name_buf) - 1) {
    name_buf[i++] = (char)c;
  }
  name_buf[i] = '\0';

  fclose(f);

  return c == 0 && i > 0 ? strdup(name_buf) : NULL;
}

static int xz_content_size(FILE *f, uint64_t *size) {
  int64_t file_size = fs_stream_size(f);
  lzma_stream strm = LZMA_STREAM_INIT;
  lzma_index *index = NULL;
  if (file_size < 0 || lzma_file_info_decoder(&strm, &index, UINT64_MAX,
                                              (uint64_t)file_size) != LZMA_OK) {
    return -1;
  }

  uint8_t buf[BUFSIZ];
  lzma_ret ret = LZMA_OK;
  while (ret == LZMA_OK) {
    if (strm.avail_in == 0) {
      strm.next_in = buf;
      strm.avail_in = fread(buf, 1, sizeof(buf), f);
    }
    ret = lzma_code(&strm, LZMA_RUN);
    if (ret == LZMA_SEEK_NEEDED) {
      strm.avail_in = 0;
      ret = fs_fseek(f, (int64_t)strm.seek_pos, SEEK_SET) == 0 ? LZMA_OK
                                                               : LZMA_DATA_ERROR;
    }
  }

  if (ret == LZMA_STREAM_END) {
    *size = lzma_index_uncompressed_size(index);
  }
  lzma_index_end(index, NULL);
  lzma_end(&strm);
  return ret == LZMA_STREAM_END ? 0 : -1;
}

// ZSTD_getFrameHeader needs static linking, so frames are walked by RFC 8878
#define ZSTD_HEADER_MAX 18 // ZSTD_FRAMEHEADERSIZE_MAX, also static-only
#define ZSTD_BLOCK_HEADER 3
#define ZSTD_SKIPPABLE_HEADER 8
#define ZSTD_CHECKSUM 4

#define ZSTD_FHD_CHECKSUM 0x04
#define ZSTD_FHD_SINGLE_SEGMENT 0x20
#define ZSTD_BLOCK_RLE 1

// The offset past the blocks at `pos`, or -1
static int64_t zstd_skip_blocks(FILE *f, int64_t pos, int64_t end) {
  for (;;) {
    unsigned char header[ZSTD_BLOCK_HEADER];
    if (fs_fseek(f, pos, SEEK_SET) != 0 ||
        fread(header, sizeof(header), 1, f) != 1) {
      return -1;
    }

    // Bit 0 ends the frame, 1-2 are the type, and the rest the size; an RLE
    // block stores just the byte it repeats
    uint32_t block = (uint32_t)get_le(header, ZSTD_BLOCK_HEADER);
    pos += ZSTD_BLOCK_HEADER;
    pos += ((block >> 1) & 3) == ZSTD_BLOCK_RLE ? 1 : block >> 3;
    if (pos > end) {
      return -1;
    }
    if (block & 1) {
      return pos;
    }
  }
}

// Adds the content size of the frame whose first `got` bytes are `header` to
// `size`, returning the offset past it, or -1
typedef int64_t (*FrameWalker)(FILE *f, const unsigned char *header,
                               size_t got, int64_t pos, int64_t end,
                               uint64_t *size);

static int64_t zstd_frame(FILE *f, const unsigned char *header, size_t got,
                          int64_t pos, int64_t end, uint64_t *size) {
  static const uint8_t dict_id_size[] = {0, 1, 2, 4};
  static const uint8_t content_size_size[] = {0, 2, 4, 8};

  // Both failures sit at the top of the range, and reject a bad magic
  unsigned long long frame = ZSTD_getFrameContentSize(header, got);
  if (frame >= ZSTD_CONTENTSIZE_ERROR || frame > UINT64_MAX - *size) {
    return -1;
  }
  *size += frame;

  // Magic and descriptor, then the window byte unless the frame's single
  // segment, whose content size is never absent
  uint8_t fhd = header[4];
  int single = (fhd & ZSTD_FHD_SINGLE_SEGMENT) != 0;
  int content_field = content_size_size[fhd >> 6];
  int header_size = 5 + !single + dict_id_size[fhd & 3] +
                    (single && !content_field ? 1 : content_field);

  pos = zstd_skip_blocks(f, pos + header_size, end);
  return pos >= 0 && (fhd & ZSTD_FHD_CHECKSUM) ? pos + ZSTD_CHECKSUM : pos;
}

#define LZ4_FLG_VERSION_MASK 0xC0
#define LZ4_FLG_VERSION 0x40
#define LZ4_FLG_BLOCK_CHECKSUM 0x10
#define LZ4_FLG_CONTENT_SIZE 0x08
#define LZ4_FLG_CONTENT_CHECKSUM 0x04
#define LZ4_FLG_DICT_ID 0x01
#define LZ4_BLOCK_SIZE_MASK 0x7FFFFFFF // The top bit marks a stored block

static int64_t lz4_frame(FILE *f, const unsigned char *header, size_t got,
                         int64_t pos, int64_t end, uint64_t *size) {
  // The minimum is magic, FLG, BD and the header checksum; the content size
  // and dictionary ID sit between BD and the checksum
  uint8_t flg = header[4];
  size_t header_size =
      LZ4F_HEADER_SIZE_MIN + 8 + (flg & LZ4_FLG_DICT_ID ? 4 : 0);

  // A frame written from a stream, the lz4 CLI's default, states no size
  if (get_le(header, 4) != LZ4F_MAGICNUMBER ||
      (flg & LZ4_FLG_VERSION_MASK) != LZ4_FLG_VERSION ||
      !(flg & LZ4_FLG_CONTENT_SIZE) || got < header_size) {
    return -1;
  }
  uint64_t frame = get_le(header + 6, 8);
  if (frame > UINT64_MAX - *size) {
    return -1;
  }
  *size += frame;

  int64_t checksum = flg & LZ4_FLG_BLOCK_CHECKSUM ? LZ4F_BLOCK_CHECKSUM_SIZE : 0;
  pos += (int64_t)header_size;
  for (;;) {
    unsigned char block[LZ4F_BLOCK_HEADER_SIZE];
    if (fs_fseek(f, pos, SEEK_SET) != 0 ||
        fread(block, sizeof(block), 1, f) != 1) {
      return -1;
    }
    pos += LZ4F_BLOCK_HEADER_SIZE;

    // liblz4 ends the frame on a zero size, whatever the top bit
    int64_t len = (int64_t)(get_le(block, 4) & LZ4_BLOCK_SIZE_MASK);
    if (len == 0) {
      break;
    }
    pos += len + checksum;
    if (pos > end) {
      return -1;
    }
  }

  return flg & LZ4_FLG_CONTENT_CHECKSUM ? pos + LZ4F_CONTENT_CHECKSUM_SIZE
                                        : pos;
}

// zstd and lz4 share skippable frames, so only the other frames differ
static int frames_content_size(FILE *f, FrameWalker frame, uint64_t *size) {
  int64_t end = fs_stream_size(f);
  int64_t pos = 0;
  *size = 0;

  while (pos >= 0 && pos < end) {
    unsigned char header[LZ4F_HEADER_SIZE_MAX]; // Over ZSTD_HEADER_MAX too
    if (fs_fseek(f, pos, SEEK_SET) != 0) {
      return -1;
    }
    size_t got = fread(header, 1, sizeof(header), f);
    if (got < ZSTD_SKIPPABLE_HEADER) {
      return -1;
    }

    if ((get_le(header, 4) & ZSTD_MAGIC_SKIPPABLE_MASK) ==
        ZSTD_MAGIC_SKIPPABLE_START) {
      pos += ZSTD_SKIPPABLE_HEADER + (int64_t)get_le(header + 4, 4);
    } else {
      pos = frame(f, header, got, pos, end, size);
    }
  }

  return pos == end && end > 0 ? 0 : -1;
}

// Snappy's framing format, as codec/snappy.c reads it
#define SNAPPY_COMPRESSED 0x00
#define SNAPPY_UNCOMPRESSED 0x01
#define SNAPPY_SKIPPABLE 0x80 // 0x80-0xFE, then the stream identifier at 0xFF
#define SNAPPY_CHUNK 65536    // The most a chunk may decode to
#define SNAPPY_CHECKSUM 4
#define SNAPPY_VARINT_MAX 3 // Enough to state SNAPPY_CHUNK

// Every snappy block opens with its decoded length, so no chunk is decoded
static int snappy_content_size(FILE *f, uint64_t *size) {
  int64_t end = fs_stream_size(f);
  int64_t pos = 0;
  *size = 0;

  while (pos < end) {
    // Type and LE24 length, then a data chunk's checksum and the start of its
    // body
    unsigned char chunk[4 + SNAPPY_CHECKSUM + SNAPPY_VARINT_MAX];
    if (fs_fseek(f, pos, SEEK_SET) != 0) {
      return -1;
    }
    size_t got = fread(chunk, 1, sizeof(chunk), f);
    if (got < 4) {
      return -1;
    }
    size_t len = (size_t)get_le(chunk + 1, 3);
    size_t body = got - 4 < len ? got - 4 : len;
    pos += 4 + (int64_t)len;

    size_t decoded = 0;
    if (chunk[0] == SNAPPY_COMPRESSED) {
      if (body < SNAPPY_CHECKSUM ||
          snappy_uncompressed_length((const char *)chunk + 4 + SNAPPY_CHECKSUM,
                                     body - SNAPPY_CHECKSUM,
                                     &decoded) != SNAPPY_OK) {
        return -1;
      }
    } else if (chunk[0] == SNAPPY_UNCOMPRESSED) {
      if (len < SNAPPY_CHECKSUM) {
        return -1;
      }
      decoded = len - SNAPPY_CHECKSUM;
    } else if (chunk[0] < SNAPPY_SKIPPABLE) {
      return -1; // Reserved unskippable
    }

    if (decoded > SNAPPY_CHUNK) {
      return -1;
    }
    *size += decoded;
  }

  return pos == end && end > 0 ? 0 : -1;
}

int standalone_content_size(FILE *f, Format format, uint64_t *size) {
  if (fs_fseek(f, 0, SEEK_SET) != 0) {
    return -1;
  }
  switch (format) {
  case FORMAT_XZ:
    return xz_content_size(f, size);
  case FORMAT_ZSTD:
    return frames_content_size(f, zstd_frame, size);
  case FORMAT_LZ4:
    return frames_content_size(f, lz4_frame, size);
  case FORMAT_SNAPPY:
    return snappy_content_size(f, size);
  default:
    return -1;
  }
}
