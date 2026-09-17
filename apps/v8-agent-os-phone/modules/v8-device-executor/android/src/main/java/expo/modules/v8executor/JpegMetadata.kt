package expo.modules.v8executor

import java.io.ByteArrayOutputStream

/** Strip encoder APP metadata after pixels were explicitly converted to sRGB. */
object JpegMetadata {
  fun strip(bytes: ByteArray): ByteArray {
    require(bytes.size >= 4 && bytes[0] == 0xff.toByte() && bytes[1] == 0xd8.toByte() &&
      bytes[bytes.lastIndex - 1] == 0xff.toByte() && bytes.last() == 0xd9.toByte()) { "jpeg_encoding_invalid" }
    val out = ByteArrayOutputStream(bytes.size)
    out.write(bytes, 0, 2)
    var offset = 2
    while (offset < bytes.size) {
      val start = offset
      require(bytes[offset].toInt() and 255 == 255) { "jpeg_encoding_invalid" }
      while (offset < bytes.size && bytes[offset].toInt() and 255 == 255) offset++
      require(offset + 2 < bytes.size) { "jpeg_encoding_invalid" }
      val marker = bytes[offset++].toInt() and 255
      val length = ((bytes[offset].toInt() and 255) shl 8) or (bytes[offset + 1].toInt() and 255)
      require(length >= 2 && offset + length <= bytes.size) { "jpeg_encoding_invalid" }
      offset += length
      if (marker == 0xda) {
        // Entropy-coded pixels are copied exactly, never scanned as metadata.
        out.write(bytes, start, bytes.size - start)
        return out.toByteArray()
      }
      if (marker !in 0xe1..0xef && marker != 0xfe) out.write(bytes, start, offset - start)
    }
    error("jpeg_encoding_invalid")
  }
}
