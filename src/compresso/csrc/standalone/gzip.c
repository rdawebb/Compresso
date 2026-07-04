#define PY_SSIZE_T_CLEAN
#include "../common.h"
#include "../standalone.h"
#include <Python.h>
#include <stdio.h>
#include <string.h>
#include <zlib.h>

#define GZIP_CHUNK 65536

// GZIP header structure (RFC 1952)
typedef struct {
  uint8_t magic[2]; // 0x1f, 0x8b
  uint8_t method;   // 0x08 for DEFLATE
  uint8_t flags;    // FLG
  uint32_t mtime;   // Modification time
  uint8_t xfl;      // Extra flags
  uint8_t os;       // Operating system
} __attribute__((packed)) GzipHeader;

// GZIP flags
#define FTEXT 0x01
#define FHCRC 0x02
#define FEXTRA 0x04
#define FNAME 0x08
#define FCOMMENT 0x10

// Write little-endian uint32
static void write_le32(uint8_t *buf, uint32_t val) {
  buf[0] = val & 0xFF;
  buf[1] = (val >> 8) & 0xFF;
  buf[2] = (val >> 16) & 0xFF;
  buf[3] = (val >> 24) & 0xFF;
}

// Read little-endian uint32
static uint32_t read_le32(const uint8_t *buf) {
  return (uint32_t)buf[0] | ((uint32_t)buf[1] << 8) | ((uint32_t)buf[2] << 16) |
         ((uint32_t)buf[3] << 24);
}

static int gzip_compress_file(const char *input_path, const char *output_path,
                              int level) {
  FILE *input = fopen(input_path, "rb");
  if (!input) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, input_path);
    return -1;
  }

  FILE *output = fopen(output_path, "wb");
  if (!output) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, output_path);
    fclose(input);
    return -1;
  }

  // Write GZIP header
  GzipHeader header;
  header.magic[0] = 0x1f;
  header.magic[1] = 0x8b;
  header.method = 0x08; // DEFLATE
  header.flags = 0;
  header.mtime = 0;
  header.xfl = 0;
  header.os = 0x03; // Unix

  if (fwrite(&header, sizeof(header), 1, output) != 1) {
    PyErr_SetString(PyExc_IOError, "Failed to write GZIP header");
    fclose(input);
    fclose(output);
    return -1;
  }

  // Initialise zlib for raw deflate
  z_stream strm;
  memset(&strm, 0, sizeof(strm));

  // Use raw deflate
  int zlevel = (level >= 0 && level <= 9) ? level : Z_DEFAULT_COMPRESSION;
  int ret = deflateInit2(&strm, zlevel, Z_DEFLATED,
                         -MAX_WBITS, // Negative for raw deflate
                         8, Z_DEFAULT_STRATEGY);
  if (ret != Z_OK) {
    PyErr_SetString(comp_BackendError, "Failed to initialize compression");
    fclose(input);
    fclose(output);
    return -1;
  }

  unsigned char in_buf[GZIP_CHUNK];
  unsigned char out_buf[GZIP_CHUNK];
  uint32_t crc = crc32(0L, Z_NULL, 0);
  uint32_t total_in = 0;
  int flush;

  Py_BEGIN_ALLOW_THREADS

      do {
    strm.avail_in = fread(in_buf, 1, GZIP_CHUNK, input);
    if (ferror(input)) {
      Py_BLOCK_THREADS deflateEnd(&strm);
      fclose(input);
      fclose(output);
      PyErr_SetString(PyExc_IOError, "Error reading input file");
      return -1;
    }

    total_in += strm.avail_in;
    crc = crc32(crc, in_buf, strm.avail_in);

    flush = feof(input) ? Z_FINISH : Z_NO_FLUSH;
    strm.next_in = in_buf;

    do {
      strm.avail_out = GZIP_CHUNK;
      strm.next_out = out_buf;

      ret = deflate(&strm, flush);
      if (ret == Z_STREAM_ERROR) {
        Py_BLOCK_THREADS deflateEnd(&strm);
        fclose(input);
        fclose(output);
        PyErr_SetString(comp_BackendError, "Compression stream error");
        return -1;
      }

      size_t have = GZIP_CHUNK - strm.avail_out;
      if (fwrite(out_buf, 1, have, output) != have || ferror(output)) {
        Py_BLOCK_THREADS deflateEnd(&strm);
        fclose(input);
        fclose(output);
        PyErr_SetString(PyExc_IOError, "Error writing output file");
        return -1;
      }
    } while (strm.avail_out == 0);
  }
  while (flush != Z_FINISH)
    ;

  Py_END_ALLOW_THREADS

      deflateEnd(&strm);

  // Write GZIP trailer (CRC32 + original size)
  uint8_t trailer[8];
  write_le32(trailer, crc);
  write_le32(trailer + 4, total_in);

  if (fwrite(trailer, 8, 1, output) != 1) {
    PyErr_SetString(PyExc_IOError, "Failed to write GZIP trailer");
    fclose(input);
    fclose(output);
    return -1;
  }

  fclose(input);
  fclose(output);
  return 0;
}

static int gzip_decompress_file(const char *input_path,
                                const char *output_path) {
  FILE *input = fopen(input_path, "rb");
  if (!input) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, input_path);
    return -1;
  }

  // Read and validate GZIP header
  GzipHeader header;
  if (fread(&header, sizeof(header), 1, input) != 1) {
    PyErr_SetString(comp_HeaderError, "Failed to read GZIP header");
    fclose(input);
    return -1;
  }

  if (header.magic[0] != 0x1f || header.magic[1] != 0x8b) {
    PyErr_SetString(comp_HeaderError, "Invalid GZIP magic number");
    fclose(input);
    return -1;
  }

  if (header.method != 0x08) {
    PyErr_SetString(comp_HeaderError, "Unsupported compression method");
    fclose(input);
    return -1;
  }

  // Skip optional fields
  if (header.flags & FEXTRA) {
    uint16_t xlen;
    if (fread(&xlen, 2, 1, input) != 1) {
      PyErr_SetString(comp_HeaderError, "Failed to read extra field length");
      fclose(input);
      return -1;
    }
    fseek(input, xlen, SEEK_CUR);
  }

  if (header.flags & FNAME) {
    // Skip original filename
    int c;
    while ((c = fgetc(input)) != 0 && c != EOF)
      ;
  }

  if (header.flags & FCOMMENT) {
    // Skip comment
    int c;
    while ((c = fgetc(input)) != 0 && c != EOF)
      ;
  }

  if (header.flags & FHCRC) {
    // Skip header CRC
    fseek(input, 2, SEEK_CUR);
  }

  FILE *output = fopen(output_path, "wb");
  if (!output) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, output_path);
    fclose(input);
    return -1;
  }

  // Initialise zlib for raw inflate
  z_stream strm;
  memset(&strm, 0, sizeof(strm));

  int ret = inflateInit2(&strm, -MAX_WBITS); // Raw inflate
  if (ret != Z_OK) {
    PyErr_SetString(comp_BackendError, "Failed to initialize decompression");
    fclose(input);
    fclose(output);
    return -1;
  }

  unsigned char in_buf[GZIP_CHUNK];
  unsigned char out_buf[GZIP_CHUNK];
  uint32_t crc = crc32(0L, Z_NULL, 0);
  uint32_t total_out = 0;

  Py_BEGIN_ALLOW_THREADS

      do {
    strm.avail_in = fread(in_buf, 1, GZIP_CHUNK, input);
    if (ferror(input)) {
      Py_BLOCK_THREADS inflateEnd(&strm);
      fclose(input);
      fclose(output);
      PyErr_SetString(PyExc_IOError, "Error reading input file");
      return -1;
    }

    if (strm.avail_in == 0)
      break;

    strm.next_in = in_buf;

    do {
      strm.avail_out = GZIP_CHUNK;
      strm.next_out = out_buf;

      ret = inflate(&strm, Z_NO_FLUSH);
      if (ret != Z_OK && ret != Z_STREAM_END) {
        Py_BLOCK_THREADS inflateEnd(&strm);
        fclose(input);
        fclose(output);
        PyErr_SetString(comp_BackendError, "Decompression error");
        return -1;
      }

      size_t have = GZIP_CHUNK - strm.avail_out;
      total_out += have;
      crc = crc32(crc, out_buf, have);

      if (fwrite(out_buf, 1, have, output) != have || ferror(output)) {
        Py_BLOCK_THREADS inflateEnd(&strm);
        fclose(input);
        fclose(output);
        PyErr_SetString(PyExc_IOError, "Error writing output file");
        return -1;
      }
    } while (strm.avail_out == 0);
  }
  while (ret != Z_STREAM_END)
    ;

  Py_END_ALLOW_THREADS

      // After the deflate stream ends, inflate leaves the 8-byte trailer
      // (CRC32 + ISIZE) unconsumed in the input buffer. Capture it from there,
      // topping up from the file if it was split across a read boundary.
      uint8_t trailer[8];
  size_t trailer_have = 0;
  if (strm.avail_in > 0) {
    trailer_have = strm.avail_in < 8 ? strm.avail_in : 8;
    memcpy(trailer, strm.next_in, trailer_have);
  }

  inflateEnd(&strm);

  if (ret != Z_STREAM_END) {
    PyErr_SetString(comp_BackendError, "Truncated or incomplete GZIP stream");
    fclose(input);
    fclose(output);
    return -1;
  }

  if (trailer_have < 8) {
    trailer_have += fread(trailer + trailer_have, 1, 8 - trailer_have, input);
  }

  if (trailer_have != 8) {
    PyErr_SetString(comp_HeaderError, "Missing or truncated GZIP trailer");
    fclose(input);
    fclose(output);
    return -1;
  }

  uint32_t expected_crc = read_le32(trailer);
  uint32_t expected_size = read_le32(trailer + 4);

  if (crc != expected_crc) {
    PyErr_Format(comp_BackendError, "CRC mismatch: expected %08x, got %08x",
                 expected_crc, crc);
    fclose(input);
    fclose(output);
    return -1;
  }

  if (total_out != expected_size) {
    PyErr_Format(comp_BackendError, "Size mismatch: expected %u, got %u",
                 expected_size, total_out);
    fclose(input);
    fclose(output);
    return -1;
  }

  fclose(input);
  fclose(output);
  return 0;
}

static char *gzip_get_original_name(const char *compressed_path) {
  FILE *f = fopen(compressed_path, "rb");
  if (!f)
    return NULL;

  GzipHeader header;
  if (fread(&header, sizeof(header), 1, f) != 1) {
    fclose(f);
    return NULL;
  }

  if (!(header.flags & FNAME)) {
    fclose(f);
    return NULL;
  }

  // Skip extra field if present
  if (header.flags & FEXTRA) {
    uint16_t xlen;
    if (fread(&xlen, 2, 1, f) != 1) {
      fclose(f);
      return NULL;
    }
    fseek(f, xlen, SEEK_CUR);
  }

  // Read filename
  char name_buf[256];
  size_t i = 0;
  int c;
  while ((c = fgetc(f)) != 0 && c != EOF && i < sizeof(name_buf) - 1) {
    name_buf[i++] = c;
  }
  name_buf[i] = '\0';

  fclose(f);

  if (i > 0) {
    return strdup(name_buf);
  }
  return NULL;
}

static int gzip_is_format(const unsigned char *magic, size_t size) {
  return (size >= 2 && magic[0] == 0x1f && magic[1] == 0x8b);
}

static const StandaloneFormat gzip_format = {
    .name = "gzip",
    .extension = ".gz",
    .compress_file = gzip_compress_file,
    .decompress_file = gzip_decompress_file,
    .get_original_name = gzip_get_original_name,
    .is_format = gzip_is_format,
};

const StandaloneFormat *get_gzip_format(void) { return &gzip_format; }
